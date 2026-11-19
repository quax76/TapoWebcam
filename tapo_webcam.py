#!/usr/bin/env python3
"""
tapo-webcam – holt regelmäßig Standbilder aus Tapo-Kameras, auch aus Modellen
ohne RTSP/ONVIF (Akku-Kameras, Türklingeln, Kameras am Hub H200/H500).

Zwei Verbindungswege pro Kamera:
  local – lokales TP-Link-Protokoll (Port 8800) wie die App im WLAN, via pytapo
  cloud – über das TP-Link-Cloud-Relay wie die App unterwegs, via ontapo
          (inoffiziell/experimentell; kein LAN/VPN nötig)

Befehle:
  tapo_webcam.py discover -c config.toml   Kamera prüfen, Hub-Kinder auflisten
  tapo_webcam.py snap     -c config.toml   Ein Bild aufnehmen und speichern
  tapo_webcam.py serve    -c config.toml   Dienst: Intervall + HTTP-API
  tapo_webcam.py cloud-login -c config.toml  Tapo-Konto anmelden (für connection="cloud")
  tapo_webcam.py placeholders -c config.toml [--upload]
                                           Nacht-/Außer-Betrieb-Platzhalter als Vorschau ablegen, optional SFTP-Test
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
logging.getLogger("paramiko").setLevel(logging.WARNING)   # sonst 3 INFO-Zeilen pro Upload


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #
@dataclass
class CameraConfig:
    name: str
    connection: str = "local"        # local = im LAN (pytapo) | cloud = über TP-Link-Cloud-Relay (ontapo)
    host: str = ""                   # nur local: IP der Kamera bzw. des Hubs
    password: str = ""               # nur local: Passwort des TP-Link/Tapo-Kontos
    cloud_device: str | None = None  # nur cloud: Kameraname aus der App oder device_id
    cloud_seconds: float = 3.0       # nur cloud: so lange wird der Stream geöffnet (kürzer = akkuschonender)
    cloud_retries: int = 3           # nur cloud: Wiederholungen, wenn die Kamera noch nicht wach ist (Relay 404)
    cloud_retry_wait: float = 5.0    # nur cloud: Sekunden zwischen den Wiederholungen
    user: str = "admin"
    child_id: str | None = None      # nur bei Kamera am Hub: device_id des Kindgeräts
    mode: str = "auto"               # auto | snapshot | stream
    quality: str = "HD"              # für mode=stream: HD | VGA
    interval: int = 300              # Sekunden zwischen Aufnahmen, 0 = aus
    timeout: int = 20                # Sekunden pro Aufnahmeversuch
    cache_seconds: int = 15          # so lange wird ein frisches Bild wiederverwendet
    jpeg_quality: int = 3            # ffmpeg -q:v (2 = beste, 31 = schlechteste)
    # Zeitfenster für die Intervall-Aufnahmen, z. B. "08:00-19:00" (leer = rund um die Uhr)
    active_hours: str = ""
    active_days: str = ""            # z. B. "Mo-Fr" oder "Sa,So" (leer = jeden Tag)
    # Datenschutz: Bild direkt nach dem Download verkleinern/weichzeichnen
    max_width: int = 0               # z. B. 1280; 0 = Originalgröße
    blur: float = 0.0                # Gauß-Weichzeichner-Radius in Pixeln (nach dem Verkleinern), 0 = aus
    output_quality: int = 80         # JPEG-Qualität des gespeicherten Bildes (1–95)
    face_blur: bool = False          # Gesichter erkennen und unkenntlich machen (OpenCV YuNet)
    face_threshold: float = 0.6      # Erkennungsschwelle 0–1: niedriger = findet mehr, aber auch Fehltreffer
    face_margin: float = 0.4         # Rand um das Gesicht, der mit verpixelt wird (Anteil der Gesichtsgröße)
    # nur cloud: Akkuschutz. Der Akkustand wird nach jedem Bild ausgelesen (Kamera ist dann ohnehin wach).
    battery_min: int = 0             # liegt der letzte Wert darunter (in %), pausieren die Intervall-Aufnahmen; 0 = aus
    battery_pause_hours: float = 6.0 # Pausendauer; danach ein Bild + neue Messung, bei weiter zu wenig Akku erneut Pause
    # Platzhalterbild statt Kamerabild: nachts (außerhalb active_hours/active_days) sowie "außer Betrieb"
    # (Akkupause oder Kamera/Cloud nicht erreichbar – der Grund wird bewusst nicht angezeigt).
    # Wird einmal pro Pause/Störung als latest.jpg abgelegt und hochgeladen (nicht ins Archiv).
    placeholders: bool = False
    night_image: str = ""            # eigenes Bild (JPEG/PNG) als Hintergrund; leer = dunkler Hintergrund mit Mond
    night_text: str = "Es ist Nacht\nNächstes Bild ab {next} Uhr"
    offline_image: str = ""          # eigenes Bild; leer = grauer Hintergrund mit durchgestrichener Kamera
    offline_text: str = "Vorübergehend außer Betrieb\nStand: {date}, {time} Uhr"
    offline_after: int = 1           # nach so vielen fehlgeschlagenen Intervall-Aufnahmen in Folge
    upload: bool = True              # an [upload] senden (falls konfiguriert)
    upload_name: str = ""            # Dateiname auf dem Server; leer = [upload] name


@dataclass
class UploadConfig:
    host: str
    user: str
    port: int = 22
    password: str = ""               # besser über Umgebungsvariable (password_env, Standard SFTP_PASSWORD)
    key: str = ""                    # privater SSH-Schlüssel (OpenSSH-Format), z. B. /var/lib/tapo-webcam/.ssh/sftp_ed25519
    dir: str = "."                   # Zielverzeichnis auf dem Server
    name: str = "{camera}.jpg"       # Dateiname auf dem Server ({camera} = Kameraname)
    known_hosts: str = ""            # leer = known_hosts_sftp neben cloud_session_file
    timeout: float = 30.0


@dataclass
class Config:
    output_dir: Path = Path("./images")
    retention_days: int = 14         # 0 = nie löschen
    keep_history: bool = True        # False = nur latest.jpg pflegen
    listen: str = "0.0.0.0"
    port: int = 8099
    token: str | None = None         # optionaler API-Token (?token=… oder Header X-Token)
    cloud_session_file: str = "cloud_session.json"   # von `cloud-login` angelegt
    cameras: list[CameraConfig] = field(default_factory=list)
    upload: UploadConfig | None = None


_DAYS = ["mo", "di", "mi", "do", "fr", "sa", "so"]


def parse_hours(spec: str, cam: str = "") -> tuple[int, int] | None:
    """"08:00-19:00" -> (480, 1140) Minuten seit Mitternacht; über Mitternacht erlaubt ("22:00-06:00")."""
    if not spec:
        return None
    try:
        a, b = [x.strip() for x in spec.split("-")]
        to_min = lambda t: int(t.split(":")[0]) * 60 + int((t.split(":") + ["0"])[1])
        start, end = to_min(a), to_min(b)
        assert 0 <= start < 1440 and 0 <= end <= 1440 and start != end
        return start, end
    except Exception:  # noqa: BLE001
        raise SystemExit(f"Kamera '{cam}': active_hours '{spec}' ungültig, Format z. B. \"08:00-19:00\".")


def parse_days(spec: str, cam: str = "") -> set[int] | None:
    """"Mo-Fr" / "Sa,So" / "Mo,Mi,Fr" -> {0,…} (0 = Montag)."""
    if not spec:
        return None
    out: set[int] = set()
    try:
        for part in spec.lower().replace(" ", "").split(","):
            if "-" in part:
                a, b = part.split("-")
                i, j = _DAYS.index(a[:2]), _DAYS.index(b[:2])
                out.update(range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1)))
            else:
                out.add(_DAYS.index(part[:2]))
        return out
    except ValueError:
        raise SystemExit(f"Kamera '{cam}': active_days '{spec}' ungültig, z. B. \"Mo-Fr\" oder \"Sa,So\".")


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
    """Beginn des nächsten Aufnahme-Zeitfensters (minutengenau, nach `now`), None = immer/nie aktiv."""
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)
    t = now + dt.timedelta(minutes=1)
    seen_inactive = not is_active(c, now)       # läuft gerade ein Fenster, erst dessen Ende abwarten
    for _ in range(8 * 1440):
        if is_active(c, t):
            if seen_inactive:
                return t
        else:
            seen_inactive = True
        t += dt.timedelta(minutes=1)
    return None


def next_capture_after(c: "CameraConfig", t: dt.datetime) -> dt.datetime:
    """t, oder – falls t außerhalb des Zeitfensters liegt – dessen nächster Beginn."""
    return t if is_active(c, t) else (next_active(c, t) or t)


def fmt_when(t: dt.datetime | None, now: dt.datetime | None = None) -> str:
    """'09:00', 'morgen 09:00' oder 'Mo 09:00'."""
    if t is None:
        return "?"
    now = now or dt.datetime.now()
    days = (t.date() - now.date()).days
    if days == 0:
        return t.strftime("%H:%M")
    if days == 1:
        return "morgen " + t.strftime("%H:%M")
    return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][t.weekday()] + " " + t.strftime("%H:%M")


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
    """Platzhalterbild 'night' oder 'offline' als JPEG."""
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
    u = min(w, h * 16 / 9) / 100                 # Maßeinheit: 1 % der (16:9-)Breite
    lines = [ln for ln in text.split("\n")] if text else []

    if not path:                                 # Symbol über dem Text
        cx, cy = w / 2, h * (0.30 if lines else 0.5)
        if night:
            r = 7 * u
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(242, 228, 170))
            o = 0.55 * r
            d.ellipse((cx - r + o, cy - r - 0.25 * o, cx + r + o, cy + r - 0.25 * o), fill=bg)
            for sx, sy, sr in ((-22, -6, .5), (18, -9, .4), (27, 4, .35), (-30, 7, .4), (-12, -13, .3), (13, -15, .3)):
                x, y, rr = cx + sx * u, cy + sy * u, sr * u
                d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(220, 225, 240))
        else:                                    # durchgestrichene Kamera
            fg = (230, 230, 230)
            bw, bh = 18 * u, 11.5 * u
            x0, y0 = cx - bw / 2, cy - bh / 2 + 1.0 * u
            my = y0 + bh / 2
            d.rounded_rectangle((x0 + 2.5 * u, y0 - 2.2 * u, x0 + 7.5 * u, y0 + u), radius=0.7 * u, fill=fg)  # Sucher
            d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=1.8 * u, fill=fg)                          # Gehäuse
            r = 3.8 * u
            d.ellipse((cx - r, my - r, cx + r, my + r), fill=bg)                                               # Objektiv
            r2 = 2.1 * u
            d.ellipse((cx - r2, my - r2, cx + r2, my + r2), fill=fg)
            d.ellipse((x0 + bw - 3.4 * u, y0 + 1.5 * u, x0 + bw - 1.7 * u, y0 + 3.2 * u), fill=bg)            # Blitz
            a, b = (cx - 11.5 * u, my - 8.5 * u), (cx + 11.5 * u, my + 8.5 * u)
            d.line((a, b), fill=bg, width=round(3.8 * u))                                                  # Spalt
            d.line((a, b), fill=(225, 60, 50), width=round(1.6 * u))                                       # Strich

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

    foot = c.name if "{time}" in (c.night_text if night else c.offline_text) else \
        f"{c.name} · Stand {dt.datetime.now():%d.%m.%Y %H:%M}"
    f = _font(max(10, round(1.9 * u)))
    l, t, r, b = d.textbbox((0, 0), foot, font=f, stroke_width=1)
    d.text((1.5 * u - l, h - 1.5 * u - b), foot, font=f, fill=(190, 190, 190), stroke_width=1, stroke_fill=(0, 0, 0))
    out = BytesIO()
    im.save(out, "JPEG", quality=max(1, min(95, c.output_quality)), optimize=True)
    return out.getvalue()


def postprocess(img: bytes, c: "CameraConfig") -> bytes:
    """Verkleinert/weichzeichnet das Bild und entfernt Metadaten. Original wird verworfen."""
    if not c.max_width and not c.blur and not c.face_blur:
        return img
    from io import BytesIO
    from PIL import Image, ImageFilter
    im = Image.open(BytesIO(img))
    im = im.convert("RGB")                      # verwirft auch EXIF/Metadaten
    if c.face_blur:                             # auf dem Original, damit auch kleinere Gesichter gefunden werden
        try:
            import tapo_faces
            t0 = time.time()
            faces = tapo_faces.detect(im, c.face_threshold)
            if faces:
                im = tapo_faces.anonymize(im, faces, c.face_margin)
                log.info("[%s] %d Gesicht(er) unkenntlich gemacht (%.1f s)", c.name, len(faces), time.time() - t0)
        except Exception as e:  # noqa: BLE001
            # lieber ein unbrauchbares als ein ungeschütztes Bild
            log.error("[%s] Gesichtserkennung fehlgeschlagen (%r) – ganzes Bild wird weichgezeichnet", c.name, e)
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
    """Sucht Ladestand (%) und Ladezustand in der Antwort von getBatteryStatus."""
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
    """need_upload=False: unvollständiges [upload] (z. B. Passwort nur im env des Dienstes) ist kein Fehler,
    der Upload ist dann einfach aus."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cams = []
    defaults = raw.pop("defaults", {})       # gilt für alle Kameras, einzeln überschreibbar
    for c in raw.pop("camera", []):
        c = {**defaults, **c}
        # Passwort darf auch aus Umgebungsvariable kommen (z. B. Docker-Secret)
        pw = c.get("password") or os.environ.get(c.get("password_env", "TAPO_PASSWORD"), "")
        c.pop("password_env", None)
        c["password"] = pw
        cams.append(CameraConfig(**c))
    if not cams:
        raise SystemExit("Keine [[camera]] in der Konfiguration gefunden.")
    for c in cams:
        if c.connection not in ("local", "cloud"):
            raise SystemExit(f"Kamera '{c.name}': connection muss local|cloud sein.")
        if c.connection == "local" and not (c.host and c.password):
            raise SystemExit(f"Kamera '{c.name}': host und password nötig (connection=local).")
        parse_hours(c.active_hours, c.name)
        if c.face_blur:
            import tapo_faces
            problem = tapo_faces.check_available()
            if problem:
                raise SystemExit(f"Kamera '{c.name}': face_blur = true, aber {problem}.")
        parse_days(c.active_days, c.name)
        if c.connection == "cloud" and not c.cloud_device:
            raise SystemExit(f"Kamera '{c.name}': cloud_device nötig (Name aus `cloud-login`).")
        if c.mode not in ("auto", "snapshot", "stream"):
            raise SystemExit(f"Kamera '{c.name}': mode muss auto|snapshot|stream sein.")
        for key in ("night_image", "offline_image"):
            p = getattr(c, key)
            if p and not os.access(p, os.R_OK):
                raise SystemExit(f"Kamera '{c.name}': {key} '{p}' nicht lesbar (Rechte für den Dienstbenutzer?).")
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
            problem = "[upload]: host, user und key oder password (bzw. SFTP_PASSWORD) nötig."
        elif upload.key and not os.access(upload.key, os.R_OK):
            problem = f"[upload]: Schlüssel '{upload.key}' nicht lesbar."
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
# SFTP-Upload
# --------------------------------------------------------------------------- #
class Uploader:
    """Lädt Bilder per SFTP hoch (paramiko). Erst als .tmp, dann umbenannt -> nie halbe Dateien."""

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
            # erster Kontakt: Host-Schlüssel merken; ändert er sich später, bricht paramiko ab
            def missing_host_key(self, client, hostname, key):
                client.get_host_keys().add(hostname, key.get_name(), key)
                kh.parent.mkdir(parents=True, exist_ok=True)
                client.save_host_keys(str(kh))
                log.warning("SFTP: Host-Schlüssel von %s gespeichert (%s %s)", hostname, key.get_name(),
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
            except OSError:                    # Server ohne posix-rename: alte Datei erst löschen
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
                log.warning("[%s] SFTP-Upload nach %s fehlgeschlagen: %s", c.name, self.uc.host,
                            self.last_error[c.name])
                return False
            self.last_ok[c.name], self.last_error[c.name] = time.time(), None
            log.info("[%s] hochgeladen: %s:%s (%d KB, %.1f s)", c.name, self.uc.host, path,
                     len(data) // 1024, time.time() - t0)
            return True

    def upload_bg(self, c: CameraConfig, data: bytes):
        t = asyncio.create_task(self.upload(c, data))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)


# --------------------------------------------------------------------------- #
# Cloud-Zugang (ontapo) – eine Sitzung für alle Cloud-Kameras
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
            raise RuntimeError(f"Keine Cloud-Sitzung ({self.session_file}). Erst `cloud-login` ausführen.")
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
                tried.append(f"{region}: leer")
            except Unauthorized:
                raise
            except Exception as e:  # noqa: BLE001
                err = e
                tried.append(f"{region}: {e!r}")
        if not cams:
            raise RuntimeError(f"Keine Kameras gefunden ({'; '.join(tried)})") from err
        if tried:
            log.info("Cloud-Region %s verwendet (vorher probiert: %s)", self.data["region"], "; ".join(tried))
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
        log.info("Cloud-Token wird erneuert …")
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
                # 404 = Kamera (noch) nicht am Relay angemeldet, typisch beim Aufwachen von Akku-Kameras
                msg = str(e)
                if "HTTP 404" not in msg and "no video data" not in msg and "ended before" not in msg:
                    raise
                last = e
                if n < retries:
                    log.info("Kamera '%s' noch nicht bereit (%s) – neuer Versuch in %.0f s (%d/%d)",
                             device, msg, retry_wait, n + 1, retries)
                    await asyncio.sleep(retry_wait)
        raise RuntimeError(f"Kamera '{device}' nach {retries + 1} Versuchen nicht erreichbar: {last} – "
                           f"Akku leer, offline oder WLAN-Problem? Livebild in der Tapo-App prüfen.")

    async def battery(self, device: str, timeout: float = 15.0) -> dict:
        """getBatteryStatus über die Cloud. Nur direkt nach einem Bild aufrufen (Kamera wach)."""
        async with self.lock:
            if self.session is None:
                await self._open()
            dev_id = self.devices.get(device) or self.devices.get(device.lower())
            if not dev_id:
                raise RuntimeError(f"Kamera '{device}' nicht im Konto gefunden")
            return await asyncio.wait_for(
                self.session.request(dev_id, "getBatteryStatus", {"battery": {"name": "status"}}), timeout)

    async def _snapshot_once(self, device: str, timeout: int, seconds: float) -> bytes:
        from ontapo.errors import Unauthorized
        import tempfile
        async with self.lock:                  # Relay-Sitzungen nacheinander
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
                        raise RuntimeError(f"Kamera '{device}' nicht im Konto gefunden. Vorhanden: {names}")
                    dev = self.session.device(dev_id, name=device)
                    with tempfile.TemporaryDirectory() as td:
                        out = os.path.join(td, "snap.jpg")
                        await asyncio.wait_for(dev.snapshot(out, seconds=seconds), timeout=timeout + seconds)
                        return Path(out).read_bytes()
                except Unauthorized:
                    if attempt:
                        raise
                    await self._refresh()
        raise RuntimeError("unerreichbar")


# --------------------------------------------------------------------------- #
# Kamerazugriff
# --------------------------------------------------------------------------- #
class Camera:
    def __init__(self, cfg: CameraConfig, cloud: "CloudAccount | None" = None, state_dir: Path | None = None,
                 uploader: "Uploader | None" = None):
        self.cfg = cfg
        self.cloud = cloud
        self.uploader = uploader if cfg.upload else None
        self.placeholder: str | None = None   # gerade gezeigter Platzhalter ("night"/"offline")
        self.failures = 0                     # fehlgeschlagene Intervall-Aufnahmen in Folge
        self._tapo = None
        self._lock = asyncio.Lock()
        self.last_image: bytes | None = None
        self.last_time: float = 0.0
        self.last_error: str | None = None
        self.last_method: str | None = None
        self._snapshot_misses = 0            # zählt, wie oft das native Standbild leer blieb
        self.battery: int | None = None      # letzter Akkustand in %
        self.battery_charging: str | None = None
        self.battery_time: float = 0.0
        self.battery_task: asyncio.Task | None = None
        self._battery_warned = False
        self._pause_logged: float | None = None
        self._state_file = (state_dir / cfg.name / ".battery.json") if state_dir else None
        self._load_battery()

    # --- Akkuschutz -----------------------------------------------------------
    def _load_battery(self):
        try:
            d = json.loads(self._state_file.read_text())
            self.battery, self.battery_charging, self.battery_time = d["percent"], d.get("charging"), d["time"]
        except Exception:  # noqa: BLE001  (noch keine Messung / kein Zustand)
            pass

    def _save_battery(self):
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps({"percent": self.battery, "charging": self.battery_charging,
                                                    "time": self.battery_time}))
        except OSError as e:
            log.warning("[%s] Akkustand nicht gespeichert: %s", self.cfg.name, e)

    async def update_battery(self):
        """Akkustand lesen – nur direkt nach einer Aufnahme, solange die Kamera wach ist."""
        if not self.cloud:
            return
        try:
            res = await self.cloud.battery(self.cfg.cloud_device)
        except Exception as e:  # noqa: BLE001
            if not self._battery_warned:
                log.warning("[%s] Akkustand nicht abrufbar: %r", self.cfg.name, e)
                self._battery_warned = True
            return
        pct, chg = parse_battery(res)
        if pct is None:
            log.warning("[%s] Akkustand in der Antwort nicht gefunden: %s", self.cfg.name, json.dumps(res)[:300])
            return
        self._battery_warned = False
        self.battery, self.battery_charging, self.battery_time = pct, chg, time.time()
        log.info("[%s] Akku %d %%%s", self.cfg.name, pct, f" (Laden: {chg})" if chg else "")
        self._save_battery()

    def battery_paused_until(self) -> float | None:
        """Zeitpunkt, bis zu dem wegen schwachem Akku pausiert wird – sonst None."""
        c = self.cfg
        if not c.battery_min or self.battery is None or self.battery >= c.battery_min:
            return None
        until = self.battery_time + c.battery_pause_hours * 3600
        return until if time.time() < until else None

    @property
    def _snapshot_unsupported(self) -> bool:
        # nach 3 leeren Antworten in Folge direkt den Stream nehmen (spart Zeit und Akku)
        return self._snapshot_misses >= 3

    # pytapo arbeitet beim Login synchron (requests) -> in Thread auslagern
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
            log.info("[%s] Verbinde mit %s …", self.cfg.name, self.cfg.host)
            self._tapo = await asyncio.to_thread(self._connect_sync)
        return self._tapo

    def reset(self):
        self._tapo = None

    # --- Methode 1: natives Standbild (schnell, ~640x360) -------------------
    async def _grab_snapshot(self) -> bytes | None:
        from pytapo.media_stream.snapshot import getSnapshot
        tapo = await self.tapo()
        return await getSnapshot(tapo, timeout=self.cfg.timeout)

    # --- Methode 2: ein Frame aus dem Live-Stream (volle Auflösung) ---------
    async def _grab_stream_frame(self) -> bytes | None:
        from pytapo.media_stream._utils import StreamType
        tapo = await self.tapo()
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg nicht gefunden (für mode=stream nötig)")

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
                    # auf 188-Byte-TS-Pakete ausrichten
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
            raise RuntimeError("Zeitüberschreitung beim Lesen des Streams")
        finally:
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):  # Session-Abbau darf scheitern
                pass
        if ff.returncode != 0 or not out:
            raise RuntimeError(f"ffmpeg lieferte kein Bild: {err.decode(errors='ignore')[-300:]}")
        return out

    async def capture(self, force: bool = False) -> bytes:
        """Liefert ein JPEG. Nutzt Cache, damit Akku-Kameras nicht ständig geweckt werden."""
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
                    log.debug("[%s] cloud fehlgeschlagen", self.cfg.name, exc_info=True)
                    raise RuntimeError(self.last_error)
                if img[:2] != b"\xff\xd8":
                    self.last_error = "cloud: kein JPEG"
                    raise RuntimeError(self.last_error)
                img = await asyncio.to_thread(postprocess, img, self.cfg)
                self.last_image, self.last_time = img, time.time()
                self.last_error, self.last_method = None, "cloud"
                log.info("[%s] Bild aufgenommen (cloud, %d KB)", self.cfg.name, len(img) // 1024)
                if self.battery_task is None or self.battery_task.done():
                    self.battery_task = asyncio.create_task(self.update_battery())
                return img

            mode = self.cfg.mode
            methods = {"snapshot": ["snapshot"], "stream": ["stream"],
                       "auto": (["stream"] if self._snapshot_unsupported else ["snapshot", "stream"])}[mode]
            errors = []
            for attempt in range(2):           # 2. Runde mit frischem Login
                for m in methods:
                    try:
                        img = await (self._grab_snapshot() if m == "snapshot" else self._grab_stream_frame())
                        if img and img[:2] == b"\xff\xd8":
                            img = await asyncio.to_thread(postprocess, img, self.cfg)
                            self.last_image, self.last_time = img, time.time()
                            self.last_error, self.last_method = None, m
                            if m == "snapshot":
                                self._snapshot_misses = 0
                            log.info("[%s] Bild aufgenommen (%s, %d KB)", self.cfg.name, m, len(img) // 1024)
                            return img
                        errors.append(f"{m}: kein Bild")
                        if m == "snapshot":
                            self._snapshot_misses += 1
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{m}: {e!r}")
                        log.debug("[%s] %s fehlgeschlagen", self.cfg.name, m, exc_info=True)
                self.reset()
            self.last_error = "; ".join(errors)
            raise RuntimeError(self.last_error)


# --------------------------------------------------------------------------- #
# Ablage
# --------------------------------------------------------------------------- #
def save_image(cfg: Config, cam: str, img: bytes, tag: str | None = None) -> Path:
    now = dt.datetime.now()
    base = cfg.output_dir / cam
    base.mkdir(parents=True, exist_ok=True)
    latest = base / "latest.jpg"
    tmp = base / ".latest.tmp"
    tmp.write_bytes(img)
    os.replace(tmp, latest)                    # atomar, Webserver sieht nie halbe Dateien
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
    """Nur latest.jpg ersetzen (für Platzhalter – nicht ins Archiv)."""
    base = cfg.output_dir / cam
    base.mkdir(parents=True, exist_ok=True)
    tmp, latest = base / ".latest.tmp", base / "latest.jpg"
    tmp.write_bytes(img)
    os.replace(tmp, latest)
    return latest


async def store(cfg: Config, cam: "Camera", img: bytes, tag: str | None = None) -> Path:
    """Echtes Kamerabild speichern und (im Hintergrund) hochladen."""
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
    """True = jetzt keine Intervall-Aufnahme (Nacht oder Akkupause). Setzt ggf. einmal den Platzhalter."""
    c = cam.cfg
    now = dt.datetime.now()
    if not is_active(c, now):
        log.debug("[%s] außerhalb des Zeitfensters – keine Aufnahme", c.name)
        await show_placeholder(cfg, cam, "night", {"next": fmt_when(next_active(c, now), now)})
        return True
    if until := cam.battery_paused_until():
        if cam._pause_logged != until:
            log.warning("[%s] Akku %d %% < %d %% – keine Intervall-Aufnahmen bis %s", c.name, cam.battery,
                        c.battery_min, dt.datetime.fromtimestamp(until).strftime("%d.%m. %H:%M"))
            cam._pause_logged = until
        await show_placeholder(cfg, cam, "offline")     # Grund (Akku) wird öffentlich nicht genannt
        return True
    return False


async def show_placeholder(cfg: Config, cam: "Camera", kind: str, values: dict | None = None):
    """Platzhalter einmal pro Pause/Störung ablegen und hochladen ('night' | 'offline')."""
    c = cam.cfg
    if not c.placeholders or cam.placeholder == kind:
        return
    now = dt.datetime.now()
    values = {**(values or {}), "camera": c.name, "time": now.strftime("%H:%M"), "date": now.strftime("%d.%m.%Y")}
    try:
        img = await asyncio.to_thread(render_placeholder, c, kind, values, _image_size(cam.last_image))
        await asyncio.to_thread(save_latest, cfg, c.name, img)
    except Exception as e:  # noqa: BLE001
        log.error("[%s] Platzhalterbild '%s' fehlgeschlagen: %r", c.name, kind, e)
        cam.placeholder = kind             # nicht jede Runde neu versuchen
        return
    ok = await cam.uploader.upload(c, img) if cam.uploader else True
    if ok:                                 # sonst beim nächsten Mal erneut versuchen
        cam.placeholder = kind
        log.info("[%s] Platzhalterbild '%s' gesetzt", c.name, kind)


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
    for d in sorted(cfg.output_dir.rglob("*"), reverse=True):   # leere Ordner entfernen
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Dienst
# --------------------------------------------------------------------------- #
async def interval_loop(cfg: Config, cam: Camera):
    if cam.cfg.interval <= 0:
        return
    await check_pause(cfg, cam)            # nach dem Start nachts gleich den Platzhalter setzen
    while True:
        # an "runden" Zeiten ausrichten (z. B. :00, :05, :10 …)
        await asyncio.sleep(cam.cfg.interval - (time.time() % cam.cfg.interval))
        if await check_pause(cfg, cam):
            continue
        try:
            img = await cam.capture(force=True)
        except Exception as e:  # noqa: BLE001
            cam.failures += 1
            log.warning("[%s] Aufnahme fehlgeschlagen (%d× in Folge): %s", cam.cfg.name, cam.failures, e)
            if cam.failures >= max(1, cam.cfg.offline_after):
                await show_placeholder(cfg, cam, "offline")
            continue
        cam.failures = 0
        try:
            await store(cfg, cam, img)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] Speichern fehlgeschlagen: %s", cam.cfg.name, e)


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
            raise web.HTTPNotFound(text=f"unbekannte Kamera '{name}'")
        return cams[name]

    jpeg_headers = {"Cache-Control": "no-store"}

    async def snapshot(request):
        """Frisches Bild (bzw. Cache < cache_seconds). ?save=1 speichert es zusätzlich."""
        cam = get_cam(request)
        try:
            img = await cam.capture(force=request.query.get("force") == "1")
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] Aufnahme (snapshot.jpg) fehlgeschlagen: %s", cam.cfg.name, e)
            return web.json_response({"error": str(e)}, status=502)
        if request.query.get("save") == "1":
            await store(cfg, cam, img)
        return web.Response(body=img, content_type="image/jpeg", headers=jpeg_headers)

    async def latest(request):
        """Zuletzt gespeichertes Bild – weckt die Kamera NICHT (ideal fürs Dashboard)."""
        cam = get_cam(request)
        f = cfg.output_dir / cam.cfg.name / "latest.jpg"
        if not f.exists():
            return web.json_response({"error": "noch kein Bild"}, status=404)
        return web.FileResponse(f, headers=jpeg_headers)

    async def event_image(request):
        """Letztes Ereignisbild zu einem Tag, z. B. /eingang/event/klingel.jpg"""
        cam = get_cam(request)
        tag = "".join(ch for ch in request.match_info["tag"] if ch.isalnum() or ch in "-_")[:32]
        f = cfg.output_dir / cam.cfg.name / f"latest_{tag}.jpg"
        if not f.exists():
            return web.json_response({"error": "kein Ereignisbild"}, status=404)
        return web.FileResponse(f, headers=jpeg_headers)

    async def trigger(request):
        """Ereignis-Aufnahme (z. B. Klingel): nimmt frisch auf, speichert mit Tag, gibt JSON zurück."""
        cam = get_cam(request)
        tag = request.query.get("tag", "event")
        try:
            img = await cam.capture(force=True)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] Aufnahme (trigger) fehlgeschlagen: %s", cam.cfg.name, e)
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
                   "active_hours": c.cfg.active_hours or "immer", "active_now": is_active(c.cfg),
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
            f'<p><a href="/{n}/snapshot.jpg">frisch aufnehmen</a> · <a href="/status">Status</a></p>'
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
    log.info("HTTP-API auf http://%s:%d/", cfg.listen, cfg.port)
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
    """Platzhalterbilder als Vorschau ablegen; mit --upload Testupload – weckt keine Kamera."""
    now = dt.datetime.now()
    for c in cfg.cameras:
        nxt = next_active(c, now) if (c.active_hours or c.active_days) else now + dt.timedelta(hours=9)
        base_vals = {"camera": c.name, "time": now.strftime("%H:%M"), "date": now.strftime("%d.%m.%Y")}
        imgs = {
            "night": render_placeholder(c, "night", {**base_vals, "next": fmt_when(nxt, now)}),
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
                raise SystemExit("Kein [upload]-Abschnitt in der Konfiguration.")
            name = "tapo-webcam-test.jpg"
            ok = await up.upload(c, imgs["night"], name=name)
            print(f"{c.name}: Testupload {'ok' if ok else 'FEHLGESCHLAGEN'} -> "
                  f"{up.uc.host}:{up.uc.dir.rstrip('/')}/{name}" + ("" if ok else f"  ({up.last_error[c.name]})"))
            if not ok:
                sys.exit(1)


async def cmd_cloud_login(cfg: Config | None, session_file: str):
    """Interaktiver Login ins Tapo-Konto (inkl. E-Mail-Code), speichert die Sitzung."""
    import getpass
    from ontapo._crypto import APP_VERSION
    from tapo_cloud_auth import CloudLogin
    email = input("E-Mail des Tapo-Kontos: ").strip()
    pw = getpass.getpass("Passwort: ")
    loop = asyncio.get_running_loop()

    async def mfa():
        return (await loop.run_in_executor(None, input, "Code aus der E-Mail von TP-Link: ")).strip()

    cl = CloudLogin(email, pw, APP_VERSION)
    try:
        data = await cl.login(mfa)
    finally:
        await cl.close()
    acc = CloudAccount(session_file)
    acc.data = data
    acc._build()
    acc._save()
    print(f"Sitzung gespeichert in {session_file} (Rechte 600 – enthält Zugangstoken!)")
    try:
        cams = await acc._load_devices()
        print(f"Kameras im Konto (Region {acc.data['region']}):")
        for c in cams:
            print(f"  cloud_device = \"{c.name}\"   ({c.model}, id {c.device_id})")
    except Exception as e:  # noqa: BLE001
        print(f"Login ok, aber Kameraliste fehlgeschlagen: {e}")
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
                print(f"  Cloud-Fehler: {e}")
            continue
        cam = Camera(c)
        print(f"== {c.name} ({c.host}) ==")
        try:
            tapo = await cam.tapo()
            info = await asyncio.to_thread(tapo.getBasicInfo)
            bi = info.get("device_info", {}).get("basic_info", info)
            print(f"  Modell: {bi.get('device_model')}  Typ: {bi.get('device_type')}  "
                  f"Firmware: {bi.get('sw_version')}")
        except Exception as e:  # noqa: BLE001
            print(f"  Verbindung fehlgeschlagen: {e}")
            continue
        try:
            children = await asyncio.to_thread(tapo.getChildDevices)
            for ch in children or []:
                print(f"  Kindgerät: {ch.get('device_model')}  '{ch.get('alias')}'  "
                      f"child_id = \"{ch.get('device_id')}\"")
        except Exception:  # noqa: BLE001
            pass  # kein Hub


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
            print(f"{c.name}: Upload {'ok' if ok else 'fehlgeschlagen: ' + str(cam.uploader.last_error.get(c.name))}")
        if cam.battery_task:
            await cam.battery_task
            if cam.battery is not None:
                print(f"{c.name}: Akku {cam.battery} %" + (f" (Laden: {cam.battery_charging})"
                                                           if cam.battery_charging else ""))
    acc = CloudAccount._instance
    if acc is not None and acc.session is not None:
        await acc.session.aclose()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["discover", "snap", "serve", "cloud-login", "placeholders"])
    ap.add_argument("-c", "--config", default=os.environ.get("TAPO_WEBCAM_CONFIG", "config.toml"))
    ap.add_argument("-o", "--output", help="bei snap: Zieldatei (nur bei einer Kamera)")
    ap.add_argument("--upload", action="store_true",
                    help="bei placeholders: Nachtbild als tapo-webcam-test.jpg hochladen (Verbindungstest)")
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
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
