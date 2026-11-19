# Funktionen im Detail

## Aufnahmewege

### Cloud (`connection = "cloud"`)

Für Kameras an einem anderen Ort, ohne VPN und ohne Gerät vor Ort. Der Dienst meldet sich wie die
Tapo-App beim TP-Link-Konto an und öffnet über das **Cloud-Relay** für wenige Sekunden
(`cloud_seconds`) den Live-Stream. Das erste vollständige Bild wird als JPEG übernommen.

* Bibliothek: [ontapo](https://pypi.org/project/ontapo/) (inoffiziell, experimentell).
* Anmeldung und Token-Erneuerung: `tapo_cloud_auth.py` (App-Login inkl. E-Mail-Code, Region-Erkennung
  `euw1`/`aps1`/`use1`). Abgelaufene Tokens werden automatisch erneuert.
* Akku-Kameras schlafen. Beim ersten Kontakt meldet das Relay oft HTTP 404, bis die Kamera wach ist –
  deshalb `cloud_retries` Wiederholungen im Abstand von `cloud_retry_wait` Sekunden.
* Dauer pro Bild: typisch 8–15 s.
* Alle Cloud-Zugriffe laufen nacheinander (eine Sitzung für alle Cloud-Kameras).

### Lokal (`connection = "local"`)

Im selben Netz spricht die Kamera ein verschlüsseltes TP-Link-Protokoll auf Port 8800, auch ohne
RTSP. [pytapo](https://github.com/JurajNyiri/pytapo) setzt es um.

* `snapshot`: eingebautes Standbild, < 1 s, meist 640×360.
* `stream`: kurz den Stream öffnen, ffmpeg macht aus dem ersten Frame ein JPEG – volle Auflösung.
* `auto`: erst snapshot, bei leerem Ergebnis stream. Nach 3 leeren Standbildern in Folge direkt stream.
* Bei einem Fehler wird einmal neu angemeldet und erneut versucht.

### Andere Orte – Alternativen zur Cloud

| Weg | Was du brauchst | Bewertung |
|---|---|---|
| Cloud | nichts vor Ort | Am schnellsten eingerichtet, aber inoffiziell. |
| Kleiner Rechner vor Ort + Tailscale | Raspberry Pi (Zero 2 W reicht) im WLAN der Kamera | Am stabilsten; `connection = "local"`, Zugriff von außen per Tailscale ohne Portfreigabe. |
| Standort-VPN | z. B. WireGuard zwischen zwei FRITZ!Boxen | Gut, wenn vor Ort ohnehin ein passender Router steht. |

## Zeitfenster und Intervall

* Intervall-Aufnahmen laufen auf „runden“ Zeiten: Bei `interval = 900` um :00, :15, :30, :45.
* Außerhalb von `active_hours`/`active_days` wird nicht aufgenommen; mit `placeholders = true`
  erscheint stattdessen das Nachtbild.
* `snapshot.jpg` und `trigger` nehmen **immer** auf, auch nachts und in Akkupausen – sie sind bewusste
  Einzelabrufe.

## Bildbearbeitung

Reihenfolge für jedes Bild (`postprocess`):

1. JPEG öffnen, nach RGB wandeln – dabei gehen alle Metadaten (EXIF, GPS, Kamerainfos) verloren.
2. `face_blur`: Gesichter auf dem **Original** suchen und verpixeln.
3. Auf `max_width` verkleinern (Lanczos).
4. `blur`: weichzeichnen.
5. Als JPEG mit `output_quality` speichern.

Das Originalbild wird zu keinem Zeitpunkt gespeichert oder ausgeliefert.

### Gesichtserkennung (`face_blur`)

* Modell: OpenCV **YuNet** (`models/face_detection_yunet_2023mar.onnx`, ca. 230 KB), läuft auf der CPU.
* Gesucht wird in drei Größen (640, 1280 und 1920 px Breite), damit große Gesichter direkt vor der
  Kamera ebenso gefunden werden wie Gesichter in einigen Metern Entfernung (ab ca. 80–100 px
  Gesichtsbreite im 4K-Bild). Überlappende Treffer werden zusammengefasst.
* Jedes Gesicht wird mit Rand (`face_margin`, nach oben etwas mehr für Haare/Stirn) auf ca. 6 Blöcke
  verpixelt und zusätzlich weichgezeichnet.
* Dauer: ca. 2–3 s pro 4K-Bild auf einem einfachen Server; Speicherbedarf kurzzeitig mehrere hundert MB.
* **Fail-safe:** Schlägt die Erkennung fehl, wird das **ganze** Bild stark weichgezeichnet.
* Grenzen: Seitlich abgewandte, verdeckte oder sehr unscharfe Gesichter können übersehen werden.
  Kennzeichen werden nicht erkannt – dafür hilft `max_width`/`blur`.
* Log: `[parkplatz] 2 Gesicht(er) unkenntlich gemacht (2.4 s)`.

Richtwerte für eine 4K-Kamera: Bei 1280 px Breite sind Personen und Autos erkennbar, Gesichter und
Kennzeichen aus der Entfernung kaum noch; bei 640 px nur noch die Gesamtszene.

## Akkuschutz

* Den Akkustand liefert nur die **wache** Kamera (`getBatteryStatus`); die Geräteliste der Cloud enthält
  ihn nicht. Deshalb wird er direkt nach jedem Cloud-Bild im Hintergrund abgefragt – es wird nie
  eigens dafür geweckt. Log: `[parkplatz] Akku 77 % (Laden: NO)`.
* Vor jeder Intervall-Aufnahme entscheidet der zuletzt gemessene Wert: Liegt er unter `battery_min`,
  pausieren die Aufnahmen für `battery_pause_hours` ab dem Messzeitpunkt. Log:
  `Akku 25 % < 30 % – keine Intervall-Aufnahmen bis 19.11. 18:30`.
* Nach der Pause folgt ein Bild mit neuer Messung. Ist der Akku weiter zu schwach, beginnt die nächste
  Pause – die Kamera wird also höchstens einmal pro Pausendauer geweckt.
* Der letzte Wert liegt in `<output_dir>/<kamera>/.battery.json` und überlebt Neustarts.
* Öffentlich sichtbar ist nur das neutrale „außer Betrieb“-Bild (mit `placeholders = true`).

## SFTP-Upload

Jedes echte Bild (Intervall, `trigger`, `snapshot.jpg?save=1`, `snap`) und jedes Platzhalterbild wird
auf den Webserver geladen.

* Ablauf: Verbindung aufbauen → Datei als `.<name>.tmp` schreiben → Rechte 644 → per `posix-rename`
  über die alte Datei schieben. Besucher sehen nie ein halbes Bild. Server ohne posix-rename: alte
  Datei löschen, dann umbenennen.
* Pro Upload eine neue Verbindung (bei 15-Minuten-Intervall unkritisch). Uploads laufen im Hintergrund
  und verzögern die Aufnahme nicht.
* **Host-Schlüssel:** Beim ersten Kontakt wird der Schlüssel des Servers in `known_hosts_sftp`
  gespeichert (Log mit Fingerabdruck). Ändert er sich später, bricht der Upload ab
  (`BadHostKeyException`) – dann prüfen, ob der Hoster den Schlüssel geändert hat, und die Zeile aus
  `known_hosts_sftp` löschen.
* Fehler stehen im Log (`SFTP-Upload nach … fehlgeschlagen`) und unter `/status` → `upload.last_error`.
  Das nächste Bild wird normal erneut versucht.

Anmeldung mit Schlüssel (empfohlen) oder Passwort – siehe [Installation](installation.md#sftp-upload-einrichten).
Bei vielen Webhostern landet man nach dem Login im Wurzelverzeichnis des Webspace; `dir` ist dann der
Ordner der Website relativ dazu, z. B. `meinewebsite/webcam`.

## Platzhalterbilder

Mit `placeholders = true` zeigt die Webcam statt eines veralteten Bildes einen eindeutigen Hinweis.

| Bild | Wann | Inhalt |
|---|---|---|
| **Nacht** | außerhalb von `active_hours`/`active_days` | Mond und Sterne auf dunkelblauem Grund, „Es ist Nacht – Nächstes Bild ab morgen 09:00 Uhr“ |
| **Außer Betrieb** | Akkupause **oder** `offline_after` fehlgeschlagene Intervall-Aufnahmen in Folge (Internet, Cloud, Kamera offline …) | Durchgestrichene Kamera auf grauem Grund, „Vorübergehend außer Betrieb – Stand: 19.11.2026, 09:00 Uhr“ |

* Der Grund einer Störung wird bewusst **nicht** angezeigt – Besucher erfahren nichts über Akku oder
  Technik. Details stehen nur im Log und unter `/status`.
* Jedes Platzhalterbild wird **einmal pro Pause bzw. Störung** erzeugt, als `latest.jpg` abgelegt
  (nicht im Archiv) und hochgeladen. Der Zeitstempel zeigt den Beginn der Störung.
* Das nächste echte Bild ersetzt den Platzhalter.
* Schlägt der Upload des Platzhalters fehl (z. B. weil die Internetverbindung des Servers selbst weg
  ist), wird er beim nächsten Durchgang erneut versucht.
* Nach einem Neustart in der Nacht wird das Nachtbild sofort gesetzt.
* Die Bildgröße richtet sich nach dem letzten echten Bild, sonst nach `max_width` im Format 16:9.
* Eigene Hintergründe: `night_image`, `offline_image`. Der Text wird mit schwarzem Rand darübergelegt,
  `night_text = ""` bzw. `offline_text = ""` lässt ihn weg.
* Schrift: DejaVu Sans (`fonts-dejavu-core`); fehlt sie, wird die eingebaute Pillow-Schrift verwendet.

Vorschau erzeugen, ohne eine Kamera zu wecken:

```bash
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     placeholders -c /etc/tapo-webcam/config.toml
# -> <output_dir>/<kamera>/placeholder_night.jpg und placeholder_offline.jpg
```

## Archiv

* Ablage: `<output_dir>/<kamera>/JJJJ/MM/TT/HHMMSS.jpg`, Ereignisbilder `HHMMSS_<tag>.jpg`.
* `latest.jpg` wird atomar ersetzt (erst `.latest.tmp`, dann umbenennen).
* Stündliche Aufräumrunde löscht Bilder älter als `retention_days` und leere Ordner.
