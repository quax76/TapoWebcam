# Betrieb und Fehlersuche

## Befehle

Alle Befehle: `tapo_webcam.py <befehl> -c <config> [-v]`. Beim systemd-Dienst als Dienstbenutzer
ausführen:

```bash
PY="sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py"
CFG=/etc/tapo-webcam/config.toml
```

| Befehl | Zweck | Weckt Kamera? |
|---|---|---|
| `$PY serve -c $CFG` | Der Dienst selbst (Intervall + HTTP-API) – normalerweise über systemd | ja, im Intervall |
| `$PY snap -c $CFG [-o datei.jpg] [-v]` | Ein Bild aufnehmen. Ohne `-o` wird es archiviert und hochgeladen (sofern Passwort/Schlüssel verfügbar). Zeigt den Akkustand. | ja |
| `$PY discover -c $CFG` | Verbindung prüfen; local: Modell, Firmware, Hub-Kinder; cloud: alle Kameras des Kontos | local: ja |
| `$PY cloud-login -c $CFG` | Tapo-Konto anmelden (interaktiv: E-Mail, Passwort, Code) | nein |
| `$PY placeholders -c $CFG [--upload]` | Platzhalterbilder als Vorschau ablegen; mit `--upload` als `tapo-webcam-test.jpg` hochladen | nein |

`-v` schaltet Debug-Ausgaben ein.

**Akku-Kameras:** Jeder `snap` weckt die Kamera. Testaufnahmen sparsam einsetzen, nicht in Schleifen.

## Dienst

```bash
sudo systemctl status tapo-webcam
sudo systemctl restart tapo-webcam
journalctl -fu tapo-webcam                          # live mitlesen
journalctl -u tapo-webcam --since today | grep -v "GET /"
journalctl -u tapo-webcam --since -3days | grep Akku    # Akkuverlauf
curl -s localhost:8099/status
```

## Logmeldungen

| Meldung | Bedeutung |
|---|---|
| `HTTP-API auf http://0.0.0.0:8099/` | Dienst gestartet |
| `[kamera] Bild aufgenommen (cloud, 97 KB)` | Erfolgreiche Aufnahme (Größe nach Bearbeitung) |
| `[kamera] 2 Gesicht(er) unkenntlich gemacht (2.4 s)` | Gesichter gefunden und verpixelt |
| `[kamera] Akku 77 % (Laden: NO)` | Akkumessung nach dem Bild |
| `[kamera] hochgeladen: host:pfad (97 KB, 1.2 s)` | SFTP-Upload erfolgreich |
| `[kamera] Platzhalterbild 'night' gesetzt` | Nachtbild abgelegt und hochgeladen |
| `[kamera] Platzhalterbild 'offline' gesetzt` | „Außer Betrieb“-Bild abgelegt und hochgeladen |
| `Kamera '…' noch nicht bereit (HTTP 404) – neuer Versuch in 5 s (1/3)` | Akku-Kamera wacht noch auf – normal |
| `[kamera] Aufnahme fehlgeschlagen (2× in Folge): …` | Kamera/Cloud/Internet nicht erreichbar |
| `[kamera] Akku 25 % < 30 % – keine Intervall-Aufnahmen bis …` | Akkupause beginnt |
| `[kamera] SFTP-Upload nach … fehlgeschlagen: …` | Webserver nicht erreichbar oder Anmeldung abgelehnt |
| `SFTP: Host-Schlüssel von … gespeichert (…)` | Erster Kontakt mit dem Webserver |
| `Cloud-Token wird erneuert …` | Automatische Token-Erneuerung |
| `Gesichtserkennung fehlgeschlagen … ganzes Bild wird weichgezeichnet` | Fail-safe gegriffen |

## Typische Fehler

### Dienst startet nicht

`journalctl -u tapo-webcam -n 30` zeigt die Ursache, meist eine Konfigurationsmeldung:

| Meldung | Lösung |
|---|---|
| `Keine [[camera]] in der Konfiguration gefunden.` | Mindestens einen `[[camera]]`-Block anlegen. |
| `Kamera '…': cloud_device nötig` | Namen aus `cloud-login` eintragen. |
| `active_hours '…' ungültig` | Format `"09:00-19:00"`. |
| `CameraConfig.__init__() got an unexpected keyword argument '…'` | Tippfehler in einem Optionsnamen, oder eine allgemeine Option steht unterhalb eines Abschnitts. |
| `[upload]: host, user und key oder password … nötig` | Schlüssel eintragen oder `SFTP_PASSWORD` in `/etc/tapo-webcam/env` setzen. |
| `face_blur = true, aber …` | OpenCV/Modell fehlt → `sudo bash install.sh` erneut ausführen. |
| `NumPy was built with baseline optimizations (X86_V2)` | CPU ohne x86-64-v2 → Versionen aus `requirements.txt` verwenden oder VM-CPU-Typ ändern. |

### Cloud: Kamera nicht erreichbar

`Kamera '…' nach 4 Versuchen nicht erreichbar … Akku leer, offline oder WLAN-Problem?`

1. Livebild in der Tapo-App prüfen – geht es dort auch nicht, liegt es an Kamera, WLAN oder Akku.
2. Internetverbindung des Servers prüfen (`curl -I https://www.tplinkcloud.com`).
3. `cloud_retries`/`cloud_retry_wait` erhöhen, wenn die Kamera langsam aufwacht.
4. Mit `placeholders = true` erscheint automatisch das „außer Betrieb“-Bild.

### Cloud: Anmeldung abgelaufen

`Token-Erneuerung fehlgeschlagen … cloud-login erneut ausführen` → `cloud-login` ausführen, dann
`sudo systemctl restart tapo-webcam`. Nur nötig, wenn die Sitzung wirklich ungültig ist.

### Upload schlägt fehl

| Fehler | Ursache |
|---|---|
| `AuthenticationException` | Falscher Benutzer/Passwort, Schlüssel nicht beim Hoster hinterlegt |
| `NoValidConnectionsError` / Timeout | Host/Port falsch oder Server nicht erreichbar |
| `FileNotFoundError` / `No such file` | `dir` existiert nicht – Ordner anlegen oder Pfad korrigieren |
| `BadHostKeyException` | Host-Schlüssel hat sich geändert – prüfen, dann Zeile in `known_hosts_sftp` löschen |
| `PermissionError` | Schreibrechte im Zielordner fehlen |

Testen ohne Kamera: siehe [Installation → SFTP-Upload](installation.md#sftp-upload-einrichten).

### Bilder kommen nachts / gar nicht

* `active_now` in `/status` prüfen. Maßgeblich ist die Zeitzone des Servers:
  `timedatectl`, ggf. `sudo timedatectl set-timezone Europe/Berlin`.
* `battery_paused_until` gesetzt? Dann läuft eine Akkupause.

### Speicher

Mit `face_blur` braucht die Verarbeitung eines 4K-Bildes kurzzeitig mehrere hundert MB
(beobachtet bis ca. 850 MB Spitze). Unter 1 GB RAM `face_blur` abschalten oder Swap einrichten.
