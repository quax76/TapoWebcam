# Configuration

The configuration is a [TOML](https://toml.io) file, by default `/etc/tapo-webcam/config.toml`
(Docker: `/config/config.toml`, otherwise `-c` or the environment variable `TAPO_WEBCAM_CONFIG`).
A commented template is [`config.example.toml`](../config.example.toml).

After every change: `sudo systemctl restart tapo-webcam`. Errors in the configuration (unknown option,
invalid time window, missing file …) prevent the start and are shown in the journal.

## Structure

```toml
# 1. General settings (top level)
output_dir = "/var/lib/tapo-webcam/images"
...

# 2. Defaults for all cameras
[defaults]
active_hours = "09:00-19:00"
...

# 3. Optional: SFTP upload
[upload]
host = "ssh.example.com"
...

# 4. One or more cameras
[[camera]]
name = "parking"
...
```

Every camera option can be set under `[defaults]` for all cameras and overridden in a `[[camera]]`
block. **Mind the order:** general settings must come before the first `[section]`, otherwise TOML
assigns them to the preceding section.

## General settings

| Option | Default | Description |
|---|---|---|
| `output_dir` | `"./images"` | Storage for pictures. One subfolder per camera with `latest.jpg` and daily folders `YYYY/MM/DD/`. |
| `retention_days` | `14` | Archive pictures older than this are deleted hourly. `0` = never. `latest*.jpg` is always kept. |
| `keep_history` | `true` | `false` = no archive, only `latest.jpg` (and event pictures). |
| `listen` | `"0.0.0.0"` | Address of the HTTP API. `"127.0.0.1"` = local access only. |
| `port` | `8099` | Port of the HTTP API. |
| `token` | – | If set, the API requires `?token=…` or the header `X-Token` (except `/health`). |
| `cloud_session_file` | `"cloud_session.json"` | Session created by `cloud-login`. Contains access tokens – mode 600. |

## Camera options

### Connection

| Option | Default | Description |
|---|---|---|
| `name` | – (required) | Internal name, part of the URL and the folder. Only `a-z`, `0-9`, `-`, `_` recommended. |
| `connection` | `"local"` | `local` = in the LAN via the TP-Link protocol (pytapo); `cloud` = through the cloud relay (ontapo). |
| `cloud_device` | – | Cloud only, required: camera name as in the Tapo app (shown by `cloud-login`) or device_id. |
| `cloud_seconds` | `3.0` | Cloud only: how long the stream is opened per picture. Shorter saves battery; below 2 s often no picture arrives. |
| `cloud_retries` | `3` | Cloud only: retries while the camera is not at the relay yet (HTTP 404 while waking up). |
| `cloud_retry_wait` | `5.0` | Cloud only: seconds between retries. |
| `host` | – | Local only, required: IP of the camera or hub. |
| `user` | `"admin"` | Local only: user, stays `admin`. |
| `password` | – | Local only: password of the TP-Link account. Better via `password_env`. |
| `password_env` | `"TAPO_PASSWORD"` | Local only: environment variable holding the password (from `/etc/tapo-webcam/env`). |
| `child_id` | – | Local only, hub camera: device_id of the child device (shown by `discover`). |
| `mode` | `"auto"` | Local only: `snapshot` (built-in still image, fast, usually 640×360), `stream` (frame from the live stream, full resolution, needs ffmpeg), `auto` (snapshot first, otherwise stream; after 3 empty still images in a row straight to stream). |
| `quality` | `"HD"` | Local only, mode stream: `HD` or `VGA`. |
| `jpeg_quality` | `3` | Local only, mode stream: ffmpeg `-q:v` (2 = best, 31 = worst). |
| `timeout` | `20` | Seconds per capture attempt. 30 recommended for the cloud. |

### Schedule

| Option | Default | Description |
|---|---|---|
| `interval` | `300` | Seconds between interval captures; aligned to "round" times (with 900: :00, :15, :30, :45). `0` = no interval captures, on request only. |
| `active_hours` | `""` | Time window for interval captures, e.g. `"09:00-19:00"`; may span midnight (`"22:00-06:00"`). Empty = around the clock. The end is exclusive: with `19:00` the last capture is at 18:45. |
| `active_days` | `""` | Weekdays, e.g. `"Mon-Fri"`, `"Sat,Sun"`, `"Mon,Wed,Fri"`. German abbreviations (`"Mo-Fr"`, `"Sa,So"`) work too. Empty = every day. |
| `cache_seconds` | `15` | Requests to `snapshot.jpg` within this time get the last picture instead of waking the camera again. |

The server's time zone is what counts (`timedatectl`).

### Image processing and privacy

| Option | Default | Description |
|---|---|---|
| `max_width` | `0` | Shrink to this width (e.g. `1280`). `0` = original size. |
| `blur` | `0.0` | Gaussian blur radius in pixels, after resizing. `1`–`1.5` looks slightly soft. |
| `output_quality` | `80` | JPEG quality of the stored picture (1–95). |
| `face_blur` | `false` | Detect and pixelate faces (OpenCV YuNet, on the original before resizing). |
| `face_threshold` | `0.6` | Detection threshold 0–1. Lower finds more faces, but also more false positives. |
| `face_margin` | `0.4` | Extra pixelated margin as a fraction of the face size. |

### Battery protection (cloud only)

| Option | Default | Description |
|---|---|---|
| `battery_min` | `0` | If the last measured battery level is below this (in %), interval captures pause. `0` = off. |
| `battery_pause_hours` | `6.0` | Length of the pause. Then one picture with a new reading; if still too low, the next pause starts. |

### Placeholder pictures

| Option | Default | Description |
|---|---|---|
| `placeholders` | `false` | At night or during an outage/battery pause, store and upload a placeholder as `latest.jpg` once. |
| `language` | `"en"` | Language of the default texts, dates and words like "tomorrow": `"en"` or `"de"`. |
| `night_text` | depends on `language` | Text of the night picture. First line large. `""` = no text. en: `"It's night\nNext picture at {next}"`, de: `"Es ist Nacht\nNächstes Bild ab {next} Uhr"` |
| `night_image` | `""` | Own background picture (JPEG/PNG). Empty = moon on dark blue. |
| `offline_text` | depends on `language` | Text for outages and battery pauses. en: `"Temporarily out of service\nAs of {date}, {time}"`, de: `"Vorübergehend außer Betrieb\nStand: {date}, {time} Uhr"` |
| `offline_image` | `""` | Own background picture. Empty = crossed-out camera on grey. |
| `offline_after` | `1` | The offline picture appears after this many failed interval captures in a row. |

Variables in the texts: `{next}` (start of the next time window, e.g. `09:00`, `tomorrow 09:00`,
`Mon 09:00`), `{date}` (en `2026-11-19`, de `19.11.2026`), `{time}`, `{camera}`. Own pictures must be
readable by the service user, e.g. under `/etc/tapo-webcam/` with mode 644.

### Upload (per camera)

| Option | Default | Description |
|---|---|---|
| `upload` | `true` | Upload this camera's pictures if `[upload]` is configured. |
| `upload_name` | `""` | File name on the server for this camera; empty = `name` from `[upload]`. |

## Section `[upload]`

| Option | Default | Description |
|---|---|---|
| `host` | – (required) | SFTP server. |
| `user` | – (required) | User name. |
| `port` | `22` | SSH port. |
| `key` | `""` | Private SSH key (OpenSSH format, ed25519/RSA/ECDSA). Must be readable by the service user. |
| `password` | `""` | Password – better via `password_env`. |
| `password_env` | `"SFTP_PASSWORD"` | Environment variable holding the password. |
| `dir` | `"."` | Target directory, absolute or relative to the login directory. Must exist. |
| `name` | `"{camera}.jpg"` | File name on the server; `{camera}` is replaced by the camera name. |
| `known_hosts` | next to `cloud_session_file` | File for the server's host key (`known_hosts_sftp`). |
| `timeout` | `30.0` | Seconds for connection and transfer. |

Either `key` or a password is required. If both are missing the service does not start. Test commands
like `placeholders` (without `--upload`) or `snap` still run, just without uploading.

## Complete example

```toml
output_dir         = "/var/lib/tapo-webcam/images"
retention_days     = 14
keep_history       = true
listen             = "0.0.0.0"
port               = 8099
cloud_session_file = "/var/lib/tapo-webcam/cloud_session.json"

[defaults]
active_hours        = "09:00-19:00"
max_width           = 1280
blur                = 1
output_quality      = 80
face_blur           = true
battery_min         = 30
battery_pause_hours = 6
placeholders        = true
language            = "en"

[upload]
host = "ssh.example.com"
user = "webcam"
key  = "/var/lib/tapo-webcam/.ssh/sftp_ed25519"
dir  = "html/webcam"
name = "webcam.jpg"

[[camera]]
name          = "parking"
connection    = "cloud"
cloud_device  = "Parking"
cloud_seconds = 3
interval      = 900
timeout       = 30
```

## Environment variables

| Variable | Use |
|---|---|
| `TAPO_WEBCAM_CONFIG` | Path of the configuration if `-c` is not given. |
| `TAPO_PASSWORD` | Password of the TP-Link account for `connection = "local"` (name changeable via `password_env`). |
| `SFTP_PASSWORD` | Password for `[upload]` (name changeable via `password_env`). |

For the systemd service they live in `/etc/tapo-webcam/env` (`NAME=value`, one per line, no quotes).
Only systemd reads this file – commands started directly with `sudo -u tapo-webcam` do not see it.
