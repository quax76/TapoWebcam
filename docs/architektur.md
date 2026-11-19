# Architektur

## Überblick

```
                ┌──────────────────────────── tapo_webcam.py serve ────────────────────────────┐
 Tapo-Kamera    │                                                                              │
 (Cloud-Relay ◄─┼─ CloudAccount ──┐                                                            │
  oder LAN:8800)│  (ontapo)       │    ┌──────────┐   ┌─────────────┐   ┌──────────────┐       │
                │                 ├──► │ Camera   │──►│ postprocess │──►│ store        │──┐    │
 Tapo-Kamera  ◄─┼─ pytapo ────────┘    │ .capture │   │ Gesichter,  │   │ latest.jpg + │  │    │
                │                      └────▲─────┘   │ verkleinern │   │ Archiv       │  │    │
                │                           │         └─────────────┘   └──────────────┘  │    │
                │  interval_loop ───────────┤                                             ▼    │
                │   (Zeitfenster, Akku,     │                                        Uploader ─┼──► Webserver (SFTP)
                │    Platzhalter)           │                                        (paramiko)│
                │  HTTP-API (aiohttp) ──────┘   /latest.jpg /snapshot.jpg /trigger /status     │
                │  cleanup_loop (stündlich)                                                    │
                └──────────────────────────────────────────────────────────────────────────────┘
```

## Module

| Datei | Inhalt |
|---|---|
| `tapo_webcam.py` | Konfiguration (`CameraConfig`, `UploadConfig`, `Config`, `load_config`), Zeitfenster (`is_active`, `next_active`), Bildbearbeitung (`postprocess`), Platzhalter (`render_placeholder`), Cloud-Zugang (`CloudAccount`), SFTP (`Uploader`), Kamerazugriff (`Camera`), Ablage (`save_image`, `save_latest`, `store`, `cleanup`), Dienst (`interval_loop`, `check_pause`, `show_placeholder`, `build_app`, `serve`) und CLI (`main`). |
| `tapo_cloud_auth.py` | Anmeldung am Tapo-Cloud-Konto wie die App (inkl. MFA-Code per E-Mail, Regionserkennung) und Token-Erneuerung. Protokolliert ohne Passwörter/Tokens. |
| `tapo_faces.py` | `detect()` – YuNet-Gesichtserkennung in mehreren Skalierungen mit Zusammenführung überlappender Treffer; `anonymize()` – Verpixeln und Weichzeichnen; `check_available()` – prüft OpenCV und Modell. |

## Ablauf einer Intervall-Aufnahme

1. `interval_loop` schläft bis zum nächsten Vielfachen von `interval`.
2. `check_pause`:
   * außerhalb des Zeitfensters → Nachtbild (einmal) → fertig;
   * Akkupause aktiv → „außer Betrieb“-Bild (einmal) → fertig.
3. `Camera.capture(force=True)`:
   * cloud: `CloudAccount.snapshot` öffnet den Relay-Stream für `cloud_seconds`, mit Wiederholungen bei
     HTTP 404; bei abgelaufenem Token einmal `_refresh()` und neu versuchen;
   * local: `snapshot` und/oder `stream` (ffmpeg) über pytapo, bei Fehler einmal neu anmelden.
4. `postprocess` im Thread: Gesichter, Verkleinern, Weichzeichnen, JPEG.
5. cloud: im Hintergrund `update_battery()` (Kamera ist noch wach) → `.battery.json`.
6. Fehlschlag → `failures += 1`; ab `offline_after` → „außer Betrieb“-Bild (einmal).
7. Erfolg → `store`: `latest.jpg` atomar ersetzen, ins Archiv schreiben, Platzhalterstatus zurücksetzen,
   Upload im Hintergrund starten.

## Nebenläufigkeit

* Alles läuft in einer asyncio-Ereignisschleife. Blockierende Arbeit (Bildbearbeitung, Dateizugriffe,
  SFTP, pytapo-Login) läuft über `asyncio.to_thread`.
* `Camera._lock` verhindert parallele Aufnahmen derselben Kamera; `CloudAccount.lock` serialisiert alle
  Cloud-Zugriffe; `Uploader.lock` serialisiert Uploads.
* Gesichtserkennung: der YuNet-Detektor wird einmal geladen und mit einem Thread-Lock geschützt.

## Zustände und Dateien

| Datei | Zweck |
|---|---|
| `<output_dir>/<kamera>/latest.jpg` | Letztes Bild oder Platzhalter |
| `<output_dir>/<kamera>/JJJJ/MM/TT/HHMMSS[_tag].jpg` | Archiv |
| `<output_dir>/<kamera>/latest_<tag>.jpg` | Letztes Ereignisbild pro Tag |
| `<output_dir>/<kamera>/.battery.json` | Letzte Akkumessung (Prozent, Ladezustand, Zeit) |
| `<output_dir>/<kamera>/placeholder_*.jpg` | Vorschau aus dem Befehl `placeholders` |
| `cloud_session_file` | Cloud-Sitzung mit Tokens (600) |
| `known_hosts_sftp` (neben `cloud_session_file`) | Host-Schlüssel des SFTP-Servers |

Im Speicher (nicht persistent): letztes Bild, Fehlerzähler, gezeigter Platzhalter, Upload-Status.

## Abhängigkeiten

| Paket | Zweck |
|---|---|
| `pytapo` | Lokales TP-Link-Protokoll |
| `ontapo` | Cloud-Relay (inoffiziell) |
| `aiohttp` | HTTP-API (und Cloud-Anmeldung) |
| `pillow` | Bildbearbeitung, Platzhalterbilder |
| `numpy`, `opencv-python-headless` | Gesichtserkennung (Versionen begrenzt wegen x86-64-v2) |
| `paramiko` | SFTP |
| `ffmpeg` (System) | Frame aus dem lokalen Stream |
| `fonts-dejavu-core` (System) | Schrift für Platzhalterbilder |

## systemd-Härtung

`tapo-webcam.service` läuft als eigener Benutzer mit `NoNewPrivileges`, `ProtectSystem=strict`
(System schreibgeschützt), `ProtectHome`, `PrivateTmp` und Schreibrecht nur auf `/var/lib/tapo-webcam`.
Passwörter kommen aus `EnvironmentFile=/etc/tapo-webcam/env`. `Restart=always` startet den Dienst nach
Abstürzen nach 10 s neu.
