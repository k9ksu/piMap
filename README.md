# piMap

A real-time ham radio shack dashboard for a Raspberry Pi on an HDMI display.
piMap renders a world map with your recent QSOs, live satellite tracks, the
solar terminator (grey line), space-weather indices, and a GPS/NTP-disciplined
clock — all in a single full-screen [pygame](https://www.pygame.org/) app.

> Built for a ham shack wall, running on a Raspberry Pi driving a second
> HDMI output in kiosk mode.

## Features

- **World map** (NASA Blue Marble) with pan/zoom and high-res map tiles on deep zoom.
- **QSO map** — pulls recent contacts from your [QRZ Logbook](https://logbook.qrz.com/),
  draws great-circle lines to each DX station, hover a dot for QSO details.
  - **Session mode:** while you're actively logging, shows just the current
    session; otherwise falls back to your most recent N contacts.
  - **Courteous polling:** uses QRZ `STATUS` (≈170 B) as a change detector and
    only fetches new records via `AFTERLOGID`, with an adaptive cadence.
  - **Optional N3FJP trigger:** when the N3FJP logger's TCP API is enabled,
    logging activity speeds the refresh cadence (off by default).
- **Satellite tracking** — TLE-based passes for amateur satellites and the ISS,
  with footprints, tracks, and per-sat radio (uplink/downlink) info.
- **Grey line / terminator** overlay with day/night shading.
- **Space weather** — SFI, K/A indices, sunspots (from hamqsl.com).
- **Header clock** with a hover bubble showing **chrony** time-sync quality and
  **GNSS** satellite stats when the Pi is a GPS/PPS NTP server.
- **Kiosk niceties** — runs on a chosen HDMI connector, auto-hides the mouse
  cursor when idle.

## Hardware (as used)

- Raspberry Pi 5 (PoE + fan), second HDMI output for the map.
- Optional: u-blox GNSS receiver (e.g. NEO-M8T) + PPS on a GPIO pin, disciplining
  `chrony`, to drive the clock/time-sync bubble.

## Setup

```bash
# 1. Clone
git clone <your-repo-url> piMap
cd piMap

# 2. Python deps (Debian: prefer apt to satisfy PEP 668)
sudo apt-get install -y python3-pygame python3-requests python3-sgp4 x11-xserver-utils
#   or, in a venv:  pip install -r requirements.txt
#   optional: timezonefinder (DST clock), Pillow (only for make_tiles.py)

# 3. Configuration — copy the example and add your secrets
cp config.example.py config.py
#   then edit config.py:
#     - QRZ_API_KEY        your QRZ Logbook API key
#     - HOME_LAT / HOME_LON your QTH
#     - QRZ_USER_AGENT_CALLSIGN, etc.

# 4. Run
python3 piMap.py
```

`config.py` is **gitignored** because it holds your QRZ API key — keep your real
config local and never commit it.

## Configuration highlights (`config.py`)

| Setting | Purpose |
|---|---|
| `QRZ_API_KEY` | QRZ Logbook API key (required for QSO map) |
| `HOME_LAT` / `HOME_LON` | Your station location |
| `QRZ_MAX_QSOS` | How many recent QSOs to show in fallback mode |
| `QRZ_SESSION_WINDOW_MIN` | Active-logging session window (minutes) |
| `QRZ_REFRESH_SECONDS` / `QRZ_REFRESH_ACTIVE_MIN_SECONDS` | Idle / active poll cadence |
| `QRZ_N3FJP_TRIGGER_ENABLE` | Enable the optional N3FJP API accelerator |
| `DISPLAY_OUTPUT` | Which HDMI connector to render on |
| `CURSOR_HIDE_SECONDS` | Idle time before the mouse cursor hides |

## Running as a service

piMap runs under a `systemd --user` service ([deploy/pimap.service](deploy/pimap.service))
that waits for the X server, places the window on the kiosk HDMI output, and
restarts on failure. The quickest path is the installer:

```bash
./install.sh                          # deps + config.py + user service
systemctl --user start pimap.service  # start it now
journalctl --user -u pimap -f         # watch logs
```

The unit uses `%h`/`%t` so it's user-portable, assumes an X session on
`DISPLAY :0`, and renders on **HDMI-2** (`--right-of HDMI-1`). Edit those lines
in `deploy/pimap.service` (or the installed copy under
`~/.config/systemd/user/`) for a different display layout.

### Kiosk host notes (optional)

The reference setup auto-logs into a minimal X session via lightdm and grants a
couple of passwordless conveniences (restart lightdm, run `solaar`) through a
sudoers drop-in — neither is required by piMap itself:

```
# /etc/sudoers.d/pimap   (use `sudo visudo -f` to edit)
<user> ALL=(root) NOPASSWD: /usr/bin/systemctl start lightdm, /usr/bin/systemctl stop lightdm, /usr/bin/solaar
```

## Map tiles

High-resolution tiles live in `data/maptiles/` and are used when zoomed in.
They can be regenerated with `make_tiles.py` from a high-res source image.

## License

MIT — see [LICENSE](LICENSE).

Bundled third-party assets keep their own licenses: the NASA Blue Marble imagery
is public domain, and the fonts in `fonts/` (Anonymous Pro, B612 Mono, JetBrains
Mono, Overpass Mono, Share Tech Mono, Space Mono) are distributed under their
respective open-font licenses.

73
