#!/usr/bin/env python3
"""
tapo-webcam – periodically grabs still images from Tapo cameras, including models
without RTSP/ONVIF (battery cameras, doorbells, cameras paired to an H200/H500 hub).

Two connection types per camera:
  local – local TP-Link protocol (port 8800), like the app on the same Wi-Fi, via pytapo
  cloud – through the TP-Link cloud relay, like the app on the go, via ontapo
          (unofficial/experimental; no LAN/VPN required)

Commands:
  tapo_webcam.py discover -c config.toml      check cameras, list hub children
  tapo_webcam.py snap     -c config.toml      take and store one picture
  tapo_webcam.py serve    -c config.toml      service: interval captures + HTTP API
  tapo_webcam.py cloud-login -c config.toml   sign in to the Tapo account (for connection="cloud")
  tapo_webcam.py placeholders -c config.toml [--upload]
                                              render night/offline placeholder previews, optional SFTP test
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import shutil
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("tapo-webcam")
logging.getLogger("paramiko").setLevel(logging.WARNING)   # otherwise 3 INFO lines per upload


# --------------------------------------------------------------------------- #
# Language-dependent defaults for placeholder images
# --------------------------------------------------------------------------- #
TEXTS = {
    "en": {
        "night": "It's night\nNext picture at {next}",
        "offline": "Temporarily out of service\nAs of {date}, {time}",
        "tomorrow": "tomorrow",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "date": "%Y-%m-%d",
        "as_of": "as of",
    },
    "de": {
        "night": "Es ist Nacht\nNächstes Bild ab {next} Uhr",
        "offline": "Vorübergehend außer Betrieb\nStand: {date}, {time} Uhr",
        "tomorrow": "morgen",
        "weekdays": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "date": "%d.%m.%Y",
        "as_of": "Stand",
    },
}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class CameraConfig:
    name: str
    connection: str = "local"        # local = in the LAN (pytapo) | cloud = via the TP-Link cloud relay (ontapo)
    host: str = ""                   # local only: IP of the camera or hub
    password: str = ""               # local only: password of the TP-Link/Tapo account
    cloud_device: str | None = None  # cloud only: camera name from the app, or device_id
    cloud_seconds: float = 3.0       # cloud only: how long the stream is opened (shorter = saves battery)
    cloud_retries: int = 3           # cloud only: retries while the camera is not awake yet (relay 404)
    cloud_retry_wait: float = 5.0    # cloud only: seconds between retries
    user: str = "admin"
    child_id: str | None = None      # hub cameras only: device_id of the child device
    mode: str = "auto"               # auto | snapshot | stream
    quality: str = "HD"              # for mode=stream: HD | VGA
    interval: int = 300              # seconds between captures, 0 = off
    timeout: int = 20                # seconds per capture attempt
    cache_seconds: int = 15          # a fresh picture is reused for this long
    jpeg_quality: int = 3            # ffmpeg -q:v (2 = best, 31 = worst)
    # time window for interval captures, e.g. "08:00-19:00" (empty = around the clock)
    active_hours: str = ""
    active_days: str = ""            # e.g. "Mon-Fri" or "Sat,Sun" (empty = every day)
    # privacy: shrink/blur the picture right after download
    max_width: int = 0               # e.g. 1280; 0 = original size
    blur: float = 0.0                # Gaussian blur radius in pixels (after resizing), 0 = off
    output_quality: int = 80         # JPEG quality of the stored picture (1–95)
    face_blur: bool = False          # detect faces and make them unrecognisable (OpenCV YuNet)
    face_threshold: float = 0.6      # detection threshold 0–1: lower = finds more, but more false positives
    face_margin: float = 0.4         # extra margin pixelated around each face (fraction of face size)
    # cloud only: battery protection. The level is read after every picture (camera is awake anyway).
    battery_min: int = 0             # below this level (%) interval captures pause; 0 = off
    battery_pause_hours: float = 6.0 # pause length; then one picture + new reading, pause again if still low
    # Placeholder picture instead of a camera picture: at night (outside active_hours/active_days) and
    # "out of service" (battery pause or camera/cloud unreachable – the reason is deliberately not shown).
    # Stored once per pause/outage as latest.jpg and uploaded (not archived).
    placeholders: bool = False
    language: str = "en"             # language of the default placeholder texts and dates: en | de
    night_image: str = ""            # own background picture (JPEG/PNG); empty = dark background with moon
    night_text: str | None = None    # None = default text for `language`
    offline_image: str = ""          # own background picture; empty = grey background with crossed-out camera
    offline_text: str | None = None  # None = default text for `language`
    offline_after: int = 1           # after this many failed interval captures in a row
    upload: bool = True              # send to [upload] (if configured)
    upload_name: str = ""            # file name on the server; empty = [upload] name

    def __post_init__(self):
        t = TEXTS.get(self.language, TEXTS["en"])
        if self.night_text is None:
            self.night_text = t["night"]
        if self.offline_text is None:
            self.offline_text = t["offline"]


@dataclass
class UploadConfig:
    host: str
    user: str
    port: int = 22
    password: str = ""               # better via environment variable (password_env, default SFTP_PASSWORD)
    key: str = ""                    # private SSH key (OpenSSH format), e.g. /var/lib/tapo-webcam/.ssh/sftp_ed25519
    dir: str = "."                   # target directory on the server
    name: str = "{camera}.jpg"       # file name on the server ({camera} = camera name)
    known_hosts: str = ""            # empty = known_hosts_sftp next to cloud_session_file
    timeout: float = 30.0


@dataclass
class Config:
    output_dir: Path = Path("./images")
    retention_days: int = 14         # 0 = never delete
    keep_history: bool = True        # False = only maintain latest.jpg
    listen: str = "0.0.0.0"
    port: int = 8099
    token: str | None = None         # optional API token (?token=… or header X-Token)
    cloud_session_file: str = "cloud_session.json"   # created by `cloud-login`
    cameras: list[CameraConfig] = field(default_factory=list)
    upload: UploadConfig | None = None


# weekday abbreviations, English and German (0 = Monday)
_DAYS = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6,
         "di": 1, "mi": 2, "do": 3, "so": 6}


def parse_hours(spec: str, cam: str = "") -> tuple[int, int] | None:
    """"08:00-19:00" -> (480, 1140) minutes since midnight; may span midnight ("22:00-06:00")."""
    if not spec:
        return None
    try:
        a, b = [x.strip() for x in spec.split("-")]
        to_min = lambda t: int(t.split(":")[0]) * 60 + int((t.split(":") + ["0"])[1])
        start, end = to_min(a), to_min(b)
        assert 0 <= start < 1440 and 0 <= end <= 1440 and start != end
        return start, end
    except Exception:  # noqa: BLE001
        raise SystemExit(f"Camera '{cam}': invalid active_hours '{spec}', expected e.g. \"08:00-19:00\".")


def parse_days(spec: str, cam: str = "") -> set[int] | None:
    """"Mon-Fri" / "Sat,Sun" / "Mo,Mi,Fr" -> {0,…} (0 = Monday). English and German abbreviations."""
    if not spec:
        return None
    out: set[int] = set()
    try:
        for part in spec.lower().replace(" ", "").split(","):
            if "-" in part:
                a, b = part.split("-")
                i, j = _DAYS[a[:2]], _DAYS[b[:2]]
                out.update(range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1)))
            else:
                out.add(_DAYS[part[:2]])
        return out
    except (KeyError, ValueError):
        raise SystemExit(f"Camera '{cam}': invalid active_days '{spec}', expected e.g. \"Mon-Fri\" or \"Sat,Sun\".")


def is_active(c: "CameraConfig", now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now()
    days = parse_days(c.active_days)
    if days is not None and now.weekday() not in days:
        return False
    hours = parse_hours(c.active_hours)
    if hours is None:
        return True
    m = now.hour * 60 + now.minute
    start, end = hours
    return start <= m < end if start < end else (m >= start or m < end)


def next_active(c: "CameraConfig", now: dt.datetime | None = None) -> dt.datetime | None:
    """Start of the next capture window (to the minute, after `now`); None = always/never active."""
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)
    t = now + dt.timedelta(minutes=1)
    seen_inactive = not is_active(c, now)       # inside a window: wait for its end first
    for _ in range(8 * 1440):
        if is_active(c, t):
            if seen_inactive:
                return t
        else:
            seen_inactive = True
        t += dt.timedelta(minutes=1)
    return None


def next_capture_after(c: "CameraConfig", t: dt.datetime) -> dt.datetime:
    """t, or – if t is outside the time window – the start of the next window."""
    return t if is_active(c, t) else (next_active(c, t) or t)


def fmt_when(t: dt.datetime | None, now: dt.datetime | None = None, language: str = "en") -> str:
    """'09:00', 'tomorrow 09:00' or 'Mon 09:00' (in the given language)."""
    if t is None:
        return "?"
    tx = TEXTS.get(language, TEXTS["en"])
    now = now or dt.datetime.now()
    days = (t.date() - now.date()).days
    if days == 0:
        return t.strftime("%H:%M")
    if days == 1:
        return f"{tx['tomorrow']} {t:%H:%M}"
    return f"{tx['weekdays'][t.weekday()]} {t:%H:%M}"


def fmt_date(t: dt.datetime, language: str = "en") -> str:
    return t.strftime(TEXTS.get(language, TEXTS["en"])["date"])


_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in _FONT_DIRS:
        try:
            return ImageFont.truetype(os.path.join(d, name), size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                           # Pillow < 10.1
        return ImageFont.load_default()


def render_placeholder(c: "CameraConfig", kind: str, values: dict, size: tuple[int, int] | None = None) -> bytes:
    """Placeholder picture 'night' or 'offline' as JPEG."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    night = kind == "night"
    path = c.night_image if night else c.offline_image
    text = c.night_text if night else c.offline_text
    try:
        text = text.format(**values)
    except (KeyError, IndexError, ValueError):
        pass
    if path:
        im = Image.open(path).convert("RGB")
        if c.max_width and im.width > c.max_width:
            im = im.resize((c.max_width, round(im.height * c.max_width / im.width)), Image.LANCZOS)
        w, h = im.size
    else:
        w, h = size or (c.max_width or 1280, round((c.max_width or 1280) * 9 / 16))
        bg = (22, 30, 46) if night else (44, 46, 50)
        im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    u = min(w, h * 16 / 9) / 100                 # unit: 1 % of the (16:9) width
    lines = [ln for ln in text.split("\n")] if text else []

    if not path:                                 # symbol above the text
        cx, cy = w / 2, h * (0.30 if lines else 0.5)
        if night:
            r = 7 * u
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(242, 228, 170))
            o = 0.55 * r
            d.ellipse((cx - r + o, cy - r - 0.25 * o, cx + r + o, cy + r - 0.25 * o), fill=bg)
            for sx, sy, sr in ((-22, -6, .5), (18, -9, .4), (27, 4, .35), (-30, 7, .4), (-12, -13, .3), (13, -15, .3)):
                x, y, rr = cx + sx * u, cy + sy * u, sr * u
                d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(220, 225, 240))
        else:                                    # crossed-out camera
            fg = (230, 230, 230)
            bw, bh = 18 * u, 11.5 * u
            x0, y0 = cx - bw / 2, cy - bh / 2 + 1.0 * u
            my = y0 + bh / 2
            d.rounded_rectangle((x0 + 2.5 * u, y0 - 2.2 * u, x0 + 7.5 * u, y0 + u), radius=0.7 * u, fill=fg)  # viewfinder
            d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=1.8 * u, fill=fg)                          # body
            r = 3.8 * u
            d.ellipse((cx - r, my - r, cx + r, my + r), fill=bg)                                               # lens
            r2 = 2.1 * u
            d.ellipse((cx - r2, my - r2, cx + r2, my + r2), fill=fg)
            d.ellipse((x0 + bw - 3.4 * u, y0 + 1.5 * u, x0 + bw - 1.7 * u, y0 + 3.2 * u), fill=bg)            # flash
            a, b = (cx - 11.5 * u, my - 8.5 * u), (cx + 11.5 * u, my + 8.5 * u)
            d.line((a, b), fill=bg, width=round(3.8 * u))                                                  # gap
            d.line((a, b), fill=(225, 60, 50), width=round(1.6 * u))                                       # stroke

    y = h * (0.50 if not path else 0.5 - 0.06 * len(lines))
    for i, ln in enumerate(lines):
        size_px = round((6.0 if i == 0 else 3.4) * u)
        while True:
            f = _font(size_px, bold=i == 0)
            stroke = max(1, size_px // 14) if path else 0
            l, t, r, b = d.textbbox((0, 0), ln, font=f, stroke_width=stroke)
            if r - l <= w * 0.92 or size_px <= 10:
                break
            size_px = int(size_px * 0.9)
        d.text(((w - (r - l)) / 2 - l, y - t), ln, font=f, fill=(245, 245, 245),
               stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += (b - t) + (2.2 if i == 0 else 1.4) * u

    now = dt.datetime.now()
    foot = c.name if "{time}" in (c.night_text if night else c.offline_text) else \
        f"{c.name} · {TEXTS.get(c.language, TEXTS['en'])['as_of']} {fmt_date(now, c.language)} {now:%H:%M}"
    f = _font(max(10, round(1.9 * u)))
    l, t, r, b = d.textbbox((0, 0), foot, font=f, stroke_width=1)
    d.text((1.5 * u - l, h - 1.5 * u - b), foot, font=f, fill=(190, 190, 190), stroke_width=1, stroke_fill=(0, 0, 0))
    out = BytesIO()
    im.save(out, "JPEG", quality=max(1, min(95, c.output_quality)), optimize=True)
    return out.getvalue()


def postprocess(img: bytes, c: "CameraConfig") -> bytes:
    """Shrinks/blurs the picture and strips metadata. The original is discarded."""
    if not c.max_width and not c.blur and not c.face_blur:
        return img
    from io import BytesIO
    from PIL import Image, ImageFilter
    im = Image.open(BytesIO(img))
    im = im.convert("RGB")                      # also drops EXIF/metadata
    if c.face_blur:                             # on the original, so that smaller faces are found too
        try:
            import tapo_faces
            t0 = time.time()
            faces = tapo_faces.detect(im, c.face_threshold)
            if faces:
                im = tapo_faces.anonymize(im, faces, c.face_margin)
                log.info("[%s] %d face(s) anonymised (%.1f s)", c.name, len(faces), time.time() - t0)
        except Exception as e:  # noqa: BLE001
            # better an unusable picture than an unprotected one
            log.error("[%s] face detection failed (%r) – blurring the whole picture", c.name, e)
            im = im.filter(ImageFilter.GaussianBlur(max(8, im.width / 80)))
    if c.max_width and im.width > c.max_width:
        h = round(im.height * c.max_width / im.width)
        im = im.resize((c.max_width, h), Image.LANCZOS)
    if c.blur:
        im = im.filter(ImageFilter.GaussianBlur(c.blur))
    out = BytesIO()
    im.save(out, "JPEG", quality=max(1, min(95, c.output_quality)), optimize=True)
    return out.getvalue()


def parse_battery(res) -> tuple[int | None, str | None]:
    """Finds charge level (%) and charging state in the getBatteryStatus response."""
    pct: int | None = None
    chg: str | None = None

    def walk(o):
        nonlocal pct, chg
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower()
                if isinstance(v, (dict, list)):
                    walk(v)
                elif pct is None and any(s in kl for s in ("percent", "capacity", "level")):
                    try:
                        f = float(v)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= f <= 100:
                        pct = int(round(f))
                elif chg is None and "charg" in kl:
                    chg = str(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(res)
    return pct, chg


def load_config(path: str, need_upload: bool = True) -> Config:
    """need_upload=False: an incomplete [upload] (e.g. password only in the service's env file) is not an
    error, uploading is simply disabled."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cams = []
    defaults = raw.pop("defaults", {})       # applies to all cameras, can be overridden per camera
    for c in raw.pop("camera", []):
        c = {**defaults, **c}
        # the password may also come from an environment variable (e.g. a Docker secret)
        pw = c.get("password") or os.environ.get(c.get("password_env", "TAPO_PASSWORD"), "")
        c.pop("password_env", None)
        c["password"] = pw
        cams.append(CameraConfig(**c))
    if not cams:
        raise SystemExit("No [[camera]] found in the configuration.")
    for c in cams:
        if c.connection not in ("local", "cloud"):
            raise SystemExit(f"Camera '{c.name}': connection must be local|cloud.")
        if c.connection == "local" and not (c.host and c.password):
            raise SystemExit(f"Camera '{c.name}': host and password required (connection=local).")
        parse_hours(c.active_hours, c.name)
        if c.face_blur:
            import tapo_faces
            problem = tapo_faces.check_available()
            if problem:
                raise SystemExit(f"Camera '{c.name}': face_blur = true, but {problem}.")
        parse_days(c.active_days, c.name)
        if c.connection == "cloud" and not c.cloud_device:
            raise SystemExit(f"Camera '{c.name}': cloud_device required (name shown by `cloud-login`).")
        if c.mode not in ("auto", "snapshot", "stream"):
            raise SystemExit(f"Camera '{c.name}': mode must be auto|snapshot|stream.")
        if c.language not in TEXTS:
            raise SystemExit(f"Camera '{c.name}': language must be one of {', '.join(TEXTS)}.")
        for key in ("night_image", "offline_image"):
            p = getattr(c, key)
            if p and not os.access(p, os.R_OK):
                raise SystemExit(f"Camera '{c.name}': {key} '{p}' not readable (permissions for the service user?).")
    up = raw.pop("upload", None)
    upload = None
    if up:
        up = dict(up)
        up["password"] = up.get("password") or os.environ.get(up.pop("password_env", "SFTP_PASSWORD"), "")
        up.pop("password_env", None)
        try:
            upload = UploadConfig(**up)
        except TypeError as e:
            raise SystemExit(f"[upload]: {e}")
        problem = None
        if not (upload.host and upload.user and (upload.password or upload.key)):
            problem = "[upload]: host, user and key or password (or SFTP_PASSWORD) required."
        elif upload.key and not os.access(upload.key, os.R_OK):
            problem = f"[upload]: key '{upload.key}' not readable."
        if problem:
            if need_upload:
                raise SystemExit(problem)
            upload = None
    cfg = Config(cameras=cams, upload=upload, **{k: v for k, v in raw.items() if k != "output_dir"})
    cfg.output_dir = Path(raw.get("output_dir", "./images"))
    if upload and not upload.known_hosts:
        upload.known_hosts = str(Path(cfg.cloud_session_file).resolve().parent / "known_hosts_sftp")
    return cfg


# --------------------------------------------------------------------------- #
# SFTP upload
# --------------------------------------------------------------------------- #
class Uploader:
    """Uploads pictures via SFTP (paramiko). Written as .tmp first, then renamed -> never half a file."""

    def __init__(self, uc: UploadConfig):
        self.uc = uc
        self.lock = asyncio.Lock()
        self.last_ok: dict[str, float] = {}
        self.last_error: dict[str, str | None] = {}
        self._tasks: set[asyncio.Task] = set()

    def remote_name(self, c: CameraConfig) -> str:
        n = (c.upload_name or self.uc.name).replace("{camera}", c.name)
        return "".join(ch for ch in n if ch not in "/\\\0") or f"{c.name}.jpg"

    def _connect(self):
        import paramiko
        kh = Path(self.uc.known_hosts)

        class Tofu(paramiko.MissingHostKeyPolicy):
            # first contact: remember the host key; if it changes later, paramiko refuses to connect
            def missing_host_key(self, client, hostname, key):
                client.get_host_keys().add(hostname, key.get_name(), key)
                kh.parent.mkdir(parents=True, exist_ok=True)
                client.save_host_keys(str(kh))
                log.warning("SFTP: host key of %s saved (%s %s)", hostname, key.get_name(),
                            key.fingerprint if hasattr(key, "fingerprint") else key.get_base64()[:24])

        cli = paramiko.SSHClient()
        if kh.exists():
            cli.load_host_keys(str(kh))
        cli.set_missing_host_key_policy(Tofu())
        cli.connect(self.uc.host, port=self.uc.port, username=self.uc.user,
                    password=self.uc.password or None, key_filename=self.uc.key or None,
                    look_for_keys=False, allow_agent=False, timeout=self.uc.timeout,
                    banner_timeout=self.uc.timeout, auth_timeout=self.uc.timeout)
        return cli

    def _upload_sync(self, name: str, data: bytes):
        cli = self._connect()
        try:
            sftp = cli.open_sftp()
            sftp.get_channel().settimeout(self.uc.timeout)
            d = self.uc.dir.rstrip("/") or "/"
            final = f"{d}/{name}" if d != "/" else f"/{name}"
            tmp = final.rsplit("/", 1)[0] + f"/.{name}.tmp"
            with sftp.open(tmp, "wb") as f:
                f.set_pipelined(True)
                f.write(data)
            try:
                sftp.chmod(tmp, 0o644)
            except OSError:
                pass
            try:
                sftp.posix_rename(tmp, final)
            except OSError:                    # server without posix-rename: delete the old file first
                try:
                    sftp.remove(final)
                except OSError:
                    pass
                sftp.rename(tmp, final)
            return final
        finally:
            cli.close()

    async def upload(self, c: CameraConfig, data: bytes, name: str | None = None) -> bool:
        name = name or self.remote_name(c)
        async with self.lock:
            t0 = time.time()
            try:
                path = await asyncio.wait_for(asyncio.to_thread(self._upload_sync, name, data),
                                              self.uc.timeout * 3)
            except Exception as e:  # noqa: BLE001
                self.last_error[c.name] = f"{type(e).__name__}: {e}"
                log.warning("[%s] SFTP upload to %s failed: %s", c.name, self.uc.host, self.last_error[c.name])
                return False
            self.last_ok[c.name], self.last_error[c.name] = time.time(), None
            log.info("[%s] uploaded: %s:%s (%d KB, %.1f s)", c.name, self.uc.host, path,
                     len(data) // 1024, time.time() - t0)
            return True

    def upload_bg(self, c: CameraConfig, data: bytes):
        t = asyncio.create_task(self.upload(c, data))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)


# --------------------------------------------------------------------------- #
# Cloud access (ontapo) – one session for all cloud cameras
# --------------------------------------------------------------------------- #
class CloudAccount:
    _instance: "CloudAccount | None" = None
    REGIONS = ("euw1", "aps1", "use1")

    def __init__(self, session_file: str):
        self.session_file = Path(session_file)
        self.data: dict = {}
        self.session = None
        self.devices: dict[str, str] = {}      # name/id -> device_id
        self.lock = asyncio.Lock()

    @classmethod
    def get(cls, session_file: str) -> "CloudAccount":
        if cls._instance is None:
            cls._instance = cls(session_file)
        return cls._instance

    def _save(self):
        tmp = self.session_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.session_file)

    def _apply_region(self, region: str):
        import ontapo.relay as relay
        relay.CIPC_HOST = f"{region}-cipc-api.i.tplinkcloud.com"
        self.session.app_server = f"{region}-app-server.iot.i.tplinkcloud.com"
        self.data["region"], self.data["appServer"] = region, self.session.app_server

    def _build(self):
        from ontapo import OnTapo
        if self.session is not None:
            try:
                asyncio.get_running_loop().create_task(self.session.aclose())
            except RuntimeError:
                pass
        self.session = OnTapo.from_dict(self.data)
        self._apply_region(self.data.get("region") or "aps1")

    async def _open(self):
        if not self.session_file.exists():
            raise RuntimeError(f"No cloud session ({self.session_file}). Run `cloud-login` first.")
        self.data = json.loads(self.session_file.read_text())
        self._build()
        await self._load_devices()

    async def _load_devices(self):
        from ontapo.errors import Unauthorized
        tried, cams, err = [], None, None
        regions = [self.data.get("region") or "aps1"] + [r for r in self.REGIONS if r != self.data.get("region")]
        for region in regions:
            self._apply_region(region)
            try:
                cams = await self.session.cameras()
                if cams:
                    break
                tried.append(f"{region}: empty")
            except Unauthorized:
                raise
            except Exception as e:  # noqa: BLE001
                err = e
                tried.append(f"{region}: {e!r}")
        if not cams:
            raise RuntimeError(f"No cameras found ({'; '.join(tried)})") from err
        if tried:
            log.info("Using cloud region %s (tried before: %s)", self.data["region"], "; ".join(tried))
            self._save()
        self.devices = {}
        for c in cams:
            self.devices[c.device_id] = c.device_id
            self.devices[c.name] = c.device_id
            self.devices[c.name.lower()] = c.device_id
        return cams

    async def _refresh(self):
        from ontapo._crypto import APP_VERSION
        from tapo_cloud_auth import refresh_session
        log.info("Refreshing cloud token …")
        self.data = await refresh_session(self.data, APP_VERSION, log=log.debug)
        self._save()
        self._build()

    async def snapshot(self, device: str, timeout: int, seconds: float = 4.0,
                       retries: int = 3, retry_wait: float = 5.0) -> bytes:
        from ontapo.errors import RelayError
        last = None
        for n in range(retries + 1):
            try:
                return await self._snapshot_once(device, timeout, seconds)
            except RelayError as e:
                # 404 = camera not (yet) registered at the relay, typical while a battery camera wakes up
                msg = str(e)
                if "HTTP 404" not in msg and "no video data" not in msg and "ended before" not in msg:
                    raise
                last = e
                if n < retries:
                    log.info("Camera '%s' not ready yet (%s) – retrying in %.0f s (%d/%d)",
                             device, msg, retry_wait, n + 1, retries)
                    await asyncio.sleep(retry_wait)
        raise RuntimeError(f"Camera '{device}' unreachable after {retries + 1} attempts: {last} – "
                           f"battery empty, offline or Wi-Fi problem? Check the live view in the Tapo app.")

    async def battery(self, device: str, timeout: float = 15.0) -> dict:
        """getBatteryStatus via the cloud. Only call right after a picture (camera awake)."""
        async with self.lock:
            if self.session is None:
                await self._open()
            dev_id = self.devices.get(device) or self.devices.get(device.lower())
            if not dev_id:
                raise RuntimeError(f"Camera '{device}' not found in the account")
            return await asyncio.wait_for(
                self.session.request(dev_id, "getBatteryStatus", {"battery": {"name": "status"}}), timeout)

    async def _snapshot_once(self, device: str, timeout: int, seconds: float) -> bytes:
        from ontapo.errors import Unauthorized
        import tempfile
        async with self.lock:                  # relay sessions one after another
            for attempt in range(2):
                try:
                    if self.session is None:
                        await self._open()
                    dev_id = self.devices.get(device) or self.devices.get(device.lower())
                    if not dev_id:
                        await self._load_devices()
                        dev_id = self.devices.get(device) or self.devices.get(device.lower())
                    if not dev_id:
                        names = sorted(k for k, v in self.devices.items() if k != v)
                        raise RuntimeError(f"Camera '{device}' not found in the account. Available: {names}")
                    dev = self.session.device(dev_id, name=device)
                    with tempfile.TemporaryDirectory() as td:
                        out = os.path.join(td, "snap.jpg")
                        await asyncio.wait_for(dev.snapshot(out, seconds=seconds), timeout=timeout + seconds)
                        return Path(out).read_bytes()
                except Unauthorized:
                    if attempt:
                        raise
                    await self._refresh()
        raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Camera access
# --------------------------------------------------------------------------- #
class Camera:
    def __init__(self, cfg: CameraConfig, cloud: "CloudAccount | None" = None, state_dir: Path | None = None,
                 uploader: "Uploader | None" = None):
        self.cfg = cfg
        self.cloud = cloud
        self.uploader = uploader if cfg.upload else None
        self.placeholder: str | None = None   # placeholder currently shown ("night"/"offline")
        self.failures = 0                     # failed interval captures in a row
        self._tapo = None
        self._lock = asyncio.Lock()
        self.last_image: bytes | None = None
        self.last_time: float = 0.0
        self.last_error: str | None = None
        self.last_method: str | None = None
        self._snapshot_misses = 0            # how often the native still image came back empty
        self.battery: int | None = None      # last battery level in %
        self.battery_charging: str | None = None
        self.battery_time: float = 0.0
        self.battery_task: asyncio.Task | None = None
        self._battery_warned = False
        self._pause_logged: float | None = None
        self._state_file = (state_dir / cfg.name / ".battery.json") if state_dir else None
        self._load_battery()

    # --- battery protection ----------------------------------------------------
    def _load_battery(self):
        try:
            d = json.loads(self._state_file.read_text())
            self.battery, self.battery_charging, self.battery_time = d["percent"], d.get("charging"), d["time"]
        except Exception:  # noqa: BLE001  (no reading / no state yet)
            pass

    def _save_battery(self):
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps({"percent": self.battery, "charging": self.battery_charging,
                                                    "time": self.battery_time}))
        except OSError as e:
            log.warning("[%s] could not save battery level: %s", self.cfg.name, e)

    async def update_battery(self):
        """Read the battery level – only right after a capture, while the camera is awake."""
        if not self.cloud:
            return
        try:
            res = await self.cloud.battery(self.cfg.cloud_device)
        except Exception as e:  # noqa: BLE001
            if not self._battery_warned:
                log.warning("[%s] battery level not available: %r", self.cfg.name, e)
                self._battery_warned = True
            return
        pct, chg = parse_battery(res)
        if pct is None:
            log.warning("[%s] battery level not found in response: %s", self.cfg.name, json.dumps(res)[:300])
            return
        self._battery_warned = False
        self.battery, self.battery_charging, self.battery_time = pct, chg, time.time()
        log.info("[%s] battery %d %%%s", self.cfg.name, pct, f" (charging: {chg})" if chg else "")
        self._save_battery()

    def battery_paused_until(self) -> float | None:
        """Time until which captures pause because of a low battery – otherwise None."""
        c = self.cfg
        if not c.battery_min or self.battery is None or self.battery >= c.battery_min:
            return None
        until = self.battery_time + c.battery_pause_hours * 3600
        return until if time.time() < until else None

    @property
    def _snapshot_unsupported(self) -> bool:
        # after 3 empty answers in a row go straight to the stream (saves time and battery)
        return self._snapshot_misses >= 3

    # pytapo logs in synchronously (requests) -> run in a thread
    def _connect_sync(self):
        from pytapo import Tapo
        c = self.cfg
        return Tapo(
            c.host, c.user, c.password, c.password,
            childID=c.child_id,
            printDebugInformation=log.isEnabledFor(logging.DEBUG),
            printWarnInformation=False,
        )

    async def tapo(self):
        if self._tapo is None:
            log.info("[%s] connecting to %s …", self.cfg.name, self.cfg.host)
            self._tapo = await asyncio.to_thread(self._connect_sync)
        return self._tapo

    def reset(self):
        self._tapo = None

    # --- method 1: native still image (fast, ~640x360) -------------------------
    async def _grab_snapshot(self) -> bytes | None:
        from pytapo.media_stream.snapshot import getSnapshot
        tapo = await self.tapo()
        return await getSnapshot(tapo, timeout=self.cfg.timeout)

    # --- method 2: one frame from the live stream (full resolution) ------------
    async def _grab_stream_frame(self) -> bytes | None:
        from pytapo.media_stream._utils import StreamType
        tapo = await self.tapo()
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found (required for mode=stream)")

        ff = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-probesize", "512k", "-analyzeduration", "1M",
            "-f", "mpegts", "-i", "pipe:0",
            "-frames:v", "1", "-q:v", str(self.cfg.jpeg_quality),
            "-f", "image2", "-c:v", "mjpeg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        preview = {"audio": ["default"], "channels": [0], "resolutions": [self.cfg.quality]}
        if self.cfg.child_id:
            preview["deviceId"] = self.cfg.child_id
        payload = json.dumps({"type": "request", "seq": 1,
                              "params": {"preview": preview, "method": "get"}})

        async def feed():
            buf = bytearray()
            session = tapo.getMediaSession(StreamType.Stream)
            session.set_window_size(50)
            async with session:
                async for resp in session.transceive(payload, no_data_timeout=self.cfg.timeout):
                    if ff.returncode is not None or ff.stdin.is_closing():
                        break
                    if resp.mimetype != "video/mp2t":
                        continue
                    buf += resp.plaintext
                    # align to 188-byte TS packets
                    while len(buf) >= 188 and buf[0] != 0x47:
                        pos = buf.find(0x47, 1)
                        if pos == -1:
                            buf.clear()
                            break
                        del buf[:pos]
                    n = len(buf) - (len(buf) % 188)
                    if n:
                        ff.stdin.write(bytes(buf[:n]))
                        del buf[:n]
                        try:
                            await ff.stdin.drain()
                        except (BrokenPipeError, ConnectionResetError):
                            break

        feeder = asyncio.create_task(feed())
        try:
            out, err = await asyncio.wait_for(ff.communicate(), timeout=self.cfg.timeout + 5)
        except asyncio.TimeoutError:
            ff.kill()
            raise RuntimeError("timeout while reading the stream")
        finally:
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):  # session teardown may fail
                pass
        if ff.returncode != 0 or not out:
            raise RuntimeError(f"ffmpeg returned no picture: {err.decode(errors='ignore')[-300:]}")
        return out

    async def capture(self, force: bool = False) -> bytes:
        """Returns a JPEG. Uses a cache so that battery cameras are not woken up constantly."""
        async with self._lock:
            age = time.time() - self.last_time
            if not force and self.last_image and age < self.cfg.cache_seconds:
                return self.last_image

            if self.cfg.connection == "cloud":
                try:
                    img = await self.cloud.snapshot(self.cfg.cloud_device, self.cfg.timeout,
                                                    seconds=self.cfg.cloud_seconds,
                                                    retries=self.cfg.cloud_retries,
                                                    retry_wait=self.cfg.cloud_retry_wait)
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"cloud: {e!r}"
                    log.debug("[%s] cloud failed", self.cfg.name, exc_info=True)
                    raise RuntimeError(self.last_error)
                if img[:2] != b"\xff\xd8":
                    self.last_error = "cloud: not a JPEG"
                    raise RuntimeError(self.last_error)
                img = await asyncio.to_thread(postprocess, img, self.cfg)
                self.last_image, self.last_time = img, time.time()
                self.last_error, self.last_method = None, "cloud"
                log.info("[%s] picture taken (cloud, %d KB)", self.cfg.name, len(img) // 1024)
                if self.battery_task is None or self.battery_task.done():
                    self.battery_task = asyncio.create_task(self.update_battery())
                return img

            mode = self.cfg.mode
            methods = {"snapshot": ["snapshot"], "stream": ["stream"],
                       "auto": (["stream"] if self._snapshot_unsupported else ["snapshot", "stream"])}[mode]
            errors = []
            for attempt in range(2):           # second round with a fresh login
                for m in methods:
                    try:
                        img = await (self._grab_snapshot() if m == "snapshot" else self._grab_stream_frame())
                        if img and img[:2] == b"\xff\xd8":
                            img = await asyncio.to_thread(postprocess, img, self.cfg)
                            self.last_image, self.last_time = img, time.time()
                            self.last_error, self.last_method = None, m
                            if m == "snapshot":
                                self._snapshot_misses = 0
                            log.info("[%s] picture taken (%s, %d KB)", self.cfg.name, m, len(img) // 1024)
                            return img
                        errors.append(f"{m}: no picture")
                        if m == "snapshot":
                            self._snapshot_misses += 1
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{m}: {e!r}")
                        log.debug("[%s] %s failed", self.cfg.name, m, exc_info=True)
                self.reset()
            self.last_error = "; ".join(errors)
            raise RuntimeError(self.last_error)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def save_image(cfg: Config, cam: str, img: bytes, tag: str | None = None) -> Path:
    now = dt.datetime.now()
    base = cfg.output_dir / cam
    base.mkdir(parents=True, exist_ok=True)
    latest = base / "latest.jpg"
    tmp = base / ".latest.tmp"
    tmp.write_bytes(img)
    os.replace(tmp, latest)                    # atomic, a web server never sees half a file
    if not cfg.keep_history and not tag:
        return latest
    day = base / now.strftime("%Y/%m/%d")
    day.mkdir(parents=True, exist_ok=True)
    safe_tag = "".join(ch for ch in (tag or "") if ch.isalnum() or ch in "-_")[:32]
    path = day / (now.strftime("%H%M%S") + (f"_{safe_tag}" if safe_tag else "") + ".jpg")
    path.write_bytes(img)
    if tag:
        (base / f"latest_{safe_tag}.jpg").write_bytes(img)
    return path


def save_latest(cfg: Config, cam: str, img: bytes) -> Path:
    """Replace latest.jpg only (for placeholders – not archived)."""
    base = cfg.output_dir / cam
    base.mkdir(parents=True, exist_ok=True)
    tmp, latest = base / ".latest.tmp", base / "latest.jpg"
    tmp.write_bytes(img)
    os.replace(tmp, latest)
    return latest


async def store(cfg: Config, cam: "Camera", img: bytes, tag: str | None = None) -> Path:
    """Store a real camera picture and upload it (in the background)."""
    path = await asyncio.to_thread(save_image, cfg, cam.cfg.name, img, tag)
    cam.placeholder = None
    if cam.uploader:
        cam.uploader.upload_bg(cam.cfg, img)
    return path


def _image_size(img: bytes | None) -> tuple[int, int] | None:
    if not img:
        return None
    try:
        from io import BytesIO
        from PIL import Image
        return Image.open(BytesIO(img)).size
    except Exception:  # noqa: BLE001
        return None


async def check_pause(cfg: Config, cam: "Camera") -> bool:
    """True = no interval capture now (night or battery pause). Shows the placeholder once if needed."""
    c = cam.cfg
    now = dt.datetime.now()
    if not is_active(c, now):
        log.debug("[%s] outside the time window – no capture", c.name)
        await show_placeholder(cfg, cam, "night", {"next": fmt_when(next_active(c, now), now, c.language)})
        return True
    if until := cam.battery_paused_until():
        if cam._pause_logged != until:
            log.warning("[%s] battery %d %% < %d %% – no interval captures until %s", c.name, cam.battery,
                        c.battery_min, dt.datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M"))
            cam._pause_logged = until
        await show_placeholder(cfg, cam, "offline")     # the reason (battery) is not shown publicly
        return True
    return False


async def show_placeholder(cfg: Config, cam: "Camera", kind: str, values: dict | None = None):
    """Store and upload a placeholder once per pause/outage ('night' | 'offline')."""
    c = cam.cfg
    if not c.placeholders or cam.placeholder == kind:
        return
    now = dt.datetime.now()
    values = {**(values or {}), "camera": c.name, "time": now.strftime("%H:%M"), "date": fmt_date(now, c.language)}
    try:
        img = await asyncio.to_thread(render_placeholder, c, kind, values, _image_size(cam.last_image))
        await asyncio.to_thread(save_latest, cfg, c.name, img)
    except Exception as e:  # noqa: BLE001
        log.error("[%s] placeholder '%s' failed: %r", c.name, kind, e)
        cam.placeholder = kind             # do not retry every round
        return
    ok = await cam.uploader.upload(c, img) if cam.uploader else True
    if ok:                                 # otherwise retry next time
        cam.placeholder = kind
        log.info("[%s] placeholder '%s' set", c.name, kind)


def cleanup(cfg: Config):
    if cfg.retention_days <= 0:
        return
    limit = time.time() - cfg.retention_days * 86400
    for f in cfg.output_dir.rglob("*.jpg"):
        if f.name.startswith("latest"):
            continue
        try:
            if f.stat().st_mtime < limit:
                f.unlink()
        except FileNotFoundError:
            pass
    for d in sorted(cfg.output_dir.rglob("*"), reverse=True):   # remove empty folders
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
async def interval_loop(cfg: Config, cam: Camera):
    if cam.cfg.interval <= 0:
        return
    await check_pause(cfg, cam)            # after a start at night, set the placeholder right away
    while True:
        # align to "round" times (e.g. :00, :05, :10 …)
        await asyncio.sleep(cam.cfg.interval - (time.time() % cam.cfg.interval))
        if await check_pause(cfg, cam):
            continue
        try:
            img = await cam.capture(force=True)
        except Exception as e:  # noqa: BLE001
            cam.failures += 1
            log.warning("[%s] capture failed (%d× in a row): %s", cam.cfg.name, cam.failures, e)
            if cam.failures >= max(1, cam.cfg.offline_after):
                await show_placeholder(cfg, cam, "offline")
            continue
        cam.failures = 0
        try:
            await store(cfg, cam, img)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] storing failed: %s", cam.cfg.name, e)


async def cleanup_loop(cfg: Config):
    while True:
        await asyncio.to_thread(cleanup, cfg)
        await asyncio.sleep(3600)


def build_app(cfg: Config, cams: dict[str, Camera]):
    from aiohttp import web

    @web.middleware
    async def auth(request, handler):
        if cfg.token and request.path != "/health":
            tok = request.query.get("token") or request.headers.get("X-Token")
            if tok != cfg.token:
                return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    def get_cam(request) -> Camera:
        name = request.match_info.get("cam") or next(iter(cams))
        if name not in cams:
            raise web.HTTPNotFound(text=f"unknown camera '{name}'")
        return cams[name]

    jpeg_headers = {"Cache-Control": "no-store"}

    async def snapshot(request):
        """Fresh picture (or cached if < cache_seconds). ?save=1 also stores it."""
        cam = get_cam(request)
        try:
            img = await cam.capture(force=request.query.get("force") == "1")
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] capture (snapshot.jpg) failed: %s", cam.cfg.name, e)
            return web.json_response({"error": str(e)}, status=502)
        if request.query.get("save") == "1":
            await store(cfg, cam, img)
        return web.Response(body=img, content_type="image/jpeg", headers=jpeg_headers)

    async def latest(request):
        """Last stored picture – does NOT wake the camera (ideal for dashboards)."""
        cam = get_cam(request)
        f = cfg.output_dir / cam.cfg.name / "latest.jpg"
        if not f.exists():
            return web.json_response({"error": "no picture yet"}, status=404)
        return web.FileResponse(f, headers=jpeg_headers)

    async def event_image(request):
        """Last event picture for a tag, e.g. /entrance/event/doorbell.jpg"""
        cam = get_cam(request)
        tag = "".join(ch for ch in request.match_info["tag"] if ch.isalnum() or ch in "-_")[:32]
        f = cfg.output_dir / cam.cfg.name / f"latest_{tag}.jpg"
        if not f.exists():
            return web.json_response({"error": "no event picture"}, status=404)
        return web.FileResponse(f, headers=jpeg_headers)

    async def trigger(request):
        """Event capture (e.g. doorbell): takes a fresh picture, stores it with a tag, returns JSON."""
        cam = get_cam(request)
        tag = request.query.get("tag", "event")
        try:
            img = await cam.capture(force=True)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] capture (trigger) failed: %s", cam.cfg.name, e)
            return web.json_response({"ok": False, "error": str(e)}, status=502)
        path = await store(cfg, cam, img, tag)
        if request.query.get("format") == "jpeg":
            return web.Response(body=img, content_type="image/jpeg", headers=jpeg_headers)
        return web.json_response({"ok": True, "camera": cam.cfg.name, "file": str(path),
                                  "size": len(img), "method": cam.last_method})

    async def status(request):
        return web.json_response({
            name: {"host": c.cfg.host or c.cfg.cloud_device, "mode": c.cfg.mode, "interval": c.cfg.interval,
                   "last_capture": dt.datetime.fromtimestamp(c.last_time).isoformat() if c.last_time else None,
                   "connection": c.cfg.connection,
                   "active_hours": c.cfg.active_hours or "always", "active_now": is_active(c.cfg),
                   "face_blur": c.cfg.face_blur,
                   "battery": c.battery, "battery_charging": c.battery_charging,
                   "battery_measured": dt.datetime.fromtimestamp(c.battery_time).isoformat() if c.battery_time else None,
                   "battery_min": c.cfg.battery_min or None,
                   "battery_paused_until": (dt.datetime.fromtimestamp(u).isoformat()
                                            if (u := c.battery_paused_until()) else None),
                   "placeholder": c.placeholder, "failures": c.failures,
                   "upload": (None if not c.uploader else {
                       "target": f"{c.uploader.uc.host}:{c.uploader.uc.dir.rstrip('/')}/{c.uploader.remote_name(c.cfg)}",
                       "last_ok": (dt.datetime.fromtimestamp(t).isoformat()
                                   if (t := c.uploader.last_ok.get(name)) else None),
                       "last_error": c.uploader.last_error.get(name)}),
                   "last_method": c.last_method, "last_error": c.last_error}
            for name, c in cams.items()})

    async def health(request):
        return web.json_response({"ok": True})

    async def index(request):
        links = "".join(
            f'<h2>{n}</h2><img src="/{n}/latest.jpg" style="max-width:100%">'
            f'<p><a href="/{n}/snapshot.jpg">take a fresh picture</a> · <a href="/status">status</a></p>'
            for n in cams)
        return web.Response(text=f"<!doctype html><meta charset=utf-8><title>tapo-webcam</title>"
                                 f"<body style='font-family:sans-serif'>{links}</body>",
                            content_type="text/html")

    app = web.Application(middlewares=[auth])
    app.add_routes([
        web.get("/", index), web.get("/health", health), web.get("/status", status),
        web.get("/snapshot.jpg", snapshot), web.get("/latest.jpg", latest),
        web.get("/trigger", trigger), web.post("/trigger", trigger),
        web.get("/{cam}/snapshot.jpg", snapshot), web.get("/{cam}/latest.jpg", latest),
        web.get("/{cam}/trigger", trigger), web.post("/{cam}/trigger", trigger),
        web.get("/{cam}/event/{tag}.jpg", event_image),
    ])
    return app


async def serve(cfg: Config):
    from aiohttp import web
    cams = {c.name: make_camera(cfg, c) for c in cfg.cameras}
    app = build_app(cfg, cams)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, cfg.listen, cfg.port).start()
    log.info("HTTP API on http://%s:%d/", cfg.listen, cfg.port)
    tasks = [asyncio.create_task(interval_loop(cfg, c)) for c in cams.values()]
    tasks.append(asyncio.create_task(cleanup_loop(cfg)))
    await asyncio.gather(*tasks)
    await asyncio.Event().wait()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def make_camera(cfg: Config, c: CameraConfig) -> Camera:
    cloud = CloudAccount.get(cfg.cloud_session_file) if c.connection == "cloud" else None
    return Camera(c, cloud, state_dir=cfg.output_dir, uploader=get_uploader(cfg))


def get_uploader(cfg: Config) -> "Uploader | None":
    if cfg.upload is None:
        return None
    if getattr(cfg, "_uploader", None) is None:
        cfg._uploader = Uploader(cfg.upload)
    return cfg._uploader


async def cmd_placeholders(cfg: Config, do_upload: bool):
    """Store placeholder previews; with --upload also a test upload – does not wake any camera."""
    now = dt.datetime.now()
    for c in cfg.cameras:
        nxt = next_active(c, now) if (c.active_hours or c.active_days) else now + dt.timedelta(hours=9)
        base_vals = {"camera": c.name, "time": now.strftime("%H:%M"), "date": fmt_date(now, c.language)}
        imgs = {
            "night": render_placeholder(c, "night", {**base_vals, "next": fmt_when(nxt, now, c.language)}),
            "offline": render_placeholder(c, "offline", base_vals),
        }
        base = cfg.output_dir / c.name
        base.mkdir(parents=True, exist_ok=True)
        for kind, img in imgs.items():
            p = base / f"placeholder_{kind}.jpg"
            p.write_bytes(img)
            print(f"{c.name}: {p}  ({len(img) // 1024} KB)")
        up = get_uploader(cfg)
        if do_upload:
            if not up:
                raise SystemExit("No [upload] section in the configuration.")
            name = "tapo-webcam-test.jpg"
            ok = await up.upload(c, imgs["night"], name=name)
            print(f"{c.name}: test upload {'ok' if ok else 'FAILED'} -> "
                  f"{up.uc.host}:{up.uc.dir.rstrip('/')}/{name}" + ("" if ok else f"  ({up.last_error[c.name]})"))
            if not ok:
                sys.exit(1)


async def cmd_cloud_login(cfg: Config | None, session_file: str):
    """Interactive sign-in to the Tapo account (incl. e-mail code), stores the session."""
    import getpass
    from ontapo._crypto import APP_VERSION
    from tapo_cloud_auth import CloudLogin
    email = input("Tapo account e-mail: ").strip()
    pw = getpass.getpass("Password: ")
    loop = asyncio.get_running_loop()

    async def mfa():
        return (await loop.run_in_executor(None, input, "Code from the TP-Link e-mail: ")).strip()

    cl = CloudLogin(email, pw, APP_VERSION)
    try:
        data = await cl.login(mfa)
    finally:
        await cl.close()
    acc = CloudAccount(session_file)
    acc.data = data
    acc._build()
    acc._save()
    print(f"Session saved to {session_file} (mode 600 – contains access tokens!)")
    try:
        cams = await acc._load_devices()
        print(f"Cameras in the account (region {acc.data['region']}):")
        for c in cams:
            print(f"  cloud_device = \"{c.name}\"   ({c.model}, id {c.device_id})")
    except Exception as e:  # noqa: BLE001
        print(f"Login ok, but listing cameras failed: {e}")
    await acc.session.aclose()


async def cmd_discover(cfg: Config):
    for c in cfg.cameras:
        if c.connection == "cloud":
            print(f"== {c.name} (cloud: {c.cloud_device}) ==")
            acc = CloudAccount.get(cfg.cloud_session_file)
            try:
                await acc._open()
                for cam in await acc.session.cameras():
                    print(f"  {cam.name}  {cam.model}  id {cam.device_id}")
            except Exception as e:  # noqa: BLE001
                print(f"  cloud error: {e}")
            continue
        cam = Camera(c)
        print(f"== {c.name} ({c.host}) ==")
        try:
            tapo = await cam.tapo()
            info = await asyncio.to_thread(tapo.getBasicInfo)
            bi = info.get("device_info", {}).get("basic_info", info)
            print(f"  model: {bi.get('device_model')}  type: {bi.get('device_type')}  "
                  f"firmware: {bi.get('sw_version')}")
        except Exception as e:  # noqa: BLE001
            print(f"  connection failed: {e}")
            continue
        try:
            children = await asyncio.to_thread(tapo.getChildDevices)
            for ch in children or []:
                print(f"  child device: {ch.get('device_model')}  '{ch.get('alias')}'  "
                      f"child_id = \"{ch.get('device_id')}\"")
        except Exception:  # noqa: BLE001
            pass  # not a hub


async def cmd_snap(cfg: Config, out: str | None):
    for c in cfg.cameras:
        cam = make_camera(cfg, c)
        t0 = time.time()
        img = await cam.capture(force=True)
        path = Path(out) if out and len(cfg.cameras) == 1 else save_image(cfg, c.name, img)
        if out and len(cfg.cameras) == 1:
            path.write_bytes(img)
        print(f"{c.name}: {path}  ({len(img)//1024} KB, {cam.last_method}, {time.time()-t0:.1f}s)")
        if cam.uploader and not out:
            ok = await cam.uploader.upload(c, img)
            print(f"{c.name}: upload {'ok' if ok else 'failed: ' + str(cam.uploader.last_error.get(c.name))}")
        if cam.battery_task:
            await cam.battery_task
            if cam.battery is not None:
                print(f"{c.name}: battery {cam.battery} %" + (f" (charging: {cam.battery_charging})"
                                                              if cam.battery_charging else ""))
    acc = CloudAccount._instance
    if acc is not None and acc.session is not None:
        await acc.session.aclose()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["discover", "snap", "serve", "cloud-login", "placeholders"])
    ap.add_argument("-c", "--config", default=os.environ.get("TAPO_WEBCAM_CONFIG", "config.toml"))
    ap.add_argument("-o", "--output", help="snap: target file (single camera only)")
    ap.add_argument("--upload", action="store_true",
                    help="placeholders: upload the night picture as tapo-webcam-test.jpg (connection test)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if a.command == "cloud-login":
        sf = "cloud_session.json"
        if os.path.exists(a.config):
            with open(a.config, "rb") as f:
                sf = tomllib.load(f).get("cloud_session_file", sf)
        asyncio.run(cmd_cloud_login(None, sf))
        return
    cfg = load_config(a.config, need_upload=a.command == "serve" or (a.command == "placeholders" and a.upload))
    try:
        if a.command == "discover":
            asyncio.run(cmd_discover(cfg))
        elif a.command == "snap":
            asyncio.run(cmd_snap(cfg, a.output))
        elif a.command == "placeholders":
            asyncio.run(cmd_placeholders(cfg, a.upload))
        else:
            asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
