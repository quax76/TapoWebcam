# Features in detail

## Capture methods

### Cloud (`connection = "cloud"`)

For cameras at another location, without VPN and without a device on site. The service signs in to the
TP-Link account like the Tapo app and opens the live stream through the **cloud relay** for a few
seconds (`cloud_seconds`). The first complete frame is taken as JPEG.

* Library: [ontapo](https://pypi.org/project/ontapo/) (unofficial, experimental).
* Sign-in and token refresh: `tapo_cloud_auth.py` (app login incl. e-mail code, region detection
  `euw1`/`aps1`/`use1`). Expired tokens are refreshed automatically.
* Battery cameras sleep. On first contact the relay often answers HTTP 404 until the camera is awake –
  hence `cloud_retries` retries every `cloud_retry_wait` seconds.
* Time per picture: typically 8–15 s.
* All cloud access is serialised (one session for all cloud cameras).

### Local (`connection = "local"`)

On the same network the camera speaks an encrypted TP-Link protocol on port 8800, even without RTSP.
[pytapo](https://github.com/JurajNyiri/pytapo) implements it.

* `snapshot`: built-in still image, < 1 s, usually 640×360.
* `stream`: briefly open the stream, ffmpeg turns the first frame into a JPEG – full resolution.
* `auto`: snapshot first, stream if the result is empty. After 3 empty still images in a row straight to stream.
* On an error the service signs in again once and retries.

### Remote sites – alternatives to the cloud

| Way | What you need | Assessment |
|---|---|---|
| Cloud | nothing on site | Fastest to set up, but unofficial. |
| Small computer on site + Tailscale | Raspberry Pi (Zero 2 W is enough) in the camera's Wi-Fi | Most stable; `connection = "local"`, remote access via Tailscale without port forwarding. |
| Site-to-site VPN | e.g. WireGuard between two routers | Good if a suitable router is on site anyway. |

## Time window and interval

* Interval captures run at "round" times: with `interval = 900` at :00, :15, :30, :45.
* Outside `active_hours`/`active_days` nothing is captured; with `placeholders = true` the night picture
  is shown instead.
* `snapshot.jpg` and `trigger` **always** capture, even at night and during battery pauses – they are
  deliberate single requests.

## Image processing

Order for every picture (`postprocess`):

1. Open the JPEG, convert to RGB – all metadata (EXIF, GPS, camera info) is lost in the process.
2. `face_blur`: find faces on the **original** and pixelate them.
3. Resize to `max_width` (Lanczos).
4. `blur`: blur.
5. Save as JPEG with `output_quality`.

The original picture is never stored or delivered.

### Face detection (`face_blur`)

* Model: OpenCV **YuNet** (`models/face_detection_yunet_2023mar.onnx`, about 230 KB), runs on the CPU.
* The search runs at three sizes (640, 1280 and 1920 px width), so that large faces right in front of
  the camera are found as well as faces a few metres away (from about 80–100 px face width in a 4K
  picture). Overlapping hits are merged.
* Each face is pixelated with a margin (`face_margin`, a bit more on top for hair/forehead) to about
  6 blocks and blurred additionally.
* Duration: about 2–3 s per 4K picture on a simple server; memory use briefly several hundred MB.
* **Fail-safe:** if detection fails, the **whole** picture is blurred heavily.
* Limits: faces turned away, covered or very blurry can be missed. Licence plates are not detected –
  `max_width`/`blur` help there.
* Log: `[parking] 2 face(s) anonymised (2.4 s)`.

Guide values for a 4K camera: at 1280 px width people and cars are recognisable, faces and licence
plates at a distance hardly; at 640 px only the overall scene.

## Battery protection

* Only the **awake** camera reports its battery level (`getBatteryStatus`); the cloud's device list does
  not contain it. It is therefore queried in the background right after every cloud picture – the
  camera is never woken up just for that. Log: `[parking] battery 77 % (charging: NO)`.
* Before every interval capture the last measured value decides: if it is below `battery_min`, captures
  pause for `battery_pause_hours` from the time of the reading. Log:
  `battery 25 % < 30 % – no interval captures until 2026-11-19 18:30`.
* After the pause one picture with a new reading follows. If the battery is still too low the next pause
  starts – so the camera is woken at most once per pause length.
* The last value is kept in `<output_dir>/<camera>/.battery.json` and survives restarts.
* Publicly only the neutral "out of service" picture is visible (with `placeholders = true`).

## SFTP upload

Every real picture (interval, `trigger`, `snapshot.jpg?save=1`, `snap`) and every placeholder is
uploaded to the web server.

* Flow: connect → write the file as `.<name>.tmp` → mode 644 → move it over the old file with
  `posix-rename`. Visitors never see half a picture. Servers without posix-rename: delete the old file,
  then rename.
* A new connection per upload (no problem with a 15-minute interval). Uploads run in the background and
  do not delay the capture.
* **Host key:** on first contact the server's key is stored in `known_hosts_sftp` (logged with its
  fingerprint). If it changes later the upload fails (`BadHostKeyException`) – then check whether the
  host changed its key and delete the line from `known_hosts_sftp`.
* Errors appear in the log (`SFTP upload to … failed`) and under `/status` → `upload.last_error`. The
  next picture is tried normally.

Sign-in with key (recommended) or password – see [Installation](installation.md#set-up-the-sftp-upload).
With many web hosts you land in the root of your web space after login; `dir` is then the website's
folder relative to it, e.g. `mywebsite/webcam`.

## Placeholder pictures

With `placeholders = true` the webcam shows a clear notice instead of an outdated picture.

| Picture | When | Contents |
|---|---|---|
| **Night** | outside `active_hours`/`active_days` | moon and stars on dark blue, "It's night – Next picture at tomorrow 09:00" |
| **Out of service** | battery pause **or** `offline_after` failed interval captures in a row (internet, cloud, camera offline …) | crossed-out camera on grey, "Temporarily out of service – As of 2026-11-19, 09:00" |

* The reason for an outage is deliberately **not** shown – visitors learn nothing about battery or
  technology. Details appear only in the log and under `/status`.
* Each placeholder is generated **once per pause or outage**, stored as `latest.jpg` (not in the
  archive) and uploaded. The timestamp shows the start of the outage.
* The next real picture replaces the placeholder.
* If uploading the placeholder fails (e.g. because the server's own internet connection is down), it is
  retried on the next round.
* After a restart at night the night picture is set immediately.
* The picture size follows the last real picture, otherwise `max_width` in 16:9.
* `language = "de"` switches the default texts, the date format and words like "tomorrow" to German.
* Own backgrounds: `night_image`, `offline_image`. The text is drawn on top with a black outline,
  `night_text = ""` / `offline_text = ""` omits it.
* Font: DejaVu Sans (`fonts-dejavu-core`); if missing, Pillow's built-in font is used.

Render a preview without waking any camera:

```bash
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     placeholders -c /etc/tapo-webcam/config.toml
# -> <output_dir>/<camera>/placeholder_night.jpg and placeholder_offline.jpg
```

## Archive

* Storage: `<output_dir>/<camera>/YYYY/MM/DD/HHMMSS.jpg`, event pictures `HHMMSS_<tag>.jpg`.
* `latest.jpg` is replaced atomically (`.latest.tmp` first, then rename).
* An hourly cleanup deletes pictures older than `retention_days` and empty folders.
