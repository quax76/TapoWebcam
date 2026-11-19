# Installation

## System requirements

| | Minimum | Recommended |
|---|---|---|
| Operating system | Debian 12, Ubuntu 24.04, Raspberry Pi OS Bookworm (Python ≥ 3.11) | Ubuntu 24.04 LTS |
| CPU | 1 core, x86-64 or ARM64 | 2 cores |
| RAM | 512 MB (without `face_blur`) | 2 GB with `face_blur` (peaks of about 850 MB while processing a 4K picture) |
| Disk | about 600 MB for program and libraries | + space for the archive (about 100 KB per picture at 1280 px) |
| Network | outgoing HTTPS (cloud), SSH/SFTP to the web server; for `local` port 8800 + 443 to the camera | |

**Note for virtual machines:** newer numpy/OpenCV versions require a CPU with x86-64-v2. The Proxmox
CPU type `kvm64` / "Common KVM processor" lacks this instruction set. `requirements.txt` therefore caps
the versions (`numpy<2.3`, `opencv-python-headless<4.13`). Alternatively choose the CPU type `host` or
`x86-64-v2-AES` in Proxmox.

## Option A – systemd service (recommended)

```bash
git clone https://github.com/quax76/TapoWebcam.git tapo-webcam
cd tapo-webcam
sudo bash install.sh
```

`install.sh` does the following:

1. Installs packages: `python3`, `python3-venv`, `ffmpeg`, `fonts-dejavu-core`
2. Creates the system user `tapo-webcam` (no login shell, home `/var/lib/tapo-webcam`)
3. Copies the program to `/opt/tapo-webcam/` and creates the virtual environment `/opt/tapo-webcam/venv`
   with all Python dependencies
4. Creates `/etc/tapo-webcam/config.toml` from `config.example.toml` (only if it does not exist yet)
5. Creates `/etc/tapo-webcam/env` for passwords (mode 600, only if it does not exist yet)
6. Installs the systemd unit `tapo-webcam.service`

The script can be run any number of times; configuration and env file are kept.

### Directories

| Path | Contents | Owner / mode |
|---|---|---|
| `/opt/tapo-webcam/` | program, model, venv | root, 755 |
| `/etc/tapo-webcam/config.toml` | configuration (no secrets) | root:tapo-webcam, 640 |
| `/etc/tapo-webcam/env` | passwords (`TAPO_PASSWORD`, `SFTP_PASSWORD`) | root, 600 – read by systemd only |
| `/var/lib/tapo-webcam/` | cloud session, SSH key, `known_hosts_sftp` | tapo-webcam, 750 |
| `/var/lib/tapo-webcam/images/` | archive, `latest.jpg`, battery state | tapo-webcam |

### Sign in to the cloud

For `connection = "cloud"` sign in to the Tapo account once. The command asks for e-mail, password and
the confirmation code from the TP-Link e-mail:

```bash
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     cloud-login -c /etc/tapo-webcam/config.toml
```

At the end all cameras of the account are listed, e.g.

```
  cloud_device = "Parking"   (C460, id 8021…)
```

Put this name into `config.toml` as `cloud_device`. The session is then stored in `cloud_session_file`
(mode 600) and refreshed automatically. Signing in again is only needed if the refresh fails
(log: `Token refresh failed`).

### Camera in the LAN

For `connection = "local"` you need the IP address and the password of the TP-Link account. The
password belongs in the env file, not in the configuration:

```bash
sudo vi /etc/tapo-webcam/env      # TAPO_PASSWORD=…
```

For cameras paired to a hub (H200/H500) enter the IP of the hub and find the `child_id` with `discover`.

### Configure, test, start

```bash
sudo cp /etc/tapo-webcam/config.toml /etc/tapo-webcam/config.toml.bak
sudo vi /etc/tapo-webcam/config.toml
# test picture (wakes the camera once):
sudo -u tapo-webcam /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     snap -c /etc/tapo-webcam/config.toml -o /var/lib/tapo-webcam/test.jpg -v
sudo systemctl enable --now tapo-webcam
journalctl -fu tapo-webcam
```

### Set up the SFTP upload

See [Features → SFTP upload](features.md#sftp-upload). In short, with an SSH key:

```bash
sudo install -d -o tapo-webcam -g tapo-webcam -m 700 /var/lib/tapo-webcam/.ssh
sudo -u tapo-webcam ssh-keygen -t ed25519 -N "" -C tapo-webcam -f /var/lib/tapo-webcam/.ssh/sftp_ed25519
sudo cat /var/lib/tapo-webcam/.ssh/sftp_ed25519.pub     # add this at your web host
```

With a password put `SFTP_PASSWORD=…` into `/etc/tapo-webcam/env` instead. Test the connection
(uploads `tapo-webcam-test.jpg`, does not wake any camera). Start it through systemd so that the env
file is loaded just like for the service:

```bash
sudo systemd-run --pipe --wait --quiet -p User=tapo-webcam -p EnvironmentFile=/etc/tapo-webcam/env \
     /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py \
     placeholders -c /etc/tapo-webcam/config.toml --upload
```

## Option B – Docker

```bash
git clone https://github.com/quax76/TapoWebcam.git tapo-webcam
cd tapo-webcam
cp config.example.toml config.toml
#   set  output_dir = "/data"  and  cloud_session_file = "/data/cloud_session.json"
printf 'TAPO_PASSWORD=\nSFTP_PASSWORD=\n' > .env      # fill in only what you need
docker compose run --rm -it tapo-webcam python tapo_webcam.py cloud-login   # cloud only
docker compose up -d --build
```

Pictures, cloud session and `known_hosts_sftp` then live in the folder `./images` (volume `/data`).
An SSH key for the upload can be stored there as well (`key = "/data/sftp_ed25519"`).

## Update

```bash
cd ~/tapo-webcam
git pull
sudo bash install.sh
sudo systemctl restart tapo-webcam
```

New options are documented in `config.example.toml`; the existing `config.toml` is not overwritten.
Docker: `git pull && docker compose up -d --build`.

## Uninstall

```bash
sudo systemctl disable --now tapo-webcam
sudo rm /etc/systemd/system/tapo-webcam.service && sudo systemctl daemon-reload
sudo rm -rf /opt/tapo-webcam
sudo rm -rf /etc/tapo-webcam            # configuration and passwords
sudo rm -rf /var/lib/tapo-webcam        # pictures, cloud session, keys
sudo userdel tapo-webcam
```

## Optional: maintenance without a sudo password

For remote maintenance (e.g. via SSH key and automation) the required commands can be allowed in
sudoers – `sudo EDITOR=vi visudo -f /etc/sudoers.d/tapo-webcam`:

```
Cmnd_Alias TAPO_SVC  = /usr/bin/systemctl restart tapo-webcam, /usr/bin/systemctl start tapo-webcam, /usr/bin/systemctl stop tapo-webcam
Cmnd_Alias TAPO_INST = /usr/bin/bash /home/<user>/tapo-webcam/install.sh
Cmnd_Alias TAPO_CFG  = /usr/bin/cat /etc/tapo-webcam/config.toml, /usr/bin/cp /etc/tapo-webcam/config.toml /etc/tapo-webcam/config.toml.bak, /usr/bin/install -m 640 -g tapo-webcam /home/<user>/tapo-webcam/config.new.toml /etc/tapo-webcam/config.toml
<user> ALL=(root) NOPASSWD: TAPO_SVC, TAPO_INST, TAPO_CFG
<user> ALL=(tapo-webcam) NOPASSWD: /opt/tapo-webcam/venv/bin/python /opt/tapo-webcam/tapo_webcam.py *
```

Caution: since `install.sh` lives in the user's home directory and runs as root, this effectively means
root access for that account. The list prevents mistakes, not attackers.
