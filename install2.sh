#!/bin/bash
# install2.sh: set up piMap 2.0 (HTML5) on the Raspberry Pi, in place of the pygame map.
#
#   ./install2.sh            install, build the map tiles, switch HDMI-2 to piMap 2.0
#   ./install2.sh --back     switch HDMI-2 back to the pygame piMap
#
# Uses the same config.py as piMap 1. Run as your normal user (sudo only for apt).
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--back" ]]; then
  systemctl --user disable --now pimap2-screen.service pimap2.service || true
  systemctl --user enable --now pimap.service
  echo "HDMI-2 is back on the pygame piMap."
  exit 0
fi

echo "==> Packages"
sudo apt-get update -qq
sudo apt-get install -y python3 python3-requests python3-pil x11-xserver-utils wget
sudo apt-get install -y chromium-browser 2>/dev/null || sudo apt-get install -y chromium
sudo apt-get install -y python3-sgp4 2>/dev/null || pip3 install --break-system-packages sgp4
sudo apt-get install -y python3-fastapi python3-uvicorn 2>/dev/null \
  || pip3 install --break-system-packages fastapi "uvicorn[standard]"
sudo apt-get install -y python3-paho-mqtt 2>/dev/null || pip3 install --break-system-packages paho-mqtt || true
# DST-correct header clock (optional; without it the clock goes by longitude)
python3 -c "import timezonefinder" 2>/dev/null || pip3 install --break-system-packages timezonefinder || true

echo "==> Settings"
if [[ ! -f config.py ]]; then
  cp config.example.py config.py
  echo "    -> EDIT config.py: QRZ_API_KEY, QRZ_USER_AGENT_CALLSIGN, HOME_LAT/HOME_LON"
else
  echo "    config.py already there: piMap 2.0 uses it as is"
fi

echo "==> Map tiles (from data/maptiles, about a minute on a Pi 5)"
python3 tools/build_pyramid.py

echo "==> Services"
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 deploy/pimap2.service deploy/pimap2-screen.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user disable --now pimap.service 2>/dev/null || true      # the pygame map wants HDMI-2 too
systemctl --user enable --now pimap2.service pimap2-screen.service
sudo loginctl enable-linger "$USER" 2>/dev/null || true

echo
echo "Done: piMap 2.0 is on HDMI-2. Back to the pygame map any time: ./install2.sh --back"
