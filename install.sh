#!/usr/bin/env bash
# Installation auf Debian/Raspberry Pi OS/Ubuntu (auch Proxmox-LXC). Als root ausführen.
set -euo pipefail
cd "$(dirname "$0")"

apt-get update
apt-get install -y python3 python3-venv ffmpeg fonts-dejavu-core
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || { echo "Python >= 3.11 noetig (Debian 12 / Ubuntu 24.04 / Raspberry Pi OS Bookworm)."; exit 1; }

id tapo-webcam &>/dev/null || useradd --system --home /var/lib/tapo-webcam --shell /usr/sbin/nologin tapo-webcam
install -d -o tapo-webcam -g tapo-webcam -m 750 /var/lib/tapo-webcam /var/lib/tapo-webcam/images
install -d /opt/tapo-webcam /etc/tapo-webcam

install -m 755 tapo_webcam.py /opt/tapo-webcam/
install -m 644 tapo_cloud_auth.py /opt/tapo-webcam/
install -m 644 tapo_faces.py /opt/tapo-webcam/
install -d /opt/tapo-webcam/models
install -m 644 models/face_detection_yunet_2023mar.onnx /opt/tapo-webcam/models/
python3 -m venv /opt/tapo-webcam/venv
/opt/tapo-webcam/venv/bin/pip install --upgrade pip -q
/opt/tapo-webcam/venv/bin/pip install -r requirements.txt -q

if [ ! -f /etc/tapo-webcam/config.toml ]; then
  install -m 640 -g tapo-webcam config.example.toml /etc/tapo-webcam/config.toml
  echo ">> /etc/tapo-webcam/config.toml anlegt – bitte IP und Passwort eintragen."
fi
if [ ! -f /etc/tapo-webcam/env ]; then
  install -m 600 /dev/null /etc/tapo-webcam/env
  echo "# TAPO_PASSWORD=dein-tapo-passwort" > /etc/tapo-webcam/env
fi

install -m 644 tapo-webcam.service /etc/systemd/system/
systemctl daemon-reload

PY=/opt/tapo-webcam/venv/bin/python
APP=/opt/tapo-webcam/tapo_webcam.py
CFG=/etc/tapo-webcam/config.toml
cat <<EOF

Fertig installiert. Naechste Schritte:
  1. Tapo-Konto anmelden (E-Mail, Passwort, Code aus der TP-Link-Mail):
       sudo -u tapo-webcam $PY $APP cloud-login -c $CFG
  2. Angezeigten Kameranamen als cloud_device eintragen:
       sudo vi $CFG
  3. Testbild:
       sudo -u tapo-webcam $PY $APP snap -c $CFG -o /var/lib/tapo-webcam/test.jpg -v
  4. Dienst starten:
       sudo systemctl enable --now tapo-webcam && journalctl -fu tapo-webcam
  Web-Oberflaeche: http://$(hostname -I | awk '{print $1}'):8099/
EOF
