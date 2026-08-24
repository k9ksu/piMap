# piMap configuration — copy to config.py and fill in your values.
#   cp config.example.py config.py   (config.py is gitignored)

# ==== Your QTH (home station) ====
HOME_LAT = 0.0   # your station latitude (decimal degrees)
HOME_LON = 0.0   # your station longitude (decimal degrees)
QTH_COLOR = "#191A19"   # home-station dot color (hex)

# ==== Map file ====
#WORLD_MAP_FILE = "PathfinderMap_hires.jpg"  # equirectangular world map
WORLD_MAP_FILE = "images/world.200406.3x5400x2700.jpg"
MAP_BRIGHTNESS = 1.15   # >1 brightens the map, <1 darkens

# ==== Greyline (day/night) overlay ====
GREYLINE_ENABLED = True
NIGHT_ALPHA = 75        # darkness of the night side (0-255)


# ==== Layout ====
SPACE_BAR_HEIGHT = 140  # pixels reserved at top for info bar

# Compact HF band-conditions strip (HamQSL) along the bottom of the header.
# Shows the day- or night-relevant Good/Fair/Poor rating per band group.
BAND_CONDITIONS_ENABLED = True

# ==== WSPR target-dot propagation monitor ====
# Left-click the map to drop a target dot; hover it + press Delete to remove.
# Each dot turns green/yellow/red for recent WSPR propagation between your grid
# FIELD and the target's field, per band (data from wspr.live). Mouseover shows
# the per-band table; an audio chime fires when a band opens.
WSPR_TARGETS_ENABLED = True
WSPR_WINDOW_MINUTES = 30            # spot look-back window
WSPR_POLL_SECONDS = 300            # query interval (be polite to wspr.live)
WSPR_GREEN_MIN_SPOTS = 2           # >= this many recent spots -> green
WSPR_GREEN_MAX_AGE_MIN = 16        # newest spot must be this fresh for green
# When a target drops to nothing-heard, show it "red" for this many polls (a
# recent close), then fade to the quiet white dot. 0 = never show red.
WSPR_RECENT_CLOSE_POLLS = 5
WSPR_BANDS = [1, 3, 7, 10, 14, 18, 21, 24, 28, 50]  # wspr.live band codes (MHz)
# Spotting mode: dots estimate the likelihood of THIS mode from the best WSPR
# SNR on the path. Pick in the Settings flyout (persists to ui_prefs.json).
WSPR_MODE = "FT8"
# (green, yellow) thresholds on best WSPR SNR (dB, 2.5 kHz ref) per mode. Tune to
# taste: raise for a stricter (less green) dot, lower for more optimistic.
WSPR_MODE_THRESHOLDS = {
    "FT8": (-12, -20),   # decodes very weak
    "CW": (-4, -13),     # needs a bit more
    "SSB": (6, -3),      # needs a solid signal
}
TARGET_DOT_RADIUS = 7

# Left SETTINGS flyout: close it after this many seconds with no mouse
# motion, even if the cursor is parked on the hot edge or on the panel
# itself. Without this a cursor left at x<=8 pins the flyout open (and
# holds the render loop at 60 fps). 0 disables the idle timeout.
FLYOUT_IDLE_HIDE_SECONDS = 10

# Max individual spots pulled when you click a band row in a target's
# bubble to see who is actually being heard on that path.
WSPR_DETAIL_MAX_SPOTS = 60

# The "home" end of a WSPR path: spots count only if that end is within this
# many km of HOME_LAT/HOME_LON. Replaces matching on the 2-char Maidenhead
# field, which is a 10x20 degree box you are rarely centered in -- EM reaches
# ~1490 km east of this QTH but only ~240 km west, so field matching reported
# openings that existed only for stations far to the east. 0 = old behavior.
WSPR_HOME_RADIUS_KM = 800

# ==== FT8 monitor (PSKReporter MQTT live feed) ====
# WSPR tells you whether a path is physically open (beacons run 24/7); FT8
# tells you whether it is open AND somebody is on it. Both columns are shown
# side by side in a target dot's bubble.
FT8_ENABLED = True
FT8_MQTT_HOST = "mqtt.pskreporter.info"
FT8_MQTT_PORT = 1883
# "Local" end of an FT8 path. FT8 is so much denser than WSPR that this can be
# far tighter than WSPR_HOME_RADIUS_KM and still have plenty of data --
# 800 km gives ~35 spots/sec, 400 km still gives ~10/sec.
FT8_HOME_RADIUS_KM = 800
FT8_WINDOW_MINUTES = 15        # rolling window kept in memory
FT8_GREEN_MAX_AGE_MIN = 6      # newest decode must be this fresh for green
FT8_GREEN_MIN_STATIONS = 5     # >= this many distinct DX callsigns for green
FT8_YELLOW_MIN_STATIONS = 2    # below this a path is 'weak', not yellow
FT8_BUCKET_MAX_SPOTS = 400     # per (field, band) memory cap
FT8_STATUS_TTL_SECONDS = 2.0   # cache the per-band snapshot this long
# Thresholds on the best FT8 report (dB), per target mode. NOT the same scale
# as WSPR_MODE_THRESHOLDS: FT8 stations run 25-100 W against WSPR's 1-5 W, so
# an identical dB figure means a much weaker path here.
FT8_MODE_THRESHOLDS = {
    "FT8": (-10, -21),
    "CW": (0, -10),
    "SSB": (12, 3),
}
ALERT_SOUND_ENABLED = True         # chime when a dot turns green (silent if no audio)
# Anti-flap floor only: a dot chimes once when it turns green and stays silent
# while it remains green. This is the minimum gap before the SAME target may
# chime again after it has dropped out of green and come back.
WSPR_ALERT_COOLDOWN_SECONDS = 1800
WSPR_ALERT_HIGHLIGHT_SECONDS = 60  # ping-ring an opened dot this long (0 = off)

# ==== Audio output ====
ALERT_VOLUME = 0.8            # chime volume 0..1 (also adjustable in Settings flyout)
# Default chime; pick from the Settings flyout (persists to ui_prefs.json).
# One of: Soft Chime, Marimba, Bell, Music Box, Ascending Triad.
ALERT_SOUND = "Soft Chime"
TV_AUDIO_CARD = "vc4-hdmi-1"  # ALSA card for HDMI-2 (piMap's TV) — the sink we control
TV_VOLUME_MAX = 1.0          # cap for the TV volume slider (1.0 = 100%)
# SDL device to route the chime to (piMap's TV). None = auto-pick the HDMI-2
# device; set an exact name from the log if auto-pick guesses wrong.
ALERT_AUDIO_DEVICE = None

# ==== Zoom ====
# The 5400px base map is sharp to ~2.8x; beyond that high-res tiles (below)
# take over for crisp detail to ~11x. Vector overlays stay crisp at any zoom.
MIN_ZOOM = 1.0
MAX_ZOOM = 11.0
ZOOM_STEP = 1.15   # wheel-notch zoom factor

# ==== High-res map tiles (deep zoom) ====
# Pre-split 21600x10800 imagery (see make_tiles.py). Only the tiles covering
# the view are loaded on demand, so the full image is never all in memory.
TILE_ENABLE = True
TILE_MIN_ZOOM = 3.0   # load high-res tiles once zoom >= this

# ==== Frame rate ====
# Full 60 fps while interacting; drop to this when idle (clock, sats, and
# greyline animate fine at a low rate) to keep CPU/heat down.
IDLE_FPS = 10

# Header callsign font. Options (zero style):
#   AnonymousPro  - clean bold, TRUE diagonal slashed zero
#   ShareTechMono - thin LCD/sci-fi, slashed zero
#   SpaceMono     - retro-geometric, center-bar zero
#   JetBrainsMono - modern crisp, dotted zero
#   OverpassMono  - industrial signage, dotted zero
#   B612Mono      - Airbus cockpit, plain zero
HEADER_FONT = "AnonymousPro"
HEADER_FILL = 0.50         # callsign fills this fraction of the bar height
HEADER_COLOR = "#512888"    # callsign color (hex)

# ==== QRZ Logbook integration ====
QRZ_ENABLE = True  # set False to run without QRZ

# QRZ Logbook API key (from QRZ Logbook settings page)
QRZ_API_KEY = "YOUR_QRZ_LOGBOOK_API_KEY_HERE"

# Used in HTTP User-Agent header
QRZ_USER_AGENT_CALLSIGN = "NOCALL"

# Name of the logbook to display in the top-left corner
QRZ_LOGBOOK_NAME = "My Logbook"

# How many recent QSOs to pull from your logbook
QRZ_MAX_QSOS = 25

# Active-logging session window, in minutes.
# If the most recent QSO is newer than this, the map shows only the current
# logging session (the run of back-to-back QSOs each within this window of the
# next). Once the newest QSO ages past this, it falls back to the last
# QRZ_MAX_QSOS. Set 0 to disable session mode and always show the last N.
QRZ_SESSION_WINDOW_MIN = 30

# QRZ poll cadence (seconds). Adaptive: while a logging session is active the
# cadence ramps from the idle rate down toward the fast floor based on how
# recently the last QSO was logged; when idle it sits at the slow ceiling.
QRZ_REFRESH_SECONDS = 150            # idle ceiling (2.5 min)
QRZ_REFRESH_ACTIVE_MIN_SECONDS = 15  # fast floor while actively logging

# ==== N3FJP API trigger (optional accelerator) ====
# When enabled, piMap connects to the N3FJP logging app's TCP API and does an
# immediate QRZ refresh the instant you log a contact (N3FJP pushes an
# ENTEREVENT), so new QSOs appear without waiting for the poll. QRZ stays the
# source of truth; if N3FJP is off/unreachable, piMap silently falls back to
# normal adaptive polling. MASTER SWITCH — default OFF.
QRZ_N3FJP_TRIGGER_ENABLE = False
QRZ_N3FJP_HOST = "192.168.1.100"     # PC running N3FJP
QRZ_N3FJP_PORT = 1100                # N3FJP API port (Settings -> API)

# QSO great-circle line / endpoint-dot color (hex)
QSO_COLOR = "#512888"
QSO_DOT_RADIUS = 4   # QSO endpoint dot radius (px)

# ==== Political boundary overlay (shown only when zoomed in) ====
BOUNDARY_ENABLE = True
BOUNDARY_MIN_ZOOM = 2.5     # show borders once zoom >= this (MAX_ZOOM is 5.0)
BOUNDARY_COLOR = "#FFFFFF"  # border line color (hex)
BOUNDARY_ALPHA = 0.5        # border opacity 0.0-1.0
BOUNDARY_SMOOTH = 2         # corner-cutting smoothing passes (0 = off, 2-3 = smooth)
BOUNDARY_COASTLINE = True   # also draw coastlines (island/continent outlines)
# ADM0_A3 country codes that get state/province borders (others get only the
# national outline). e.g. USA, CAN, AUS, BRA, MEX, RUS, CHN, IND.
BOUNDARY_STATES_COUNTRIES = ["USA", "CAN"]

# ==== ISS overlay ====
ISS_ICON_FILE = "images/iss.webp"
ISS_ICON_WIDTH_PX = 62
ISS_ICON_HALO_PX = .25       # white outline width so the icon reads over land (0 = off)
ISS_ICON_HALO_ALPHA = .1   # halo opacity 0.0-1.0 (lower = fainter outline)
ISS_ICON_HALO_COLOR = "#FFFFFF"  # halo color (hex)
ISS_ICON_SHADOW_X = -7.5    # drop-shadow X offset px (+right / -left)
ISS_ICON_SHADOW_Y = 7.5    # drop-shadow Y offset px (+down / -up)
ISS_ICON_SHADOW_ALPHA = .1 # drop-shadow opacity 0.0-1.0
ISS_ICON_SHADOW_GRAY = .5  # drop-shadow gray level 0.0=black .. 1.0=white

# Debug/test knobs for the ISS ground track:
#   ISS_TIME_OFFSET_S shifts the starting position along the orbit (seconds).
#   ISS_TIME_SPEED >1 fast-forwards the simulation (e.g. 180 => ~30s orbit).
ISS_TIME_OFFSET_S = 0
ISS_TIME_SPEED = 1.0

# ==== Satellites (markers + pass prediction) ====
SAT_DOT_RADIUS = 6        # non-ISS satellite dot radius (px)
PASS_MIN_ELEVATION = 10.0 # menu sorts by next pass above this elevation (deg)