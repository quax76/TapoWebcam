# Changelog

## 2026-11-19 (2)

### Changed
* **Everything in English:** documentation, code comments, log messages, CLI output, example
  configuration. Documentation chapters renamed (`docs/configuration.md`, `docs/features.md`,
  `docs/operations.md`, `docs/architecture.md`, `docs/privacy.md`).
* New option **`language`** (`"en"` default, `"de"`) for the default placeholder texts, the date format
  and words like "tomorrow". `night_text`/`offline_text` default to the chosen language.
  **Existing German installations:** add `language = "de"` under `[defaults]` to keep the German texts.
* `active_days` accepts English (`Mon-Fri`, `Sat,Sun`) and German (`Mo-Fr`, `Sa,So`) abbreviations.
* `/status`: `active_hours` reports `always` instead of `immer` when no window is set.

## 2026-11-19

### Added
* **"Temporarily out of service" placeholder** (crossed-out camera, timestamp) – during a battery pause
  and when interval captures fail (`offline_after`). The reason is not shown.
  Replaces the previous battery picture (`battery_text`/`battery_image` → `offline_text`/`offline_image`).
* Detailed documentation under `docs/`.

### Changed
* `placeholders`, `snap` and `discover` also run without SFTP credentials (then without upload).
  Only the service and `placeholders --upload` require them.
* Docker/`install.sh`: font `fonts-dejavu-core` for placeholder pictures; `SFTP_PASSWORD` in
  `docker-compose.yml`.

## 2026-11-17

### Added
* **SFTP upload** (`[upload]`) of every picture to a web server, atomic via `.tmp` + rename, host key
  verification (TOFU).
* **Placeholder pictures** (`placeholders`): night picture with the next capture time.
* Command `placeholders [--upload]` for previews and connection tests without a camera.
* **Battery protection** (`battery_min`, `battery_pause_hours`): battery level after every cloud picture,
  pause on low battery, state survives restarts; fields in `/status`.
* **Face detection** (`face_blur`) with OpenCV YuNet, several scales, fail-safe.
* Log output for failed `snapshot.jpg`/`trigger` captures.

### Fixed
* `snap` closes the cloud session cleanly.
* numpy/OpenCV capped to versions that run without x86-64-v2.

## First version

* Still pictures from Tapo cameras locally (pytapo) or through the cloud (ontapo), interval with time
  window, resizing/blurring, archive with retention, HTTP API, systemd/Docker, ioBroker script.
