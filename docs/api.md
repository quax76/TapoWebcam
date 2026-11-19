# HTTP-API und ioBroker

Der Dienst stellt eine kleine HTTP-API bereit (Standard: Port 8099, alle Schnittstellen). Ohne
`/<kamera>` im Pfad gilt die erste Kamera aus der Konfiguration.

## Endpunkte

| Methode | Pfad | Wirkung | Weckt Kamera? |
|---|---|---|---|
| GET | `/` | Übersichtsseite mit dem letzten Bild jeder Kamera | nein |
| GET | `/<kamera>/latest.jpg` | Zuletzt gespeichertes Bild (auch Platzhalter) | **nein** |
| GET | `/<kamera>/snapshot.jpg` | Frisches Bild; innerhalb `cache_seconds` das zwischengespeicherte. `?force=1` erzwingt eine Aufnahme, `?save=1` speichert zusätzlich (Archiv + Upload). | ja |
| GET/POST | `/<kamera>/trigger?tag=klingel` | Ereignisaufnahme, gespeichert als `HHMMSS_klingel.jpg` und `latest_klingel.jpg`, wird hochgeladen. Antwort JSON; mit `&format=jpeg` das Bild selbst. | ja |
| GET | `/<kamera>/event/<tag>.jpg` | Letztes Bild zu diesem Ereignis-Tag | nein |
| GET | `/status` | JSON-Status aller Kameras | nein |
| GET | `/health` | `{"ok": true}` – auch ohne Token | nein |

Bei Fehlern antworten `snapshot.jpg` und `trigger` mit HTTP 502 und `{"error": "…"}`.

Ist `token` gesetzt, muss jede Anfrage (außer `/health`) `?token=…` oder den Header `X-Token: …`
enthalten, sonst HTTP 401.

**Für Dashboards immer `latest.jpg` verwenden** – jeder Aufruf von `snapshot.jpg` weckt eine
Akku-Kamera.

## `/status`

Beispiel:

```json
{
  "parkplatz": {
    "host": "Parkplatz",
    "connection": "cloud",
    "mode": "auto",
    "interval": 900,
    "active_hours": "09:00-19:00",
    "active_now": true,
    "last_capture": "2026-11-19T10:15:09.569",
    "last_method": "cloud",
    "last_error": null,
    "face_blur": true,
    "battery": 77,
    "battery_charging": "NO",
    "battery_measured": "2026-11-19T10:15:09.875",
    "battery_min": 30,
    "battery_paused_until": null,
    "placeholder": null,
    "failures": 0,
    "upload": {
      "target": "ssh.example.de:html/webcam/webcam.jpg",
      "last_ok": "2026-11-19T10:15:10.738",
      "last_error": null
    }
  }
}
```

| Feld | Bedeutung |
|---|---|
| `host` | IP (local) bzw. `cloud_device` (cloud) |
| `active_now` | Liegt die aktuelle Zeit im Zeitfenster? |
| `last_capture` | Zeitpunkt des letzten erfolgreichen Bildes seit dem Start |
| `last_method` | `cloud`, `snapshot` oder `stream` |
| `last_error` | Letzter Aufnahmefehler (wird bei Erfolg geleert) |
| `battery`, `battery_charging`, `battery_measured` | Letzte Akkumessung (nur cloud); `battery_charging` ist der Rohwert der Kamera, z. B. `NO` |
| `battery_min` | Schwelle, `null` = Akkuschutz aus |
| `battery_paused_until` | Ende der laufenden Akkupause oder `null` |
| `placeholder` | Aktuell gezeigter Platzhalter: `night`, `offline` oder `null` |
| `failures` | Fehlgeschlagene Intervall-Aufnahmen in Folge |
| `upload` | Ziel, letzter erfolgreicher Upload und letzter Fehler; `null` ohne `[upload]` |

## Beispiele

```bash
curl -s http://server:8099/status | python3 -m json.tool
curl -o bild.jpg "http://server:8099/parkplatz/latest.jpg"
curl -s "http://server:8099/parkplatz/trigger?tag=klingel"
```

## ioBroker

`iobroker/tapo-webcam.js` als neues Skript im JavaScript-Adapter (≥ 7) anlegen und oben anpassen:

| Konstante | Bedeutung |
|---|---|
| `BASE_URL` | Adresse des Dienstes, z. B. `http://192.168.1.20:8099` |
| `TOKEN` | API-Token, falls gesetzt |
| `CAMERA` | Kameraname aus `config.toml` |
| `TRIGGERS` | Datenpunkte, die eine Aufnahme auslösen (Klingel, Bewegungsmelder …) mit Tag |
| `TELEGRAM_INSTANCE` | z. B. `telegram.0`, leer = kein Versand |

Das Skript legt unter `0_userdata.0.tapoWebcam.<kamera>` an: `snapshot` (Button), `lastCapture`,
`lastEvent` (lokale Datei), `lastError`, `latestUrl`, `eventUrl`, `online`. Es fragt jede Minute
`/status` ab und nimmt bei jedem Trigger sofort ein Bild auf.

VIS/VIS-2: Bild-Widget mit der URL `http://<dienst>:8099/<kamera>/latest.jpg` und 60 s Refresh.

Kommt der Klingel-Datenpunkt vom ioBroker-Tapo-Adapter, liegt er unter `tapo.0.<geräte-id>…`;
jeder andere Datenpunkt funktioniert ebenso.

## Einbindung in eine Website

Mit SFTP-Upload liegt das Bild unter einem festen Namen auf dem Webserver. Damit Browser es nicht
zwischenspeichern, einen Zeitstempel anhängen und regelmäßig neu laden:

```html
<img id="webcam" src="/webcam/webcam.jpg" alt="Webcam" style="max-width:100%">
<script>
  setInterval(() => {
    document.getElementById('webcam').src = '/webcam/webcam.jpg?t=' + Date.now();
  }, 60000);
</script>
```
