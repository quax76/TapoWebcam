# Datenschutz und Sicherheit

## Was das Tool für die Privatsphäre tut

* **Kein Original:** Das Bild der Kamera wird nur im Arbeitsspeicher bearbeitet. Gespeichert,
  ausgeliefert und hochgeladen wird ausschließlich die bearbeitete Fassung.
* **Metadaten entfernt:** EXIF, GPS und Kamerainfos gehen bei der Umwandlung verloren.
* **Gesichter verpixelt** (`face_blur`), mit Fail-safe: Scheitert die Erkennung, wird das ganze Bild
  weichgezeichnet.
* **Auflösung reduziert** (`max_width`) und optional weichgezeichnet (`blur`).
* **Zeitfenster:** Außerhalb von `active_hours`/`active_days` keine automatischen Aufnahmen.
* **Neutrale Störungsmeldung:** Bei Ausfall oder schwachem Akku zeigt die Webcam nur „Vorübergehend
  außer Betrieb“ – ohne technische Details.
* **Begrenzte Aufbewahrung:** `retention_days` löscht das Archiv automatisch; `keep_history = false`
  speichert gar kein Archiv.

## Grenzen

Keine automatische Erkennung ist vollständig. Abgewandte, verdeckte oder unscharfe Gesichter können
übersehen werden; Kennzeichen, Kleidung oder Körperhaltung bleiben erkennbar. Bildausschnitt,
Auflösung und Aufstellort sind die wirksamsten Stellschrauben.

## Rechtliches (keine Rechtsberatung)

Wer eine Webcam öffentlich betreibt, ist für die Rechtmäßigkeit selbst verantwortlich. Typische Punkte
in Deutschland:

* Personen dürfen nicht identifizierbar sein, oder es braucht eine Rechtsgrundlage (DSGVO Art. 6).
* Öffentlicher Raum (Straßen, Gehwege, fremde Grundstücke) sollte möglichst nicht erfasst werden.
* Hinweisschilder vor Ort, wenn Personen erfasst werden können.
* Im Verein: Beschluss bzw. Information der Mitglieder, Hinweis in der Datenschutzerklärung der Website.

## Geheimnisse

| Geheimnis | Ort | Schutz |
|---|---|---|
| Cloud-Sitzung (Token des Tapo-Kontos) | `cloud_session_file` | Rechte 600, nur Dienstbenutzer. Gewährt **vollen Zugriff** auf das Tapo-Konto. |
| Passwort TP-Link-Konto (local) | `/etc/tapo-webcam/env` | root, 600; nur systemd liest die Datei |
| SFTP-Passwort | `/etc/tapo-webcam/env` | wie oben |
| SFTP-Schlüssel | z. B. `/var/lib/tapo-webcam/.ssh/sftp_ed25519` | 600, nur Dienstbenutzer |
| API-Token | `config.toml` | 640, Gruppe tapo-webcam |

Nichts davon gehört in ein Git-Repository. `.gitignore` schließt `config.toml`, `.env`,
`cloud_session.json` und Bilder aus.

Das Cloud-Login-Modul protokolliert Anfragen nur mit geschwärzten Feldern (`token`, `password` …).

## Härtung

* **HTTP-API** nicht ins Internet freigeben. Sie hat keine TLS-Verschlüsselung; `token` schützt nur
  vor zufälligen Zugriffen im LAN. Für die öffentliche Webcam ist der SFTP-Upload gedacht.
  Wenn die API nur lokal gebraucht wird: `listen = "127.0.0.1"`.
* **SFTP:** eigenes Konto beim Hoster mit Zugriff nur auf den Webcam-Ordner, wenn möglich;
  Schlüssel statt Passwort. Der Host-Schlüssel wird beim ersten Kontakt gespeichert und danach
  geprüft.
* **systemd:** Der Dienst läuft unprivilegiert mit schreibgeschütztem System (siehe
  [Architektur](architektur.md#systemd-härtung)).
* **Updates:** `pytapo`/`ontapo` regelmäßig aktualisieren (`sudo bash install.sh` installiert die
  jeweils neuesten erlaubten Versionen).
