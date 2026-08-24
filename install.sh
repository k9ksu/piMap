#!/bin/bash
# install.sh — set up piMap on a Raspberry Pi (Debian) kiosk.
#
# Installs dependencies, seeds config.py from the example, and installs the
# systemd --user service that auto-starts the map on the kiosk display.
# Run as your normal user (it uses sudo only for apt + linger).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing dependencies (Debian packages)"
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pygame python3-requests x11-xserver-utils
# sgp4 (satellite TLE math): prefer the distro package, fall back to pip.
sudo apt-get install -y python3-sgp4 2>/dev/null \
  || pip3 install --break-system-packages sgp4
# Optional extras (safe to skip): timezonefinder (DST clock), Pillow (make_tiles.py)
# paho-mqtt (FT8 monitor / PSKReporter live feed): distro first, pip fallback.
sudo apt-get install -y python3-paho-mqtt 2>/dev/null \
  || pip3 install --break-system-packages paho-mqtt
sudo apt-get install -y python3-tz python3-pil 2>/dev/null || true

echo "==> Seeding config.py from example (if missing)"
if [[ ! -f config.py ]]; then
  cp config.example.py config.py
  echo "    -> EDIT config.py: QRZ_API_KEY, QRZ_USER_AGENT_CALLSIGN, HOME_LAT/HOME_LON"
else
  echo "    config.py already exists — left untouched"
fi

echo "==> Installing systemd --user service"
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 deploy/pimap.service "$HOME/.config/systemd/user/pimap.service"
systemctl --user daemon-reload
systemctl --user enable pimap.service
# Keep the user service running across logout / at boot (kiosk autostart).
sudo loginctl enable-linger "$USER" 2>/dev/null || true

echo
echo "Done. Next:"
echo "  1. Edit config.py with your QRZ key / callsign / QTH."
echo "  2. Test in the foreground:   ./run.sh"
echo "  3. Start the kiosk service:  systemctl --user start pimap.service"
echo
echo "NOTE: the service assumes an X session on DISPLAY :0 and renders on HDMI-2."
echo "      Edit ~/.config/systemd/user/pimap.service if your display differs."
