# Konfiguration

Die Konfiguration ist eine [TOML](https://toml.io)-Datei, standardmäßig
`/etc/tapo-webcam/config.toml` (Docker: `/config/config.toml`, sonst `-c` oder die Umgebungsvariable
`TAPO_WEBCAM_CONFIG`). Eine kommentierte Vorlage ist [`config.example.toml`](../config.example.toml).

Nach jeder Änderung: `sudo systemctl restart tapo-webcam`. Fehler in der Konfiguration (unbekannte
Option, ungültiges Zeitfenster, fehlende Datei …) verhindern den Start und stehen im Journal.

## Aufbau

```toml
# 1. Allgemeine Einstellungen (oberste Ebene)
output_dir = "/var/lib/tapo-webcam/images"
...

# 2. Vorgaben für alle Kameras
[defaults]
active_hours = "09:00-19:00"
...

# 3. Optional: SFTP-Upload
[upload]
host = "ssh.example.de"
...

# 4. Eine oder mehrere Kameras
[[camera]]
name = "parkplatz"
...
```

Jede Kameraoption kann unter `[defaults]` für alle Kameras gesetzt und in einem `[[camera]]`-Block
überschrieben werden. **Reihenfolge beachten:** Allgemeine Einstellungen müssen vor dem ersten
`[abschnitt]` stehen, sonst ordnet TOML sie dem vorherigen Abschnitt zu.

## Allgemeine Einstellungen

| Option | Standard | Beschreibung |
|---|---|---|
| `output_dir` | `"./images"` | Ablage für Bilder. Pro Kamera ein Unterordner mit `latest.jpg` und Tagesordnern `JJJJ/MM/TT/`. |
| `retention_days` | `14` | Archivbilder älter als so viele Tage werden stündlich gelöscht. `0` = nie. `latest*.jpg` bleibt immer. |
| `keep_history` | `true` | `false` = kein Archiv, nur `latest.jpg` (und Ereignisbilder). |
| `listen` | `"0.0.0.0"` | Adresse der HTTP-API. `"127.0.0.1"` = nur lokal erreichbar. |
| `port` | `8099` | Port der HTTP-API. |
| `token` | – | Wenn gesetzt, verlangt die API `?token=…` oder den Header `X-Token` (außer `/health`). |
| `cloud_session_file` | `"cloud_session.json"` | Von `cloud-login` angelegte Sitzung. Enthält Zugangstoken – Rechte 600. |

## Kameraoptionen

### Verbindung

| Option | Standard | Beschreibung |
|---|---|---|
| `name` | – (Pflicht) | Interner Name, Teil der URL und des Ordners. Nur `a-z`, `0-9`, `-`, `_` empfohlen. |
| `connection` | `"local"` | `local` = im LAN über das TP-Link-Protokoll (pytapo); `cloud` = über das Cloud-Relay (ontapo). |
| `cloud_device` | – | Nur cloud, Pflicht: Kameraname wie in der Tapo-App (zeigt `cloud-login` an) oder device_id. |
| `cloud_seconds` | `3.0` | Nur cloud: so lange wird der Stream für ein Bild geöffnet. Kürzer schont den Akku; unter 2 s kommt oft kein Bild. |
| `cloud_retries` | `3` | Nur cloud: Wiederholungen, wenn die Kamera noch nicht am Relay ist (HTTP 404 beim Aufwachen). |
| `cloud_retry_wait` | `5.0` | Nur cloud: Sekunden zwischen den Wiederholungen. |
| `host` | – | Nur local, Pflicht: IP der Kamera bzw. des Hubs. |
| `user` | `"admin"` | Nur local: Benutzer, bleibt `admin`. |
| `password` | – | Nur local: Passwort des TP-Link-Kontos. Besser über `password_env`. |
| `password_env` | `"TAPO_PASSWORD"` | Nur local: Umgebungsvariable mit dem Passwort (aus `/etc/tapo-webcam/env`). |
| `child_id` | – | Nur local, Kamera am Hub: device_id des Kindgeräts (zeigt `discover` an). |
| `mode` | `"auto"` | Nur local: `snapshot` (eingebautes Standbild, schnell, meist 640×360), `stream` (Frame aus dem Live-Stream, volle Auflösung, braucht ffmpeg), `auto` (erst snapshot, sonst stream; nach 3 leeren Standbildern direkt stream). |
| `quality` | `"HD"` | Nur local, mode stream: `HD` oder `VGA`. |
| `jpeg_quality` | `3` | Nur local, mode stream: ffmpeg `-q:v` (2 = beste, 31 = schlechteste). |
| `timeout` | `20` | Sekunden pro Aufnahmeversuch. Für die Cloud 30 empfohlen. |

### Zeitplan

| Option | Standard | Beschreibung |
|---|---|---|
| `interval` | `300` | Sekunden zwischen Intervall-Aufnahmen; ausgerichtet auf „runde“ Zeiten (bei 900: :00, :15, :30, :45). `0` = keine Intervall-Aufnahmen, nur auf Anfrage. |
| `active_hours` | `""` | Zeitfenster für Intervall-Aufnahmen, z. B. `"09:00-19:00"`; über Mitternacht erlaubt (`"22:00-06:00"`). Leer = rund um die Uhr. Ende ist exklusiv: bei `19:00` ist 18:45 die letzte Aufnahme. |
| `active_days` | `""` | Wochentage, z. B. `"Mo-Fr"`, `"Sa,So"`, `"Mo,Mi,Fr"`. Leer = jeden Tag. |
| `cache_seconds` | `15` | Anfragen an `snapshot.jpg` innerhalb dieser Zeit bekommen das letzte Bild, statt die Kamera erneut zu wecken. |

Maßgeblich ist die Zeitzone des Servers (`timedatectl`).

### Bildbearbeitung und Datenschutz

| Option | Standard | Beschreibung |
|---|---|---|
| `max_width` | `0` | Auf diese Breite verkleinern (z. B. `1280`). `0` = Originalgröße. |
| `blur` | `0.0` | Gaußscher Weichzeichner-Radius in Pixeln, nach dem Verkleinern. `1`–`1.5` wirkt leicht weich. |
| `output_quality` | `80` | JPEG-Qualität des gespeicherten Bildes (1–95). |
| `face_blur` | `false` | Gesichter erkennen und verpixeln (OpenCV YuNet, auf dem Original vor dem Verkleinern). |
| `face_threshold` | `0.6` | Erkennungsschwelle 0–1. Niedriger findet mehr Gesichter, aber auch mehr Fehltreffer. |
| `face_margin` | `0.4` | Zusätzlich verpixelter Rand als Anteil der Gesichtsgröße. |

### Akkuschutz (nur cloud)

| Option | Standard | Beschreibung |
|---|---|---|
| `battery_min` | `0` | Liegt der zuletzt gemessene Akkustand darunter (in %), pausieren die Intervall-Aufnahmen. `0` = aus. |
| `battery_pause_hours` | `6.0` | Dauer der Pause. Danach ein Bild mit neuer Messung; reicht es noch nicht, beginnt die nächste Pause. |

### Platzhalterbilder

| Option | Standard | Beschreibung |
|---|---|---|
| `placeholders` | `false` | Nachts bzw. bei Störung/Akkupause einmal ein Platzhalterbild als `latest.jpg` ablegen und hochladen. |
| `night_text` | `"Es ist Nacht\nNächstes Bild ab {next} Uhr"` | Text des Nachtbildes. Erste Zeile groß. `""` = kein Text. |
| `night_image` | `""` | Eigenes Hintergrundbild (JPEG/PNG). Leer = Mond auf dunkelblauem Grund. |
| `offline_text` | `"Vorübergehend außer Betrieb\nStand: {date}, {time} Uhr"` | Text bei Störung oder Akkupause. |
| `offline_image` | `""` | Eigenes Hintergrundbild. Leer = durchgestrichene Kamera auf grauem Grund. |
| `offline_after` | `1` | Nach so vielen fehlgeschlagenen Intervall-Aufnahmen in Folge erscheint das Offline-Bild. |

Platzhalter in den Texten: `{next}` (Beginn des nächsten Zeitfensters, z. B. `09:00`,
`morgen 09:00`, `Mo 09:00`), `{date}`, `{time}`, `{camera}`. Eigene Bilder müssen für den
Dienstbenutzer lesbar sein, z. B. unter `/etc/tapo-webcam/` mit Rechten 644.

### Upload (pro Kamera)

| Option | Standard | Beschreibung |
|---|---|---|
| `upload` | `true` | Bilder dieser Kamera hochladen, sofern `[upload]` konfiguriert ist. |
| `upload_name` | `""` | Dateiname auf dem Server für diese Kamera; leer = `name` aus `[upload]`. |

## Abschnitt `[upload]`

| Option | Standard | Beschreibung |
|---|---|---|
| `host` | – (Pflicht) | SFTP-Server. |
| `user` | – (Pflicht) | Benutzername. |
| `port` | `22` | SSH-Port. |
| `key` | `""` | Privater SSH-Schlüssel (OpenSSH-Format, ed25519/RSA/ECDSA). Muss für den Dienstbenutzer lesbar sein. |
| `password` | `""` | Passwort – besser über `password_env`. |
| `password_env` | `"SFTP_PASSWORD"` | Umgebungsvariable mit dem Passwort. |
| `dir` | `"."` | Zielverzeichnis, absolut oder relativ zum Login-Verzeichnis. Muss existieren. |
| `name` | `"{camera}.jpg"` | Dateiname auf dem Server; `{camera}` wird durch den Kameranamen ersetzt. |
| `known_hosts` | neben `cloud_session_file` | Datei für den Host-Schlüssel des Servers (`known_hosts_sftp`). |
| `timeout` | `30.0` | Sekunden für Verbindung und Übertragung. |

Entweder `key` oder ein Passwort ist nötig. Fehlt beides, startet der Dienst nicht. Testbefehle wie
`placeholders` (ohne `--upload`) oder `snap` laufen trotzdem, nur ohne Upload.

## Vollständiges Beispiel

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

[upload]
host = "ssh.example.de"
user = "webcam"
key  = "/var/lib/tapo-webcam/.ssh/sftp_ed25519"
dir  = "html/webcam"
name = "webcam.jpg"

[[camera]]
name          = "parkplatz"
connection    = "cloud"
cloud_device  = "Parkplatz"
cloud_seconds = 3
interval      = 900
timeout       = 30
```

## Umgebungsvariablen

| Variable | Verwendung |
|---|---|
| `TAPO_WEBCAM_CONFIG` | Pfad der Konfiguration, wenn `-c` fehlt. |
| `TAPO_PASSWORD` | Passwort des TP-Link-Kontos für `connection = "local"` (Name über `password_env` änderbar). |
| `SFTP_PASSWORD` | Passwort für `[upload]` (Name über `password_env` änderbar). |

Beim systemd-Dienst stehen sie in `/etc/tapo-webcam/env` (`NAME=wert`, eine Zeile pro Variable, ohne
Anführungszeichen). Diese Datei liest nur systemd – Befehle, die direkt mit `sudo -u tapo-webcam`
gestartet werden, sehen sie nicht.
