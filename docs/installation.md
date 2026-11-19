# Installation

## Systemanforderungen

| | Minimum | Empfohlen |
|---|---|---|
| Betriebssystem | Debian 12, Ubuntu 24.04, Raspberry Pi OS Bookworm (Python ≥ 3.11) | Ubuntu 24.04 LTS |
| CPU | 1 Kern, x86-64 oder ARM64 | 2 Kerne |
| RAM | 512 MB (ohne `face_blur`) | 2 GB mit `face_blur` (Spitzen bis ca. 850 MB beim Verarbeiten eines 4K-Bildes) |
| Speicher | ca. 600 MB für Programm und Bibliotheken | + Platz für das Bildarchiv (ca. 100 KB pro Bild bei 1280 px) |
| Netz | ausgehend HTTPS (Cloud), SSH/SFTP zum Webserver; für `local` Port 8800 + 443 zur Kamera | |

**Hinweis zu virtuellen Maschinen:** Neuere numpy-/OpenCV-Versionen setzen eine CPU mit
x86-64-v2 voraus. Auf dem Proxmox-CPU-Typ `kvm64` bzw. „Common KVM processor“ fehlt dieser
Befehlssatz. `requirements.txt` begrenzt die Versionen deshalb (`numpy<2.3`,
`opencv-python-headless<4.13`). Alternativ in Proxmox den CPU-Typ `host` oder `x86-64-v2-AES` wählen.

## Variante A – systemd-Dienst (empfohlen)

```bash
git clone https://github.com/quax76/TapoWebcam.git tapo-webcam
cd tapo-webcam
sudo bash install.sh
```

`install.sh` erledigt:

1. Pakete installieren: `python3`, `python3-venv`, `ffmpeg`, `fonts-dejavu-core`
2. Systembenutzer `tapo-webcam` anlegen (ohne Login-Shell, Home `/var/lib/tapo-webcam`)
3. Programm nach `/opt/tapo-webcam/` kopieren, virtuelle Umgebung `/opt/tapo-webcam/venv` mit allen
   Python-Abhängigkeiten erzeugen
4. `/etc/tapo-webcam/config.toml` aus `config.example.toml` anlegen (nur wenn noch nicht vorhanden)
5. `/etc/tapo-webcam/env` für Passwörter anlegen (Rechte 600, nur wenn noch nicht vorhanden)
6. systemd-Unit `tapo-webcam.service` installieren

Das Skript kann beliebig oft ausgeführt werden; Konfiguration und env-Datei bleiben dabei erhalten.

### Verzeichnisse

| Pfad | Inhalt | Besitzer / Rechte |
|---|---|---|
| `/opt/tapo-webcam/` | Programm, Modell, venv | root, 755 |
| `/etc/tapo-webcam/config.toml` | Konfiguration (keine Geheimnisse) | root:tapo-webcam, 640 |
| `/etc/tapo-webcam/env` | Passwörter (`TAPO_PASSWORD`, `SFTP_PASSWORD`) | root, 600 – nur systemd liest sie |
| `/var/lib/tapo-webcam/` | Cloud-Sitzung, SSH-Schlüssel, `known_hosts_sftp` | tapo-webcam, 750 |
| `/var/lib/tapo-webcam/images/` | Bildarchiv, `latest.jpg`, Akku-Zustand | tapo-webcam |

### Kamera über die Cloud anmelden

Für `connection = "cloud"` einmalig das Tapo-Konto anmelden. Der Befehl fragt E-Mail, Passwort und
den Bestätigungscode aus der TP-Link-Mail ab:

```bash
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     cloud-login -c /etc/tapo-webcam/config.toml
```

Am Ende werden alle Kameras des Kontos aufgelistet, z. B.

```
  cloud_device = "Parkplatz"   (C460, id 8021…)
```

Diesen Namen in `config.toml` als `cloud_device` eintragen. Die Sitzung liegt danach in
`cloud_session_file` (Rechte 600) und wird automatisch erneuert. Ein erneuter Login ist nur nötig,
wenn die Erneuerung scheitert (Log: `Token-Erneuerung fehlgeschlagen`).

### Kamera im LAN

Für `connection = "local"` werden IP-Adresse und das Passwort des TP-Link-Kontos gebraucht.
Das Passwort gehört in die env-Datei, nicht in die Konfiguration:

```bash
sudo vi /etc/tapo-webcam/env      # TAPO_PASSWORD=…
```

Bei Kameras am Hub (H200/H500) die IP des Hubs eintragen und mit `discover` die `child_id` ermitteln.

### Konfigurieren, testen, starten

```bash
sudo cp /etc/tapo-webcam/config.toml /etc/tapo-webcam/config.toml.bak
sudo vi /etc/tapo-webcam/config.toml
# Testbild (weckt die Kamera einmal):
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     snap -c /etc/tapo-webcam/config.toml -o /var/lib/tapo-webcam/test.jpg -v
sudo systemctl enable --now tapo-webcam
journalctl -fu tapo-webcam
```

### SFTP-Upload einrichten

Siehe [Funktionen → SFTP-Upload](funktionen.md#sftp-upload). Kurzfassung mit SSH-Schlüssel:

```bash
sudo install -d -o tapo-webcam -g tapo-webcam -m 700 /var/lib/tapo-webcam/.ssh
sudo -u tapo-webcam ssh-keygen -t ed25519 -N "" -C tapo-webcam -f /var/lib/tapo-webcam/.ssh/sftp_ed25519
sudo cat /var/lib/tapo-webcam/.ssh/sftp_ed25519.pub     # beim Webhoster hinterlegen
```

Mit Passwort stattdessen `SFTP_PASSWORD=…` in `/etc/tapo-webcam/env`. Verbindung testen
(lädt `tapo-webcam-test.jpg` hoch, weckt keine Kamera). Damit die env-Datei wie beim Dienst
geladen wird, über systemd starten:

```bash
sudo systemd-run --pipe --wait --quiet -p User=tapo-webcam -p EnvironmentFile=/etc/tapo-webcam/env \
     /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     placeholders -c /etc/tapo-webcam/config.toml --upload
```

## Variante B – Docker

```bash
git clone https://github.com/quax76/TapoWebcam.git tapo-webcam
cd tapo-webcam
cp config.example.toml config.toml
#   output_dir = "/data"  und  cloud_session_file = "/data/cloud_session.json"  setzen
printf 'TAPO_PASSWORD=\nSFTP_PASSWORD=\n' > .env      # nur ausfüllen, was gebraucht wird
docker compose run --rm -it tapo-webcam python tapo_webcam.py cloud-login   # nur für Cloud
docker compose up -d --build
```

Bilder, Cloud-Sitzung und `known_hosts_sftp` liegen dann im Ordner `./images` (Volume `/data`).
Ein SSH-Schlüssel für den Upload kann ebenfalls dort abgelegt werden (`key = "/data/sftp_ed25519"`).

## Update

```bash
cd ~/tapo-webcam
git pull
sudo bash install.sh
sudo systemctl restart tapo-webcam
```

Neue Optionen sind in `config.example.toml` dokumentiert; die bestehende `config.toml` wird nicht
überschrieben. Bei Docker: `git pull && docker compose up -d --build`.

## Deinstallation

```bash
sudo systemctl disable --now tapo-webcam
sudo rm /etc/systemd/system/tapo-webcam.service && sudo systemctl daemon-reload
sudo rm -rf /opt/tapo-webcam
sudo rm -rf /etc/tapo-webcam            # Konfiguration und Passwörter
sudo rm -rf /var/lib/tapo-webcam        # Bilder, Cloud-Sitzung, Schlüssel
sudo userdel tapo-webcam
```

## Optional: Wartung ohne sudo-Passwort

Für Fernwartung (z. B. per SSH-Schlüssel und Automatisierung) können die nötigen Befehle per sudoers
freigegeben werden – `sudo EDITOR=vi visudo -f /etc/sudoers.d/tapo-webcam`:

```
Cmnd_Alias TAPO_SVC  = /usr/bin/systemctl restart tapo-webcam, /usr/bin/systemctl start tapo-webcam, /usr/bin/systemctl stop tapo-webcam
Cmnd_Alias TAPO_INST = /usr/bin/bash /home/<benutzer>/tapo-webcam/install.sh
Cmnd_Alias TAPO_CFG  = /usr/bin/cat /etc/tapo-webcam/config.toml, /usr/bin/cp /etc/tapo-webcam/config.toml /etc/tapo-webcam/config.toml.bak, /usr/bin/install -m 640 -g tapo-webcam /home/<benutzer>/tapo-webcam/config.new.toml /etc/tapo-webcam/config.toml
<benutzer> ALL=(root) NOPASSWD: TAPO_SVC, TAPO_INST, TAPO_CFG
<benutzer> ALL=(tapo-webcam) NOPASSWD: /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py *
```

Achtung: Da `install.sh` im Home-Verzeichnis des Benutzers liegt und als root läuft, ist das
praktisch root-Zugriff für dieses Konto. Die Liste verhindert Versehen, keinen Angreifer.
