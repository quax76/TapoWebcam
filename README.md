# TapoWebcam

Macht aus Tapo-Kameras eine **öffentliche Webcam**: Der Dienst holt in festen Abständen ein Standbild,
schützt die Privatsphäre (Verkleinern, Gesichter verpixeln, Metadaten entfernen), lädt das Bild per
SFTP auf einen Webserver und stellt es zusätzlich per HTTP bereit.

Funktioniert **auch mit Modellen ohne RTSP/ONVIF** – Akku-Kameras wie C410/C420/C425/C460,
Türklingel D230, Kameras am Hub H200/H500 – und auch, wenn die Kamera an einem ganz anderen Ort
steht (Zugriff über das TP-Link-Cloud-Relay, wie die Tapo-App unterwegs).

![Platzhalterbilder](docs/img/placeholders.jpg)

## Funktionen

| Bereich | Was passiert |
|---|---|
| **Aufnahme** | Lokal im LAN (pytapo, Port 8800) oder über die TP-Link-Cloud (ontapo). Intervall frei wählbar, nur in einem Zeitfenster (`active_hours`, `active_days`). |
| **Datenschutz** | Gesichtserkennung mit OpenCV YuNet und Verpixeln, Verkleinern auf `max_width`, optional Weichzeichnen, EXIF/Metadaten werden verworfen. Das Original wird nie gespeichert. |
| **Akkuschutz** | Akkustand wird nach jedem Bild gelesen (Kamera ist ohnehin wach). Unter `battery_min` pausieren die Aufnahmen. |
| **Veröffentlichung** | Upload per SFTP (atomar: erst `.tmp`, dann umbenennen). HTTP-API mit `latest.jpg`, `snapshot.jpg`, Ereignis-Triggern und `/status`. |
| **Platzhalter** | Nachts ein „Es ist Nacht“-Bild mit der nächsten Aufnahmezeit; bei Störung oder Akkupause ein neutrales „Vorübergehend außer Betrieb“ – ohne den Grund zu verraten. |
| **Archiv** | Tagesordner `JJJJ/MM/TT/HHMMSS.jpg`, automatische Löschung nach `retention_days`. |
| **Integration** | systemd-Dienst oder Docker, Skript für ioBroker. |

## Schnellstart (Debian/Ubuntu, systemd)

```bash
git clone https://github.com/quax76/TapoWebcam.git tapo-webcam
cd tapo-webcam
sudo bash install.sh

# Cloud-Zugang (Kamera an einem entfernten Ort):
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     cloud-login -c /etc/tapo-webcam/config.toml

sudo vi /etc/tapo-webcam/config.toml          # cloud_device, Zeitfenster, Upload …
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     snap -c /etc/tapo-webcam/config.toml -o /var/lib/tapo-webcam/test.jpg -v
sudo systemctl enable --now tapo-webcam
```

Weboberfläche: `http://<server>:8099/`

## Dokumentation

| Kapitel | Inhalt |
|---|---|
| [Installation](docs/installation.md) | systemd, Docker, Cloud-Login, Update, Deinstallation, Systemanforderungen |
| [Konfiguration](docs/konfiguration.md) | Vollständige Referenz aller Optionen mit Standardwerten und Beispielen |
| [Funktionen im Detail](docs/funktionen.md) | Aufnahmewege, Zeitfenster, Gesichtserkennung, Akkuschutz, SFTP-Upload, Platzhalterbilder |
| [HTTP-API und ioBroker](docs/api.md) | Alle Endpunkte, `/status`-Felder, ioBroker-Skript, VIS |
| [Betrieb und Fehlersuche](docs/betrieb.md) | Befehle, Logmeldungen, typische Fehler und Lösungen |
| [Architektur](docs/architektur.md) | Aufbau des Codes, Ablauf einer Aufnahme, Dateien und Rechte |
| [Datenschutz und Sicherheit](docs/datenschutz.md) | Was gespeichert wird, Geheimnisse, Härtung |
| [Änderungen](CHANGELOG.md) | Versionsgeschichte |

## Projektdateien

```
tapo_webcam.py         Hauptprogramm (CLI + Dienst + HTTP-API)
tapo_cloud_auth.py     Login/Token-Erneuerung für das Tapo-Cloud-Konto
tapo_faces.py          Gesichtserkennung und Verpixeln (OpenCV YuNet)
models/                YuNet-Modell (ONNX)
config.example.toml    kommentierte Beispielkonfiguration
install.sh             Installation als systemd-Dienst
tapo-webcam.service    systemd-Unit (gehärtet)
Dockerfile, docker-compose.yml
iobroker/              Skript für den ioBroker-JavaScript-Adapter
docs/                  ausführliche Dokumentation
```

## Hinweise

* Der Cloud-Zugang nutzt die inoffizielle Bibliothek [ontapo](https://pypi.org/project/ontapo/) und
  eine nachgebaute App-Anmeldung. TP-Link sieht diese Nutzung nicht vor; Änderungen an App oder Cloud
  können den Zugang jederzeit unterbrechen. Einsatz auf eigenes Risiko.
* Jede Aufnahme weckt eine Akku-Kamera. Intervalle unter 10 Minuten kosten spürbar Laufzeit.
* Wer eine Webcam öffentlich betreibt, ist für die Einhaltung des Datenschutzrechts (DSGVO, KUG)
  selbst verantwortlich – siehe [Datenschutz](docs/datenschutz.md).
