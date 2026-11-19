# Operations and troubleshooting

## Commands

All commands: `tapo_webcam.py <command> -c <config> [-v]`. With the systemd service run them as the
service user:

```bash
PY="sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py"
CFG=/etc/tapo-webcam/config.toml
```

| Command | Purpose | Wakes camera? |
|---|---|---|
| `$PY serve -c $CFG` | The service itself (interval + HTTP API) – normally via systemd | yes, at the interval |
| `$PY snap -c $CFG [-o file.jpg] [-v]` | Take one picture. Without `-o` it is archived and uploaded (if password/key are available). Shows the battery level. | yes |
| `$PY discover -c $CFG` | Check the connection; local: model, firmware, hub children; cloud: all cameras of the account | local: yes |
| `$PY cloud-login -c $CFG` | Sign in to the Tapo account (interactive: e-mail, password, code) | no |
| `$PY placeholders -c $CFG [--upload]` | Store placeholder previews; with `--upload` upload them as `tapo-webcam-test.jpg` | no |

`-v` enables debug output.

**Battery cameras:** every `snap` wakes the camera. Use test captures sparingly, never in loops.

## Service

```bash
sudo systemctl status tapo-webcam
sudo systemctl restart tapo-webcam
journalctl -fu tapo-webcam                                 # follow live
journalctl -u tapo-webcam --since today | grep -v "GET /"
journalctl -u tapo-webcam --since -3days | grep battery    # battery history
curl -s localhost:8099/status
```

## Log messages

| Message | Meaning |
|---|---|
| `HTTP API on http://0.0.0.0:8099/` | Service started |
| `[camera] picture taken (cloud, 97 KB)` | Successful capture (size after processing) |
| `[camera] 2 face(s) anonymised (2.4 s)` | Faces found and pixelated |
| `[camera] battery 77 % (charging: NO)` | Battery reading after the picture |
| `[camera] uploaded: host:path (97 KB, 1.2 s)` | SFTP upload successful |
| `[camera] placeholder 'night' set` | Night picture stored and uploaded |
| `[camera] placeholder 'offline' set` | "Out of service" picture stored and uploaded |
| `Camera '…' not ready yet (HTTP 404) – retrying in 5 s (1/3)` | Battery camera is still waking up – normal |
| `[camera] capture failed (2× in a row): …` | Camera/cloud/internet unreachable |
| `[camera] battery 25 % < 30 % – no interval captures until …` | Battery pause starts |
| `[camera] SFTP upload to … failed: …` | Web server unreachable or sign-in rejected |
| `SFTP: host key of … saved (…)` | First contact with the web server |
| `Refreshing cloud token …` | Automatic token refresh |
| `face detection failed … blurring the whole picture` | Fail-safe kicked in |

## Common errors

### The service does not start

`journalctl -u tapo-webcam -n 30` shows the cause, usually a configuration message:

| Message | Fix |
|---|---|
| `No [[camera]] found in the configuration.` | Add at least one `[[camera]]` block. |
| `Camera '…': cloud_device required` | Enter the name shown by `cloud-login`. |
| `invalid active_hours '…'` | Format `"09:00-19:00"`. |
| `CameraConfig.__init__() got an unexpected keyword argument '…'` | Typo in an option name, or a general option is placed below a section. |
| `[upload]: host, user and key or password … required` | Set a key or put `SFTP_PASSWORD` into `/etc/tapo-webcam/env`. |
| `face_blur = true, but …` | OpenCV/model missing → run `sudo bash install.sh` again. |
| `NumPy was built with baseline optimizations (X86_V2)` | CPU without x86-64-v2 → use the versions from `requirements.txt` or change the VM CPU type. |

### Cloud: camera unreachable

`Camera '…' unreachable after 4 attempts … battery empty, offline or Wi-Fi problem?`

1. Check the live view in the Tapo app – if it does not work there either, the problem is camera, Wi-Fi or battery.
2. Check the server's internet connection (`curl -I https://www.tplinkcloud.com`).
3. Increase `cloud_retries`/`cloud_retry_wait` if the camera wakes up slowly.
4. With `placeholders = true` the "out of service" picture appears automatically.

### Cloud: sign-in expired

`Token refresh failed … run cloud-login again` → run `cloud-login`, then
`sudo systemctl restart tapo-webcam`. Only needed if the session really is invalid.

### Upload fails

| Error | Cause |
|---|---|
| `AuthenticationException` | Wrong user/password, key not added at the web host |
| `NoValidConnectionsError` / timeout | Wrong host/port or server unreachable |
| `FileNotFoundError` / `No such file` | `dir` does not exist – create the folder or fix the path |
| `BadHostKeyException` | Host key changed – verify, then delete the line in `known_hosts_sftp` |
| `PermissionError` | No write permission in the target folder |

Test without a camera: see [Installation → SFTP upload](installation.md#set-up-the-sftp-upload).

### Pictures at night / no pictures at all

* Check `active_now` in `/status`. The server's time zone is what counts:
  `timedatectl`, if needed `sudo timedatectl set-timezone Europe/Berlin`.
* Is `battery_paused_until` set? Then a battery pause is running.

### Memory

With `face_blur`, processing a 4K picture briefly needs several hundred MB (peaks of about 850 MB
observed). Below 1 GB RAM disable `face_blur` or add swap.
