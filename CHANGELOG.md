# Änderungen

## 2026-11-19

### Neu
* **Platzhalterbild „Vorübergehend außer Betrieb“** (durchgestrichene Kamera, Zeitstempel) – bei
  Akkupause und wenn Intervall-Aufnahmen fehlschlagen (`offline_after`). Der Grund wird nicht angezeigt.
  Ersetzt das bisherige Akku-Bild (`battery_text`/`battery_image` → `offline_text`/`offline_image`).
* Ausführliche Dokumentation unter `docs/`.

### Geändert
* `placeholders`, `snap` und `discover` laufen auch ohne SFTP-Zugangsdaten (dann ohne Upload).
  Nur der Dienst und `placeholders --upload` verlangen sie.
* Docker/`install.sh`: Schrift `fonts-dejavu-core` für Platzhalterbilder; `SFTP_PASSWORD` in
  `docker-compose.yml`.

## 2026-11-17

### Neu
* **SFTP-Upload** (`[upload]`) jedes Bildes auf einen Webserver, atomar per `.tmp` + Umbenennen,
  Host-Schlüssel-Prüfung (TOFU).
* **Platzhalterbilder** (`placeholders`): Nachtbild mit nächster Aufnahmezeit.
* Befehl `placeholders [--upload]` für Vorschau und Verbindungstest ohne Kamera.
* **Akkuschutz** (`battery_min`, `battery_pause_hours`): Akkustand nach jedem Cloud-Bild, Pause bei
  schwachem Akku, Zustand übersteht Neustarts; Felder in `/status`.
* **Gesichtserkennung** (`face_blur`) mit OpenCV YuNet, mehrere Skalierungen, Fail-safe.
* Log-Ausgabe bei fehlgeschlagenen `snapshot.jpg`/`trigger`-Aufnahmen.

### Behoben
* `snap` schließt die Cloud-Sitzung sauber.
* numpy/OpenCV auf Versionen begrenzt, die ohne x86-64-v2 laufen.

## Erste Version

* Standbilder aus Tapo-Kameras lokal (pytapo) oder über die Cloud (ontapo), Intervall mit Zeitfenster,
  Verkleinern/Weichzeichnen, Archiv mit Aufbewahrungsfrist, HTTP-API, systemd/Docker, ioBroker-Skript.
