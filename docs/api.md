# HTTP API and ioBroker

The service provides a small HTTP API (default: port 8099, all interfaces). Without `/<camera>` in the
path the first camera from the configuration is used.

## Endpoints

| Method | Path | Effect | Wakes camera? |
|---|---|---|---|
| GET | `/` | Overview page with the last picture of each camera | no |
| GET | `/<camera>/latest.jpg` | Last stored picture (placeholders included) | **no** |
| GET | `/<camera>/snapshot.jpg` | Fresh picture; within `cache_seconds` the cached one. `?force=1` forces a capture, `?save=1` also stores it (archive + upload). | yes |
| GET/POST | `/<camera>/trigger?tag=doorbell` | Event capture, stored as `HHMMSS_doorbell.jpg` and `latest_doorbell.jpg`, uploaded. Response JSON; with `&format=jpeg` the picture itself. | yes |
| GET | `/<camera>/event/<tag>.jpg` | Last picture for this event tag | no |
| GET | `/status` | JSON status of all cameras | no |
| GET | `/health` | `{"ok": true}` – also without token | no |

On errors `snapshot.jpg` and `trigger` answer HTTP 502 with `{"error": "…"}`.

If `token` is set, every request (except `/health`) must contain `?token=…` or the header
`X-Token: …`, otherwise HTTP 401.

**Always use `latest.jpg` for dashboards** – every request to `snapshot.jpg` wakes a battery camera.

## `/status`

Example:

```json
{
  "parking": {
    "host": "Parking",
    "connection": "cloud",
    "mode": "auto",
    "interval": 900,
    "active_hours": "09:00-19:00",
    "active_now": true,
    "last_capture": "2026-11-19T10:15:09.569",
    "last_method": "cloud",
    "last_error": null,
    "face_blur": true,
    "battery": 77,
    "battery_charging": "NO",
    "battery_measured": "2026-11-19T10:15:09.875",
    "battery_min": 30,
    "battery_paused_until": null,
    "placeholder": null,
    "failures": 0,
    "upload": {
      "target": "ssh.example.com:html/webcam/webcam.jpg",
      "last_ok": "2026-11-19T10:15:10.738",
      "last_error": null
    }
  }
}
```

| Field | Meaning |
|---|---|
| `host` | IP (local) or `cloud_device` (cloud) |
| `active_now` | Is the current time inside the time window? |
| `last_capture` | Time of the last successful picture since start |
| `last_method` | `cloud`, `snapshot` or `stream` |
| `last_error` | Last capture error (cleared on success) |
| `battery`, `battery_charging`, `battery_measured` | Last battery reading (cloud only); `battery_charging` is the camera's raw value, e.g. `NO` |
| `battery_min` | Threshold, `null` = battery protection off |
| `battery_paused_until` | End of the current battery pause or `null` |
| `placeholder` | Placeholder currently shown: `night`, `offline` or `null` |
| `failures` | Failed interval captures in a row |
| `upload` | Target, last successful upload and last error; `null` without `[upload]` |

## Examples

```bash
curl -s http://server:8099/status | python3 -m json.tool
curl -o picture.jpg "http://server:8099/parking/latest.jpg"
curl -s "http://server:8099/parking/trigger?tag=doorbell"
```

## ioBroker

Create `iobroker/tapo-webcam.js` as a new script in the JavaScript adapter (≥ 7) and adjust the top:

| Constant | Meaning |
|---|---|
| `BASE_URL` | Address of the service, e.g. `http://192.168.1.20:8099` |
| `TOKEN` | API token, if set |
| `CAMERA` | Camera name from `config.toml` |
| `TRIGGERS` | States that trigger a capture (doorbell, motion sensor …) with a tag |
| `TELEGRAM_INSTANCE` | e.g. `telegram.0`, empty = do not send |

The script creates under `0_userdata.0.tapoWebcam.<camera>`: `snapshot` (button), `lastCapture`,
`lastEvent` (local file), `lastError`, `latestUrl`, `eventUrl`, `online`. It polls `/status` every
minute and takes a picture immediately on every trigger.

VIS/VIS-2: image widget with the URL `http://<service>:8099/<camera>/latest.jpg` and a 60 s refresh.

If the doorbell state comes from the ioBroker Tapo adapter it lives under `tapo.0.<device-id>…`; any
other state works just as well.

## Embedding in a website

With the SFTP upload the picture lives on the web server under a fixed name. To keep browsers from
caching it, append a timestamp and reload regularly:

```html
<img id="webcam" src="/webcam/webcam.jpg" alt="Webcam" style="max-width:100%">
<script>
  setInterval(() => {
    document.getElementById('webcam').src = '/webcam/webcam.jpg?t=' + Date.now();
  }, 60000);
</script>
```
