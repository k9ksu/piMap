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