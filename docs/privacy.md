# Privacy and security

## What the tool does for privacy

* **No original:** the camera's picture is only processed in memory. Only the processed version is
  stored, delivered and uploaded.
* **Metadata stripped:** EXIF, GPS and camera info are lost during conversion.
* **Faces pixelated** (`face_blur`), with a fail-safe: if detection fails the whole picture is blurred.
* **Reduced resolution** (`max_width`) and optional blur (`blur`).
* **Time window:** no automatic captures outside `active_hours`/`active_days`.
* **Neutral outage notice:** during an outage or with a low battery the webcam only shows "Temporarily
  out of service" – without technical details.
* **Limited retention:** `retention_days` deletes the archive automatically; `keep_history = false`
  stores no archive at all.

## Limits

No automatic detection is complete. Faces turned away, covered or blurry can be missed; licence plates,
clothing or posture remain recognisable. Framing, resolution and camera position are the most effective
levers.

## Legal (not legal advice)

If you run a public webcam you are responsible for its lawfulness. Typical points in the EU:

* People must not be identifiable, or there must be a legal basis (GDPR Art. 6).
* Public space (streets, sidewalks, neighbouring property) should be avoided as far as possible.
* Signs on site if people can be captured.
* In clubs/associations: a resolution or information of the members, a note in the website's privacy
  policy.

## Secrets

| Secret | Location | Protection |
|---|---|---|
| Cloud session (Tapo account token) | `cloud_session_file` | mode 600, service user only. Grants **full access** to the Tapo account. |
| TP-Link account password (local) | `/etc/tapo-webcam/env` | root, 600; read by systemd only |
| SFTP password | `/etc/tapo-webcam/env` | as above |
| SFTP key | e.g. `/var/lib/tapo-webcam/.ssh/sftp_ed25519` | 600, service user only |
| API token | `config.toml` | 640, group tapo-webcam |

None of these belong in a Git repository. `.gitignore` excludes `config.toml`, `.env`,
`cloud_session.json` and pictures.

The cloud sign-in module logs requests only with redacted fields (`token`, `password` …).

## Hardening

* Do **not** expose the HTTP API to the internet. It has no TLS; `token` only protects against casual
  access in the LAN. The SFTP upload is meant for the public webcam. If the API is only needed locally:
  `listen = "127.0.0.1"`.
* **SFTP:** a dedicated account at the web host with access only to the webcam folder, if possible; key
  instead of password. The host key is stored on first contact and verified afterwards.
* **systemd:** the service runs unprivileged with a read-only system (see
  [Architecture](architecture.md#systemd-hardening)).
* **Updates:** update `pytapo`/`ontapo` regularly (`sudo bash install.sh` installs the latest allowed
  versions).
