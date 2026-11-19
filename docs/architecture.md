# Architecture

## Overview

```
                ┌──────────────────────────── tapo_webcam.py serve ────────────────────────────┐
 Tapo camera    │                                                                              │
 (cloud relay ◄─┼─ CloudAccount ──┐                                                            │
  or LAN:8800)  │  (ontapo)       │    ┌──────────┐   ┌─────────────┐   ┌──────────────┐       │
                │                 ├──► │ Camera   │──►│ postprocess │──►│ store        │──┐    │
 Tapo camera  ◄─┼─ pytapo ────────┘    │ .capture │   │ faces,      │   │ latest.jpg + │  │    │
                │                      └────▲─────┘   │ resize      │   │ archive      │  │    │
                │                           │         └─────────────┘   └──────────────┘  │    │
                │  interval_loop ───────────┤                                             ▼    │
                │   (time window, battery,  │                                        Uploader ─┼──► web server (SFTP)
                │    placeholders)          │                                        (paramiko)│
                │  HTTP API (aiohttp) ──────┘   /latest.jpg /snapshot.jpg /trigger /status     │
                │  cleanup_loop (hourly)                                                       │
                └──────────────────────────────────────────────────────────────────────────────┘
```

## Modules

| File | Contents |
|---|---|
| `tapo_webcam.py` | Configuration (`CameraConfig`, `UploadConfig`, `Config`, `load_config`), texts (`TEXTS`), time windows (`is_active`, `next_active`), image processing (`postprocess`), placeholders (`render_placeholder`), cloud access (`CloudAccount`), SFTP (`Uploader`), camera access (`Camera`), storage (`save_image`, `save_latest`, `store`, `cleanup`), service (`interval_loop`, `check_pause`, `show_placeholder`, `build_app`, `serve`) and CLI (`main`). |
| `tapo_cloud_auth.py` | Sign-in to the Tapo cloud account like the app (incl. MFA code by e-mail, region detection) and token refresh. Logs without passwords/tokens. |
| `tapo_faces.py` | `detect()` – YuNet face detection at several scales with merging of overlapping hits; `anonymize()` – pixelation and blur; `check_available()` – checks OpenCV and the model. |

## Flow of an interval capture

1. `interval_loop` sleeps until the next multiple of `interval`.
2. `check_pause`:
   * outside the time window → night picture (once) → done;
   * battery pause active → "out of service" picture (once) → done.
3. `Camera.capture(force=True)`:
   * cloud: `CloudAccount.snapshot` opens the relay stream for `cloud_seconds`, with retries on
     HTTP 404; on an expired token `_refresh()` once and retry;
   * local: `snapshot` and/or `stream` (ffmpeg) via pytapo, on error sign in again once.
4. `postprocess` in a thread: faces, resize, blur, JPEG.
5. cloud: `update_battery()` in the background (camera still awake) → `.battery.json`.
6. Failure → `failures += 1`; from `offline_after` on → "out of service" picture (once).
7. Success → `store`: replace `latest.jpg` atomically, write to the archive, reset the placeholder
   state, start the upload in the background.

## Concurrency

* Everything runs in one asyncio event loop. Blocking work (image processing, file access, SFTP, pytapo
  login) runs via `asyncio.to_thread`.
* `Camera._lock` prevents parallel captures of the same camera; `CloudAccount.lock` serialises all cloud
  access; `Uploader.lock` serialises uploads.
* Face detection: the YuNet detector is loaded once and protected by a thread lock.

## State and files

| File | Purpose |
|---|---|
| `<output_dir>/<camera>/latest.jpg` | Last picture or placeholder |
| `<output_dir>/<camera>/YYYY/MM/DD/HHMMSS[_tag].jpg` | Archive |
| `<output_dir>/<camera>/latest_<tag>.jpg` | Last event picture per tag |
| `<output_dir>/<camera>/.battery.json` | Last battery reading (percent, charging state, time) |
| `<output_dir>/<camera>/placeholder_*.jpg` | Previews from the `placeholders` command |
| `cloud_session_file` | Cloud session with tokens (600) |
| `known_hosts_sftp` (next to `cloud_session_file`) | Host key of the SFTP server |

In memory (not persistent): last picture, failure counter, placeholder shown, upload status.

## Dependencies

| Package | Purpose |
|---|---|
| `pytapo` | Local TP-Link protocol |
| `ontapo` | Cloud relay (unofficial) |
| `aiohttp` | HTTP API (and cloud sign-in) |
| `pillow` | Image processing, placeholder pictures |
| `numpy`, `opencv-python-headless` | Face detection (versions capped because of x86-64-v2) |
| `paramiko` | SFTP |
| `ffmpeg` (system) | Frame from the local stream |
| `fonts-dejavu-core` (system) | Font for placeholder pictures |

## systemd hardening

`tapo-webcam.service` runs as its own user with `NoNewPrivileges`, `ProtectSystem=strict` (read-only
system), `ProtectHome`, `PrivateTmp` and write access only to `/var/lib/tapo-webcam`. Passwords come
from `EnvironmentFile=/etc/tapo-webcam/env`. `Restart=always` restarts the service 10 s after a crash.
