# piMap

A real-time ham radio shack dashboard for a Raspberry Pi on an HDMI display.
piMap renders a world map with your recent QSOs, live satellite tracks, the
solar terminator (grey line), space-weather indices, and a GPS/NTP-disciplined
clock — all in a single full-screen [pygame](https://www.pygame.org/) app.

> Built for a ham shack wall: a Raspberry Pi 5 driving a second HDMI output in
> kiosk mode, pointed at a TV.

<!-- Drop a photo of it on the wall here, e.g.  ![piMap](docs/screenshot.jpg) -->

## Features

- **World map** (NASA Blue Marble) with smooth pan/zoom and high-res map tiles on deep zoom.
- **QSO map** — pulls recent contacts from your [QRZ Logbook](https://logbook.qrz.com/)
  and draws great-circle lines to each DX station; hover a dot for QSO details.
  - **Session mode:** while you're actively logging, shows just the current
    session; otherwise falls back to your most recent N contacts.
  - **Courteous polling:** uses QRZ `STATUS` (≈170 B) as a change detector and
    only fetches new records via `AFTERLOGID`, with an adaptive cadence.
  - **Optional N3FJP trigger:** when the N3FJP logger's TCP API is enabled,
    logging activity speeds the refresh cadence (off by default).
- **Satellite tracking** — TLE-based passes for amateur satellites and the ISS,
  with footprints, ground tracks, and per-sat radio (uplink/downlink) info.
- **Propagation monitor** — drop target dots anywhere on the map and each one
  rates the path from your QTH, per band, from two independent live sources:
  - **FT8** via the [PSKReporter](https://pskreporter.info/) MQTT stream — answers
    *is the path open **and** is anybody on it*, with real callsigns you could work.
  - **WSPR** via [wspr.live](https://wspr.live/) — answers *is the path physically
    open at all*, since beacons run 24/7 even when nobody is operating.
  - Both columns are shown side by side and deliberately **not merged**: a band can
    easily be green on one and blank on the other. Click either cell to drill into
    which stations are actually being heard, grouped one row per callsign.
  - **Locality is a real radius around your QTH**, not the Maidenhead field. A
    2-character field is a 10°×20° box you are rarely centred in — a station in the
    corner of one can be credited with openings that only exist 1000 km away.
  - A chime fires when a dot turns green, **edge-triggered**, so a path that stays
    open all afternoon announces itself once rather than on a repeating timer.
- **Settings flyout** (hover the left edge) — TV output volume, alert-chime volume,
  chime picker, and the spot mode (FT8 / CW / SSB) that sets how strict the dot
  colours are. Choices persist across restarts.
- **Grey line / terminator** overlay with day/night shading.
- **Space weather** — SFI, K/A indices, sunspots (from hamqsl.com).
- **Header clock** with a hover bubble showing **chrony** time-sync quality and
  **GNSS** satellite stats when the Pi is also a GPS/PPS NTP server.
- **Kiosk niceties** — renders on a chosen HDMI connector and auto-hides the
  mouse cursor when idle.

## How it works

piMap is a single pygame process that pulls from a few network sources (plus the
local time chain) and composites everything onto one full-screen surface:

```
        QRZ Logbook ──┐  QSOs → great-circle lines
     Celestrak TLEs ──┤  satellites → tracks + footprints
         hamqsl.com ──┼──▶   piMap (pygame)   ──▶  full-screen world map (HDMI)
         SatNOGS DB ──┤            │
   PSKReporter MQTT ──┤            │  FT8 spots (live stream) ─┐ target-dot
          wspr.live ──┘            │  WSPR spots (5-min poll) ─┘ propagation
                                   └─ header: callsign · clock · time-sync/GNSS bubble
   gpsd + chrony (local) ─────────────┘   (optional — see the gps-stratum1-pi project)
```

Each data source runs on its own adaptive cadence with on-disk caches, so a
flaky network never blocks rendering — the map keeps drawing from the last good
data and the relevant status tag goes amber/red until the fetch recovers.

## Controls

| Input | Action |
|---|---|
| **Mouse wheel** | Zoom in/out at the cursor (or scroll the satellite panel when hovering it) |
| **Left-drag on map** | Pan |
| **Left-click a dot** | Pin / unpin that QSO or satellite's info bubble |
| **Left-click empty map** | Dismiss the pinned bubble |
| **Hover a dot** | Show its info bubble (stays open while the cursor is inside the bubble) |
| **Double-click empty map** | Drop a propagation target dot |
| **Left-drag a target dot** | Move it (its grid square is recomputed on release) |
| **Middle-click a target dot** | Remove it |
| **Click a band cell in a target bubble** | Drill into that source's spots — who is being heard |
| **Right-drag on map** | Pan |
| **Right-click** | Reset view (re-center / default zoom) |
| **Hover the left edge** | Settings flyout (volumes, chime, spot mode) |
| **Hover the right edge** | Satellite selection panel |
| **Esc** | Quit (note: a kiosk window without keyboard focus will not receive this) |

## piMap 2.0 (HTML5) — `v2` branch, phase 1

piMap 2.0 keeps piMap's Python for the data (QRZ, TLEs, space weather, the same
`config.py`) and draws the map in a browser instead of pygame:

- **Smooth zoom** on the full-resolution Blue Marble: a 5-level tile pyramid
  (`tools/build_pyramid.py`, built from `data/maptiles`, never all in memory) so every
  zoom draws tiles about the size they appear. Wheel, pinch and drag glide; while a
  sharper tile loads the coarser one stands in and the sharp one fades in.
- **Satellites glide**: the browser propagates them (SGP4) for every frame it draws,
  at sub-pixel precision, instead of stepping a pixel at a time.
- **Crisp at any resolution**: the header is text, the map is drawn at the screen's
  own pixels (1080p on the shack Pi, 4K on the FieldLab TV).
- Same look: callsign, clock (with DST via `timezonefinder`), space weather, band
  conditions, status tags, piMap's `MAP_BRIGHTNESS` and night shading, black ISS track.

Phase 1 is the map, grey line, borders, header, satellites and mouse zoom/pan, with
piMap's edge flyouts: **right edge** the satellite list (ISS first, then by next pass
over home, tick to show; the choice is saved on the server so every screen shows the
same set, starting from `SATS_SHOWN`), **left edge** display settings (night shading,
borders, tracks, footprints; per browser, and on the FieldLab kiosk a second "TV"
column that sets what the TV draws). Touch screens get edge tabs instead of hover.
**QSO paths** (phase 2): piMap's QRZ loop on the server, unchanged in behaviour (STATUS
change detection, session mode, adaptive polling, the N3FJP trigger), great-circle
paths and dots in `QSO_COLOR` drawn by the browser, piMap's QSO bubble on hover/click,
and a "QSO paths" line in the left menu (and the TV column).
Coming: target dots, propagation, sound and spot mode (phase 3).

**On the shack Pi** (switches HDMI-2 from the pygame map; `./install2.sh --back` undoes it):

```bash
git switch v2 && ./install2.sh
```

**By hand:** `python3 tools/build_pyramid.py`, then `python3 -m pimap2.server` and open
http://localhost:8090/. Tests: `python3 -m unittest discover -s tests` and
`node --test tests/web.test.mjs`.

## Hardware (as used)

- Raspberry Pi 5 (PoE + fan), second HDMI output for the map.
- A TV / monitor on that HDMI connector.
- Optional: a u-blox GNSS receiver (e.g. NEO-M8T) with PPS on a GPIO pin,
  disciplining `chrony`, to drive the clock / time-sync bubble. See the companion
  [gps-stratum1-pi](https://github.com/k9ksu/gps-stratum1-pi) project for that setup.

## Quick start

```bash
git clone https://github.com/k9ksu/piMap
cd piMap
./install.sh          # deps + seeds config.py + installs the systemd --user service
nano config.py        # add your QRZ key, callsign, and QTH lat/lon
./run.sh              # test in the foreground (Esc or Ctrl-C to stop)
```

When it looks right, start the kiosk service:

```bash
systemctl --user start pimap.service
```

### Manual setup (without install.sh)

```bash
# Python deps (Debian: prefer apt to satisfy PEP 668)
sudo apt-get install -y python3-pygame python3-requests python3-sgp4 x11-xserver-utils
#   or, in a venv:  pip install -r requirements.txt
#   optional: timezonefinder (DST clock), Pillow (only for make_tiles.py)

cp config.example.py config.py   # then edit it (see below)
python3 piMap.py
```

`config.py` is **gitignored** because it holds your QRZ API key — keep your real
config local and never commit it. Start from `config.example.py`, which contains
only placeholders.

## Configuration highlights (`config.py`)

| Setting | Purpose |
|---|---|
| `QRZ_API_KEY` | QRZ Logbook API key (required for the QSO map) |
| `QRZ_USER_AGENT_CALLSIGN` | Your callsign, sent in the API user-agent |
| `HOME_LAT` / `HOME_LON` | Your station location (QTH) |
| `QRZ_MAX_QSOS` | How many recent QSOs to show in fallback mode |
| `QRZ_SESSION_WINDOW_MIN` | Active-logging session window (minutes) |
| `QRZ_REFRESH_SECONDS` / `QRZ_REFRESH_ACTIVE_MIN_SECONDS` | Idle / active poll cadence |
| `QRZ_N3FJP_TRIGGER_ENABLE` | Enable the optional N3FJP API accelerator |
| `DISPLAY_OUTPUT` | Which HDMI connector to render on |
| `CURSOR_HIDE_SECONDS` | Idle time before the mouse cursor hides |
| `FLYOUT_IDLE_HIDE_SECONDS` | Close an edge flyout after this long with no mouse motion |
| `WSPR_HOME_RADIUS_KM` | Radius around the QTH counted as "local" for WSPR (0 = old Maidenhead-field matching) |
| `FT8_ENABLED` / `FT8_HOME_RADIUS_KM` | Enable the PSKReporter feed, and its own local radius |
| `FT8_GREEN_MIN_STATIONS` | Distinct DX callsigns needed before a band counts as open |
| `FT8_MODE_THRESHOLDS` / `WSPR_MODE_THRESHOLDS` | Per-mode SNR bars. **Separate scales** — FT8 stations run 25–100 W against WSPR's 1–5 W |
| `WSPR_ALERT_COOLDOWN_SECONDS` | Anti-flap floor between chimes for one dot |
| `ALERT_VOLUME` / `ALERT_SOUND` | Chime volume and which of the 5 synthesised chimes |

## Running as a service

piMap runs under a `systemd --user` service ([deploy/pimap.service](deploy/pimap.service))
that waits for the X server, places the window on the kiosk HDMI output, and
restarts on failure:

```bash
systemctl --user start pimap.service   # start now
systemctl --user enable pimap.service  # start at boot (install.sh does this)
journalctl --user -u pimap -f          # watch logs
```

The unit uses `%h`/`%t` so it's user-portable, assumes an X session on
`DISPLAY :0`, and renders on **HDMI-2** (`--right-of HDMI-1`). Edit those lines
in `deploy/pimap.service` (or the installed copy under `~/.config/systemd/user/`)
for a different display layout.

### Kiosk host notes (optional)

The reference setup auto-logs into a minimal X session via lightdm and grants a
couple of passwordless conveniences (restart lightdm, run `solaar`) through a
sudoers drop-in — neither is required by piMap itself:

```
# /etc/sudoers.d/pimap   (edit with: sudo visudo -f /etc/sudoers.d/pimap)
<user> ALL=(root) NOPASSWD: /usr/bin/systemctl start lightdm, /usr/bin/systemctl stop lightdm, /usr/bin/solaar
```

## Data sources

| Service | Used for | Notes |
|---|---|---|
| [QRZ Logbook API](https://logbook.qrz.com/) | Your QSOs | Requires `QRZ_API_KEY`; polite STATUS/AFTERLOGID polling |
| [Celestrak](https://celestrak.org/) | Satellite TLEs | Cached to `data/amateur_tle_cache.txt`; 15-min retry on failure |
| [SatNOGS DB](https://db.satnogs.org/) | Sat transmitter (uplink/downlink) info | Cached to `data/satnogs_tx_cache.json` |
| [hamqsl.com](https://www.hamqsl.com/) | Space-weather indices | SFI, K/A, sunspots + HF band-conditions strip |
| [PSKReporter](https://pskreporter.info/) | Live FT8 spots | MQTT stream; the QTH radius is enumerated into 4-char grid squares and filtered **broker-side**, so only local traffic is received |
| [wspr.live](https://wspr.live/) | WSPR spots | ClickHouse HTTP API at `db1.wspr.live`; 5-minute poll, each direction limited separately |
| gpsd + chrony (local) | Clock + time-sync/GNSS bubble | Optional; only if the Pi is a GPS NTP server |

## Project layout

```
piMap.py              # main app: render loop, input, layout
config.example.py     # config template — copy to config.py (gitignored)
run.sh                # foreground launcher (testing)
install.sh            # deps + config + systemd --user service
deploy/pimap.service  # the kiosk autostart unit
requirements.txt      # pip deps (core)

qrz_client.py         # QRZ Logbook API client (STATUS / AFTERLOGID)
adif_parser.py        # parse ADIF QSO records
n3fjp_status.py       # optional N3FJP TCP API trigger
sat_tracker.py        # TLE propagation / pass prediction (sgp4)
sat_panel.py          # satellite side-panel UI
sat_panel_manager.py  # panel state / scroll / selection
sat_radio.py          # per-satellite uplink/downlink info
space_weather.py      # hamqsl SFI / K / A indices + band conditions
wspr_monitor.py       # WSPR propagation per target dot (wspr.live)
ft8_monitor.py        # FT8 propagation per target dot (PSKReporter MQTT)
settings_panel.py     # left-edge settings flyout (volumes, chime, spot mode)
alerts.py             # synthesised band-opening chimes (no sound files)
audio_out.py          # PipeWire routing + volume for the TV output
metros.py             # largest cities in a grid field, for dot bubbles
greyline.py           # day/night terminator overlay
geo.py                # map projection / coordinate math
geo_boundaries.py     # coastline / country / state rendering
maidenhead.py         # Maidenhead grid <-> lat/lon
header_ui.py          # top header bar (callsign, clock, time-sync bubble)
chrony_status.py      # query chrony tracking / sources
gps_status.py         # query gpsd GNSS stats
overlays.py           # misc on-map overlays
map_tiles.py          # high-res tile loading on deep zoom
make_tiles.py         # utility: generate tiles from a source image
applog.py             # logging setup

data/                 # boundaries, map tiles, caches (caches gitignored)
images/               # base world maps (NASA Blue Marble, etc.)
fonts/                # bundled monospace fonts
```

## Map tiles

High-resolution tiles live in `data/maptiles/` and are used when zoomed in. They
can be regenerated with `make_tiles.py` from a high-res source image (needs
Pillow).

## Troubleshooting

```bash
./run.sh                          # run in the foreground to see errors live
journalctl --user -u pimap -e     # service logs
tail -f logs/piMap.log            # app log
```

- **Black screen / wrong monitor:** the service renders on HDMI-2 by default.
  Check `xrandr` and adjust the `--output` line in `deploy/pimap.service`.
- **No QSOs:** verify `QRZ_API_KEY` and `QRZ_ENABLE = True` in `config.py`; the
  key comes from your QRZ Logbook settings page (not your QRZ account password).
- **Satellite tag amber/red:** Celestrak TLE fetch is failing; it retries every
  15 min and recovers on its own once the network returns.
- **Service won't start at boot:** ensure linger is on
  (`loginctl enable-linger <user>`) and that the X session is up on `:0`.

## License

MIT — see [LICENSE](LICENSE).

Bundled third-party assets keep their own licenses: the NASA Blue Marble imagery
is public domain, and the fonts in `fonts/` (Anonymous Pro, B612 Mono, JetBrains
Mono, Overpass Mono, Share Tech Mono, Space Mono) are distributed under their
respective open-font licenses.

73
