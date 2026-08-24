# -*- coding: utf-8 -*-
"""
piMap.py - QSO great-circle map with QRZ logbook and space weather.

Controls:
  - Mouse wheel: zoom around cursor (unless mouse is over Sat Panel)
  - Double-click empty map: drop a WSPR target dot (single click just deselects)
  - Left click a dot: pin/unpin its info bubble (does not pan)
  - Left-drag a dot: move it
  - Middle click (or Delete/Backspace) over a target dot: remove it
  - Right click + drag: pan (hand cursor)
  - Right click (no drag): reset view (zoom=1, centered on home QTH)
  - ESC: exit

This module owns app setup, the camera/view math, and the main event/render
loop. Functionality is split across focused modules:
  - config.py         all user settings
  - geo.py            projection + small math (clamp, nice_number, wrap_lon)
  - greyline.py       solar position / day-night terminator math
  - header_ui.py      header fonts, callsign title, QTH-local clock, hex color
  - overlays.py       QSO/sat track lines, sat markers, ISS icon, bubbles, status row
  - qrz_client.py     QRZ logbook fetch + QSO formatting
  - space_weather.py  HamQSL space weather (background)
  - sat_tracker.py    TLE fetch + SGP4 ground tracks + pass scheduler
  - sat_radio.py      SatNOGS transmitter (frequency) lookup
  - geo_boundaries.py country/state borders + coastlines
  - map_tiles.py      on-demand high-res map tiles for deep zoom
  - sat_panel*.py     slide-out satellite selection panel
  - applog.py         rotating log + component status registry

Controls: wheel = zoom, right-drag = pan, right-click = reset, ESC = exit.
"""

import sys
import os
import time
import math
import json
import uuid
import colorsys
from datetime import datetime, timezone, timedelta
import pygame
import pygame.gfxdraw

import config
from geo import (
    lonlat_to_xy,
    xy_to_lonlat,
    great_circle_points,
    split_path_at_dateline,
    clamp,
    nice_number as _nice_number,
)
from maidenhead import maidenhead_to_latlon, latlon_to_field, latlon_to_maidenhead
from greyline import (
    solar_decl_and_eqtime as _solar_decl_and_eqtime_minutes_approx,
    subsolar_lon as _subsolar_lon_deg_approx,
    cos_zenith as _cos_zenith,
    terminator_lat as _terminator_lat_deg_for_lon,
)
from header_ui import (
    make_title as _make_title,
    home_localtime as _home_localtime,
    parse_hex_color as _parse_hex_color,
    header_font_path as _header_font_path,
)
from qrz_client import (
    fetch_recent_qsos, make_qsos_from_qrz, select_session_qsos, poll_interval,
)
from space_weather import get_space_weather

from sat_panel_manager import SatPanelManager
from sat_panel import SatDef
from settings_panel import SettingsPanel
import sat_tracker
import sat_radio
import geo_boundaries
import map_tiles
import applog
import chrony_status
import gps_status
import n3fjp_status
import wspr_monitor
import ft8_monitor
import alerts
import audio_out
import metros
import overlays
from overlays import (
    ISS_NORAD, QSO_DOT_RADIUS,
    draw_gc_line, draw_sat_track, draw_sat_marker, draw_sat_bubble,
    _marker_hit, draw_status_row, draw_band_conditions, draw_target_bubble,
    draw_target_detail_bubble,
    target_summary_color, _sat_color, _fmt_pass,
    iss_simulated_now, _apply_map_brightness,
)

_log = applog.get_logger("piMap")


# ----------------------------- UX TUNING -----------------------------

MIN_ZOOM = float(getattr(config, "MIN_ZOOM", 1.0))
MAX_ZOOM = float(getattr(config, "MAX_ZOOM", 8.0))
ZOOM_STEP = float(getattr(config, "ZOOM_STEP", 1.15))

DRAG_THRESHOLD_PX = 6
DOUBLE_CLICK_S = 0.40   # max gap between the two clicks of a double-click
DOUBLE_CLICK_PX = 8     # and they must land within this many px of each other
IDLE_SMOOTH_DELAY_S = 0.20
LINES_HQ_IDLE_S = 0.18  # supersample lines only after the mouse has been still this long

MAP_BRIGHTNESS = float(getattr(config, "MAP_BRIGHTNESS", 1.15))

SCALEBAR_TARGET_PX = 180
SCALEBAR_PAD_PX = 16
SCALEBAR_HEIGHT_PX = 7

EARTH_CIRCUMFERENCE_KM = 40075.0

GREYLINE_ENABLED = bool(getattr(config, "GREYLINE_ENABLED", True))
NIGHT_ALPHA = int(getattr(config, "NIGHT_ALPHA", 75))
GREYLINE_STEP_PX = 1
TERMINATOR_LINE = False
TERMINATOR_ALPHA = 140
TERMINATOR_WIDTH = 2
GREYLINE_REFRESH_S = 5  # recompute the greyline at most this often (it drifts slowly)


_SUMMARY_RANK = {"quiet": 0, "red": 1, "yellow": 2, "green": 3}


def _merge_summary(wspr_summary, ft8_bands):
    """One dot colour from two independent sources: whichever is more open wins.

    ft8_bands is {band: {..., "state": ...}} for this dot's grid field. FT8
    "weak"/"none" mean heard-but-unusable / not-heard, which display as red the
    same way the WSPR side already does."""
    best = wspr_summary if wspr_summary in _SUMMARY_RANK else "quiet"
    for v in (ft8_bands or {}).values():
        st = v.get("state")
        st = "red" if st in ("weak", "none") else st
        if _SUMMARY_RANK.get(st, 0) > _SUMMARY_RANK.get(best, 0):
            best = st
    return best


def _connector_bounds(name):
    """Return (x, y, w, h) of the X output named `name` (e.g. "HDMI-2").

    Binds piMap to the physical connector rather than an SDL display index,
    which can reorder depending on which monitors are detected at boot. Returns
    None if xrandr is unavailable or the output isn't a connected, active one.
    """
    import subprocess
    import re
    try:
        out = subprocess.check_output(["xrandr", "--query"],
                                      text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    for line in out.splitlines():
        # e.g. "HDMI-2 connected 1920x1080+1920+0 (normal left ...) 160mm x 90mm"
        if line.startswith(name + " ") and " connected" in line:
            m = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
            if m:
                w, h, x, y = (int(v) for v in m.groups())
                return (x, y, w, h)
    return None


def _display_bounds(index):
    """Return (x, y, w, h) for SDL display `index`, or None on failure.

    pygame exposes display *sizes* but not their position in the X screen, so
    we ask SDL directly for the bounds (origin + size) of the target monitor.
    """
    import ctypes

    class _Rect(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int),
                    ("w", ctypes.c_int), ("h", ctypes.c_int)]
    try:
        sdl = ctypes.CDLL("libSDL2-2.0.so.0")
    except OSError:
        return None
    r = _Rect()
    if sdl.SDL_GetDisplayBounds(int(index), ctypes.byref(r)) != 0:
        return None
    return (r.x, r.y, r.w, r.h)


def main():
    chrony_status.start()  # background poller for the header-clock time-sync bubble
    gps_status.start()     # background poller for GNSS satellite stats
    # Optional N3FJP API trigger (default OFF via config); no-op when disabled.
    n3fjp_status.start(
        getattr(config, "QRZ_N3FJP_HOST", "127.0.0.1"),
        getattr(config, "QRZ_N3FJP_PORT", 1100),
        getattr(config, "QRZ_N3FJP_TRIGGER_ENABLE", False))
    pygame.init()

    # piMap lives on HDMI-2. On a Pi where one X screen spans both HDMI outputs,
    # a real-mode-switch FULLSCREEN on the secondary output tends to black out,
    # so we open a borderless window placed exactly over the target monitor —
    # the same approach unifi-wall's mpv uses. We locate the monitor by its
    # connector name (robust to SDL index reordering at boot) and only fall back
    # to an SDL display index, then plain fullscreen, if that can't be resolved.
    output_name = getattr(config, "DISPLAY_OUTPUT", "HDMI-2")
    bounds = _connector_bounds(output_name)
    if not bounds:
        want_display = int(getattr(config, "DISPLAY_INDEX", 1))
        if want_display >= pygame.display.get_num_displays():
            want_display = 0
        bounds = _display_bounds(want_display)
    if bounds:
        x, y, w, h = bounds
        os.environ["SDL_VIDEO_WINDOW_POS"] = "%d,%d" % (x, y)
        screen = pygame.display.set_mode((w, h), pygame.NOFRAME)
    else:
        # Couldn't read monitor geometry at all — last-resort fullscreen.
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("PiMap")
    pygame.event.set_grab(True)  # confine the cursor to the piMap display
    screen_width, screen_height = screen.get_size()
    clock = pygame.time.Clock()

    font_small = pygame.font.SysFont("consolas", 18)
    font_big = pygame.font.SysFont("consolas", 28, bold=True)

    bar_h = getattr(config, "SPACE_BAR_HEIGHT", 90)
    callsign = getattr(config, "QRZ_USER_AGENT_CALLSIGN", "PiMap")
    title_surf, title_pos = _make_title(callsign, bar_h)

    clock_size = max(14, int(bar_h * 0.30))
    try:
        font_clock = pygame.font.Font(_header_font_path(), clock_size)
    except Exception:
        font_clock = pygame.font.SysFont("monospace", clock_size)

    qso_color = _parse_hex_color(getattr(config, "QSO_COLOR", "#FFC800"))
    qth_color = _parse_hex_color(getattr(config, "QTH_COLOR", "#00FF00"))

    boundary_color = _parse_hex_color(getattr(config, "BOUNDARY_COLOR", "#8FA3B8"))
    boundary_min_zoom = float(getattr(config, "BOUNDARY_MIN_ZOOM", 2.5))
    boundary_alpha = float(getattr(config, "BOUNDARY_ALPHA", 0.55))

    tile_mgr = map_tiles.TileManager() if getattr(config, "TILE_ENABLE", True) else None
    tile_min_zoom = float(getattr(config, "TILE_MIN_ZOOM", 3.0))
    idle_fps = int(getattr(config, "IDLE_FPS", 15))
    if tile_mgr:
        _log.info("Map tiles: %s", "available" if tile_mgr.available else "none found")
    if getattr(config, "BOUNDARY_ENABLE", True):
        world_boundaries = geo_boundaries.load(
            state_countries=tuple(getattr(config, "BOUNDARY_STATES_COUNTRIES", ["USA"])),
            smooth_iters=int(getattr(config, "BOUNDARY_SMOOTH", 0)),
            coastline=bool(getattr(config, "BOUNDARY_COASTLINE", True)),
        )
    else:
        world_boundaries = []
    _log.info("Boundaries: loaded %d polylines", len(world_boundaries))

    sat_ui = SatPanelManager(screen_width, screen_height)

    # Left-edge user-settings flyout. First widgets: TV (HDMI-2) output volume
    # and the alert-chime volume + sound picker, plus Mute-TV / Test-chime.
    settings_ui = SettingsPanel(screen_width, screen_height,
                                top=getattr(config, "SPACE_BAR_HEIGHT", 90),
                                idle_hide_s=getattr(config, "FLYOUT_IDLE_HIDE_SECONDS", 10))

    # Small persisted UI prefs (the chosen chime survives restarts).
    prefs_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_prefs.json")
    try:
        with open(prefs_file) as f:
            ui_prefs = json.load(f)
    except (OSError, ValueError):
        ui_prefs = {}

    def _save_prefs():
        try:
            with open(prefs_file, "w") as f:
                json.dump(ui_prefs, f, indent=2)
        except OSError as e:
            _log.warning("could not save prefs: %s", e)

    def _tv_vol_get():
        c = audio_out.get_cached()
        return c[0] if c else 0.0

    def _select_alert_sound(name):
        alerts.set_sound(name)
        alerts.play()               # preview the pick
        ui_prefs["alert_sound"] = name
        _save_prefs()

    def _select_spot_mode(name):
        wspr_monitor.set_mode(name)
        ft8_monitor.set_mode(name)
        ui_prefs["spot_mode"] = name
        _save_prefs()

    # Apply the persisted spotting mode before the monitor's first poll.
    ft8_monitor.set_mode(ui_prefs.get("spot_mode",
                          getattr(config, "WSPR_MODE", "FT8")))
    wspr_monitor.set_mode(ui_prefs.get("spot_mode",
                                       getattr(config, "WSPR_MODE", "FT8")))

    settings_ui.set_sliders([
        {"key": "tv", "label": "TV Volume (HDMI-2)", "get": _tv_vol_get,
         "set": audio_out.set_volume},
        {"key": "alert", "label": "Alert Chime", "get": alerts.get_volume,
         "set": alerts.set_volume},
    ])
    settings_ui.set_choosers([
        {"label": "Spot Mode (dot = mode likelihood)", "options": wspr_monitor.modes,
         "get": wspr_monitor.get_mode, "on_select": _select_spot_mode},
        {"label": "Alert Sound (click to hear)", "options": alerts.sound_names,
         "get": alerts.current_sound, "on_select": _select_alert_sound},
    ])
    settings_ui.set_buttons([
        {"key": "test", "label": "Test Chime", "on_click": alerts.play},
        {"key": "mute",
         "label": lambda: ("Unmute TV" if (audio_out.get_cached() or (0, False))[1]
                           else "Mute TV"),
         "on_click": audio_out.toggle_mute},
    ])

    try:
        map_path = os.path.join(os.path.dirname(__file__), config.WORLD_MAP_FILE)
        world_img = pygame.image.load(map_path).convert()
    except Exception as e:
        print(f"Error loading map file {config.WORLD_MAP_FILE}: {e}")
        pygame.quit()
        sys.exit(1)

    world_img = _apply_map_brightness(world_img, MAP_BRIGHTNESS)
    map_width, map_height = world_img.get_size()

    usable_height = screen_height - getattr(config, "SPACE_BAR_HEIGHT", 90)
    base_scale = min(float(screen_width) / float(map_width),
                     float(usable_height) / float(map_height))

    render_width = int(map_width * base_scale)
    render_height = int(map_height * base_scale)

    offset_x = (screen_width - render_width) // 2
    offset_y = getattr(config, "SPACE_BAR_HEIGHT", 90) + (usable_height - render_height) // 2

    home_latlon = (config.HOME_LAT, config.HOME_LON)
    home_mx, home_my = lonlat_to_xy(home_latlon[1], home_latlon[0], map_width, map_height)

    # ---- WSPR target dots: user-placed points whose propagation to the QTH is
    # monitored via wspr.live. Persisted to targets.json next to this script. ----
    wspr_enabled = getattr(config, "WSPR_TARGETS_ENABLED", True)
    home_field = latlon_to_field(config.HOME_LAT, config.HOME_LON)
    targets_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "targets.json")
    targets = []

    def _save_targets():
        try:
            with open(targets_file, "w") as f:
                json.dump(targets, f, indent=2)
        except Exception as e:
            _log.warning("could not save targets: %s", e)

    ft8_enabled = getattr(config, "FT8_ENABLED", True)

    def _reconfig_wspr():
        if wspr_enabled:
            wspr_monitor.configure(home_field, targets)
        if ft8_enabled:
            ft8_monitor.configure(targets)

    def _add_target(lat, lon):
        t = {
            "id": uuid.uuid4().hex[:8],
            "lat": round(lat, 4), "lon": round(lon, 4),
            "field": latlon_to_field(lat, lon),
            "label": latlon_to_maidenhead(lat, lon, 4),
        }
        targets.append(t)
        _save_targets()
        _reconfig_wspr()
        _log.info("Target added: %s (field %s)", t["label"], t["field"])

    def _remove_target(tid):
        nonlocal targets
        targets = [t for t in targets if t["id"] != tid]
        alerted_until.pop(tid, None)   # else the deleted dot's ping entry lingers
        _save_targets()
        _reconfig_wspr()

    def _finalize_target_move(tid):
        # After a drag, recompute the dot's grid field/label and re-query WSPR
        # (its field, and thus its monitored path, may have changed).
        for t in targets:
            if t["id"] == tid:
                t["field"] = latlon_to_field(t["lat"], t["lon"])
                t["label"] = latlon_to_maidenhead(t["lat"], t["lon"], 4)
                _log.info("Target moved: %s (field %s)", t["label"], t["field"])
                break
        _save_targets()
        _reconfig_wspr()

    if wspr_enabled:
        try:
            with open(targets_file) as f:
                targets = json.load(f)
        except FileNotFoundError:
            targets = []
        except Exception as e:
            _log.warning("could not load targets: %s", e)
            targets = []
        for t in targets:  # backfill derived fields for older/hand-edited files
            t.setdefault("id", uuid.uuid4().hex[:8])
            t.setdefault("field", latlon_to_field(t["lat"], t["lon"]))
            t.setdefault("label", latlon_to_maidenhead(t["lat"], t["lon"], 4))
        _reconfig_wspr()
        alerts.init(getattr(config, "ALERT_SOUND_ENABLED", True),
                    devicename=getattr(config, "ALERT_AUDIO_DEVICE", None),
                    volume=float(getattr(config, "ALERT_VOLUME", 0.8)),
                    sound=ui_prefs.get("alert_sound",
                                       getattr(config, "ALERT_SOUND", "Soft Chime")))
        metros.start()  # background load of the city dataset for dot bubbles
        _log.info("WSPR monitor: %d target(s), home end = %s of %.2f,%.2f (field %s)",
                  len(targets), wspr_monitor.home_scope_label(),
                  getattr(config, "HOME_LAT", 0.0), getattr(config, "HOME_LON", 0.0),
                  home_field)
        if ft8_enabled:
            _log.info("FT8 monitor enabled (PSKReporter MQTT, home end = %s)",
                      ft8_monitor.home_scope_label())

    audio_out.start()   # TV (HDMI-2) volume control for the Settings flyout
    audio_out.refresh()
    # The default sink is HDMI-1; move piMap's chime stream to HDMI-2 (the TV).
    audio_out.route_pid_to_tv(os.getpid())

    # qrz_all  = full last-N list from QRZ (with timestamps) — the fallback set.
    # qrz_qsos = what's drawn: current logging session if active, else qrz_all.
    qrz_records = []
    qrz_all = []
    qrz_qsos = []
    qrz_last_fetch = 0.0
    qrz_session_active = False

    def _apply_session_filter(all_qsos):
        return select_session_qsos(
            all_qsos, getattr(config, "QRZ_SESSION_WINDOW_MIN", 0))

    sat_tracker.preload_async()
    sat_radio.preload_async()

    if getattr(config, "QRZ_ENABLE", False):
        applog.set_status("qrz", "loading", "")
        _log.info("QRZ: initial load")
        qrz_records, err = fetch_recent_qsos()
        if err:
            applog.set_status("qrz", "failed", err)
            _log.warning("QRZ error: %s", err)
        else:
            qrz_all = make_qsos_from_qrz(qrz_records, home_latlon)
            qrz_qsos, qrz_session_active = _apply_session_filter(qrz_all)
            applog.set_status("qrz", "ok", f"{len(qrz_qsos)} QSOs")
            _log.info("QRZ: loaded %d QSOs (%s)", len(qrz_qsos),
                      "session" if qrz_session_active else "recent")
            qrz_last_fetch = time.time()

    zoom = 1.0
    cam_cx = home_mx
    cam_cy = home_my

    # Pan gesture state. Either the left button (button 1) or the right button
    # (button 3) can drag the map. pan_button records which one started the
    # gesture so a release knows whether it should also reset the view — a
    # right-click that never became a drag still means "reset", as before.
    pan_active = False
    dragging = False
    pan_button = 0
    down_pos = (0, 0)
    down_cam = (cam_cx, cam_cy)

    # Target-dot drag: which dot (id) the left button grabbed, and whether the
    # pointer has moved past the threshold yet (a grab with no move = pin click).
    drag_target = None
    dot_moved = False

    # target_id -> time.time() until which a "just opened" ping ring is drawn.
    alerted_until = {}
    alert_highlight_s = float(getattr(config, "WSPR_ALERT_HIGHLIGHT_SECONDS", 60))

    # Double-click tracking for placing dots (single click just deselects).
    last_lclick_t = 0.0
    last_lclick_pos = (0, 0)

    last_interaction = 0.0
    last_motion = time.time()  # so the idle-hide timer starts fresh
    cursor_hidden = False
    cursor_hide_s = float(getattr(config, "CURSOR_HIDE_SECONDS", 5))
    cached_key = None
    cached_scaled = None
    frame_cache = None
    frame_cache_t = 0.0
    track_overlay = None
    track_overlay_key = None
    boundary_overlay = None
    boundary_key = None
    tile_overlay = None
    tile_key = None
    qrz_version = 0
    sat_catalog = []
    hover_targets = {}      # key -> ((sx, sy), kind, payload), refreshed each render
    pinned_key = None
    # Geometry of the target bubble drawn on the PREVIOUS frame, so the cursor
    # can move off the dot and into the bubble without it vanishing. One frame
    # of lag is invisible and avoids laying the bubble out twice per frame.
    bubble_layout = None    # {"rect":..., "band_rows":[(band, src, Rect)], ...} or None
    bubble_key = None       # hover_targets key that bubble_layout belongs to
    # Which (source, target, band) cell the drill-down is showing, if any.
    detail_req = None       # {"source": "ft8"|"wspr", "target_id":, "field":, "band":}
    ss_overlay = None
    SS_SCALE = 2

    def inside_map_rect(sx, sy):
        return (offset_x <= sx <= offset_x + render_width) and (offset_y <= sy <= offset_y + render_height)

    def get_src_rect(z, cx, cy):
        src_w = int(map_width / z)
        src_h = int(map_height / z)

        left = int(cx - src_w * 0.5)
        top = int(cy - src_h * 0.5)

        left = clamp(left, 0, map_width - src_w)
        top = clamp(top, 0, map_height - src_h)

        return pygame.Rect(left, top, src_w, src_h)

    def clamp_camera():
        nonlocal cam_cx, cam_cy
        src = get_src_rect(zoom, cam_cx, cam_cy)
        cam_cx = src.left + src.width * 0.5
        cam_cy = src.top + src.height * 0.5

    def screen_to_map(sx, sy, z, cx, cy):
        src = get_src_rect(z, cx, cy)
        u = (sx - offset_x) / float(render_width)
        v = (sy - offset_y) / float(render_height)
        mx = src.left + u * src.width
        my = src.top + v * src.height
        return mx, my

    def map_to_screen(mx, my):
        src = get_src_rect(zoom, cam_cx, cam_cy)
        sx = offset_x + int((mx - src.left) * (render_width / float(src.width)))
        sy = offset_y + int((my - src.top) * (render_height / float(src.height)))
        return sx, sy

    def reset_view():
        nonlocal zoom, cam_cx, cam_cy, last_interaction
        zoom = 1.0
        cam_cx = home_mx
        cam_cy = home_my
        clamp_camera()
        last_interaction = time.time()

    def set_map_cursor(hand):
        # Show the hand cursor while dragging the map, arrow otherwise. The
        # idle-hide logic (set_visible) is independent of shape, so this stays
        # in effect across a hide/show cycle. Wrapped defensively — a cursor
        # failure must never take the map down.
        try:
            pygame.mouse.set_cursor(
                pygame.SYSTEM_CURSOR_HAND if hand else pygame.SYSTEM_CURSOR_ARROW
            )
        except Exception:
            pass

    def apply_zoom_at_cursor(new_zoom, mouse_x, mouse_y):
        nonlocal zoom, cam_cx, cam_cy, last_interaction

        if not inside_map_rect(mouse_x, mouse_y):
            return

        new_zoom = clamp(new_zoom, MIN_ZOOM, MAX_ZOOM)
        if abs(new_zoom - zoom) < 1e-6:
            return

        mx, my = screen_to_map(mouse_x, mouse_y, zoom, cam_cx, cam_cy)
        u = (mouse_x - offset_x) / float(render_width)
        v = (mouse_y - offset_y) / float(render_height)

        new_src_w = map_width / float(new_zoom)
        new_src_h = map_height / float(new_zoom)

        new_left = mx - u * new_src_w
        new_top = my - v * new_src_h

        new_left = clamp(new_left, 0.0, map_width - new_src_w)
        new_top = clamp(new_top, 0.0, map_height - new_src_h)

        cam_cx = new_left + new_src_w * 0.5
        cam_cy = new_top + new_src_h * 0.5
        zoom = new_zoom

        last_interaction = time.time()
        clamp_camera()

    def km_per_screen_pixel():
        src = get_src_rect(zoom, cam_cx, cam_cy)
        km_per_map_px = EARTH_CIRCUMFERENCE_KM / float(map_width)
        map_px_per_screen_px = src.width / float(render_width)
        return km_per_map_px * map_px_per_screen_px

    def draw_scale_bar(surface):
        kpp = km_per_screen_pixel()
        if kpp <= 0:
            return

        target_km = kpp * float(SCALEBAR_TARGET_PX)
        nice_km = _nice_number(target_km)
        bar_px = int(round(nice_km / kpp))
        bar_px = clamp(bar_px, 60, render_width - 2 * SCALEBAR_PAD_PX)

        label = f"{int(round(nice_km)):,} km"
        label_surf = font_small.render(label, True, (240, 240, 246))
        label_sh = font_small.render(label, True, (0, 0, 0))
        label_w, label_h = label_surf.get_size()

        pad_x, pad_y, gap, tick = 12, 7, 5, 5
        content_w = max(bar_px, label_w)
        plate_w = content_w + pad_x * 2
        plate_h = label_h + gap + tick * 2 + pad_y * 2

        plate_x = offset_x + SCALEBAR_PAD_PX
        plate_y = offset_y + render_height - SCALEBAR_PAD_PX - plate_h

        # Floating translucent pill so the map shows through.
        panel = pygame.Surface((plate_w, plate_h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (8, 10, 14, 135), panel.get_rect(), border_radius=8)
        pygame.draw.rect(panel, (255, 255, 255, 45), panel.get_rect(), width=1, border_radius=8)
        surface.blit(panel, (plate_x, plate_y))

        cx = plate_x + plate_w // 2
        label_x = cx - label_w // 2
        label_y = plate_y + pad_y
        surface.blit(label_sh, (label_x + 1, label_y + 1))
        surface.blit(label_surf, (label_x, label_y))

        col = (235, 238, 245)
        bar_x0 = cx - bar_px // 2
        bar_y = label_y + label_h + gap + tick
        pygame.draw.line(surface, col, (bar_x0, bar_y), (bar_x0 + bar_px, bar_y), 2)
        for tx in (bar_x0, bar_x0 + bar_px // 2, bar_x0 + bar_px):
            pygame.draw.line(surface, col, (tx, bar_y - tick), (tx, bar_y + tick), 2)

    greyline_cache = {"key": None, "surf": None}

    def draw_greyline_overlay(surface):
        if not GREYLINE_ENABLED:
            return

        src = get_src_rect(zoom, cam_cx, cam_cy)
        key = (src.left, src.top, src.width, src.height,
               int(time.time() // GREYLINE_REFRESH_S))
        if greyline_cache["key"] != key:
            utc = time.gmtime()
            decl, eqtime = _solar_decl_and_eqtime_minutes_approx(utc)
            sub_lon = _subsolar_lon_deg_approx(utc, eqtime)

            overlay = pygame.Surface((render_width, render_height), pygame.SRCALPHA)
            terminator_pts = []
            for sx in range(0, render_width, GREYLINE_STEP_PX):
                u = sx / float(render_width)
                map_x = src.left + u * src.width

                lon_deg, _ = xy_to_lonlat(map_x, src.top, map_width, map_height)
                lat_term = _terminator_lat_deg_for_lon(lon_deg, decl, sub_lon)

                _, term_y_map = lonlat_to_xy(lon_deg, lat_term, map_width, map_height)
                v = (term_y_map - src.top) / float(src.height)
                sy_term = clamp(int(v * render_height), 0, render_height)
                terminator_pts.append((sx, sy_term))

                cosz = _cos_zenith(math.radians(lat_term + 1.0), lon_deg, decl, sub_lon)
                if cosz < 0:
                    rect = pygame.Rect(sx, 0, GREYLINE_STEP_PX, sy_term)
                else:
                    rect = pygame.Rect(sx, sy_term, GREYLINE_STEP_PX, render_height - sy_term)
                overlay.fill((0, 0, 0, NIGHT_ALPHA), rect)

            if TERMINATOR_LINE and len(terminator_pts) > 2:
                core_col = (255, 255, 255, TERMINATOR_ALPHA)
                pygame.draw.aalines(overlay, core_col, False, terminator_pts)
                pygame.draw.lines(overlay, core_col, False, terminator_pts, TERMINATOR_WIDTH)

            greyline_cache["key"] = key
            greyline_cache["surf"] = overlay

        surface.blit(greyline_cache["surf"], (offset_x, offset_y))

    running = True
    while running:
        # Adaptive frame rate: full 60 fps while interacting (smooth cursor /
        # pan / zoom / panel), throttle to IDLE_FPS when the scene is just
        # slowly animating (clock, sats, greyline) so we don't peg a core.
        now_t = time.time()
        panel_animating = abs(sat_ui.panel.x - sat_ui.panel.target_x) > 1
        ping_active = any(now_t < exp for exp in alerted_until.values())
        interacting = (dragging or pan_active or panel_animating or ping_active
                       or settings_ui.visible()
                       or (now_t - last_motion) < 0.4
                       or (now_t - last_interaction) < 0.4)
        dt = clock.tick(60 if interacting else idle_fps) / 1000.0
        mouse_pos = pygame.mouse.get_pos()
        sat_ui.update(dt, mouse_pos, now_t - last_motion)
        # Pass the idle clock so the flyout also closes when the mouse simply
        # stops moving, not only when the cursor leaves it.
        settings_ui.update(dt, now_t - last_motion)

        # Hide the cursor after a period of no mouse motion; show it on movement.
        # Toggle only on change so we aren't calling SDL every frame.
        if cursor_hide_s > 0:
            want_hidden = (now_t - last_motion) >= cursor_hide_s
            if want_hidden != cursor_hidden:
                pygame.mouse.set_visible(not want_hidden)
                cursor_hidden = want_hidden

        # Build the full sat catalog once the TLEs have loaded, then start the
        # background pass scheduler.
        if not sat_catalog:
            avail = sat_tracker.all_sats()
            if avail:
                sat_catalog = [
                    SatDef(sat_id=name, label=name, norad_id=norad,
                           color=((0, 0, 0) if norad == ISS_NORAD else _sat_color(i)))
                    for i, (norad, name) in enumerate(avail)
                ]
                sat_ui.set_catalog(sat_catalog)
                sat_tracker.start_pass_scheduler(config.HOME_LAT, config.HOME_LON)

        # Re-order the menu by next pass (ISS pinned top) with live countdowns.
        if sat_catalog:
            _, sched = sat_tracker.get_schedule()
            now_real = time.time()

            def _pass_key(d, _s=sched):
                if d.norad_id == ISS_NORAD:
                    return (0, 0.0)
                p = _s.get(d.norad_id)
                return (1, p if p is not None else float("inf"))

            ordered = sorted(sat_catalog, key=_pass_key)
            sat_ui.set_rows([(d, _fmt_pass(sched.get(d.norad_id), now_real)) for d in ordered])

        if getattr(config, "QRZ_ENABLE", False) and getattr(config, "QRZ_REFRESH_SECONDS", 0) > 0:
            # Adaptive cadence: ramp from the idle ceiling down to the fast
            # floor based on how fresh the last QSO is (see poll_interval).
            window_min = getattr(config, "QRZ_SESSION_WINDOW_MIN", 0)
            idle_s = config.QRZ_REFRESH_SECONDS
            floor_s = getattr(config, "QRZ_REFRESH_ACTIVE_MIN_SECONDS", idle_s)
            interval = poll_interval(qrz_all, window_min, idle_s, floor_s)

            # N3FJP logging activity also counts as "active": recent log events
            # pull the cadence off idle toward the fast floor, so a fresh
            # session is picked up without an immediate per-QSO fetch. The
            # normal fast polling then collects the new contact from QRZ.
            n3fjp_ts = n3fjp_status.get().get("last_event_ts", 0.0)
            if window_min > 0 and n3fjp_ts:
                age = time.time() - n3fjp_ts
                win_s = window_min * 60.0
                if age < win_s:
                    frac = max(0.0, min(1.0, age / win_s))
                    interval = min(interval, floor_s + (idle_s - floor_s) * frac)

            if (time.time() - qrz_last_fetch) >= interval:
                qrz_records, err = fetch_recent_qsos()
                if not err:
                    qrz_all = make_qsos_from_qrz(qrz_records, home_latlon)
                    qrz_last_fetch = time.time()
                else:
                    applog.set_status("qrz", "failed", err)
                    _log.warning("QRZ refresh error: %s", err)
                    qrz_last_fetch = time.time() - config.QRZ_REFRESH_SECONDS + 30

            # Re-derive the displayed set each tick (cheap; uses cached list) so
            # the view flips to last-N the instant the session goes stale.
            new_qsos, new_active = _apply_session_filter(qrz_all)
            if len(new_qsos) != len(qrz_qsos) or new_active != qrz_session_active:
                qrz_qsos = new_qsos
                qrz_session_active = new_active
                qrz_version += 1
                applog.set_status("qrz", "ok", f"{len(qrz_qsos)} QSOs")
                _log.info("QRZ: %d QSOs (%s)", len(qrz_qsos),
                          "session" if qrz_session_active else "recent")

        force_render = False
        for event in pygame.event.get():
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                              pygame.MOUSEWHEEL, pygame.KEYDOWN):
                force_render = True

            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

            elif (event.type == pygame.KEYDOWN
                  and event.key in (pygame.K_DELETE, pygame.K_BACKSPACE)):
                # Delete/Backspace removes the target dot under the cursor — a
                # bonus for setups where the window has keyboard focus (the kiosk
                # here does not, so middle-click is the reliable remove).
                if wspr_enabled:
                    mxp, myp = pygame.mouse.get_pos()
                    hk = _marker_hit(hover_targets, mxp, myp)
                    if hk is not None and hk[0] == "target":
                        _remove_target(hk[1])
                        if pinned_key == hk:
                            pinned_key = None

            # Settings flyout (left edge) gets first crack; if it consumes the
            # event, don't let it fall through to map pan/zoom/dot handling.
            if settings_ui.handle_event(event):
                continue

            # Let panel consume clicks first
            if sat_ui.handle_event(event):
                # If it was a wheel, handle separately below
                if event.type not in (pygame.MOUSEWHEEL,):
                    continue

            # Wheel routing: panel scroll vs map zoom
            if event.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if sat_ui.wheel_is_for_panel((mx, my)):
                    sat_ui.on_wheel(event.y)
                else:
                    if event.y != 0:
                        apply_zoom_at_cursor(zoom * (ZOOM_STEP ** event.y), mx, my)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos

                # Legacy wheel buttons on some setups
                if event.button == 4:
                    if not sat_ui.wheel_is_for_panel((mx, my)):
                        apply_zoom_at_cursor(zoom * ZOOM_STEP, mx, my)
                    else:
                        sat_ui.on_wheel(+1)
                    continue
                if event.button == 5:
                    if not sat_ui.wheel_is_for_panel((mx, my)):
                        apply_zoom_at_cursor(zoom / ZOOM_STEP, mx, my)
                    else:
                        sat_ui.on_wheel(-1)
                    continue

                if event.button == 2:
                    # Middle button removes the WSPR target dot under the cursor.
                    if wspr_enabled:
                        hk = _marker_hit(hover_targets, mx, my)
                        if hk is not None and hk[0] == "target":
                            _remove_target(hk[1])
                            if pinned_key == hk:
                                pinned_key = None
                    continue

                if event.button == 3:
                    # Right button on the map: begin a hand-cursor pan. If the
                    # pointer never moves past the drag threshold, MOUSEBUTTONUP
                    # treats it as the reset-view click it has always been.
                    # A right-click off the map resets immediately, as before.
                    if inside_map_rect(mx, my):
                        pan_active = True
                        dragging = False
                        pan_button = 3
                        down_pos = (mx, my)
                        down_cam = (cam_cx, cam_cy)
                        set_map_cursor(True)
                    else:
                        reset_view()
                    continue

                if event.button == 1 and not sat_ui.wheel_is_for_panel((mx, my)):
                    # A click inside an open target bubble belongs to the bubble,
                    # never to the map underneath it: a band row drills into that
                    # band, the back row returns, and anything else is swallowed
                    # so it can't drop a dot or dismiss the bubble you're using.
                    if (bubble_layout is not None
                            and bubble_layout["rect"].collidepoint(mx, my)):
                        entry = hover_targets.get(bubble_key)
                        tgt = entry[2][0] if (entry and entry[1] == "target") else None
                        back = bubble_layout.get("back")
                        if back is not None and back.collidepoint(mx, my):
                            detail_req = None
                            wspr_monitor.clear_detail()
                        elif tgt is not None:
                            for band_code, src, brect in bubble_layout.get("band_rows", []):
                                if not brect.collidepoint(mx, my):
                                    continue
                                detail_req = {"source": src, "target_id": tgt["id"],
                                              "field": tgt.get("field", ""),
                                              "band": band_code}
                                if src == "wspr":
                                    # WSPR detail is a network fetch; FT8 spots
                                    # are already in memory, nothing to request.
                                    wspr_monitor.request_detail(
                                        tgt["id"], tgt.get("field", ""), band_code,
                                        tgt.get("label", ""))
                                # Pin it, or the bubble would close the moment
                                # the cursor drifted off while reading.
                                pinned_key = bubble_key
                                break
                        continue
                    hit = _marker_hit(hover_targets, mx, my)
                    if hit is not None and hit[0] == "target":
                        # Grab a target dot: drag to move it, or release without
                        # moving to pin/unpin its bubble (decided on button-up).
                        drag_target = hit[1]
                        dot_moved = False
                        down_pos = (mx, my)
                    elif hit is not None:
                        # Click a sat/QSO dot: toggle its pinned info bubble.
                        pinned_key = None if pinned_key == hit else hit
                    elif inside_map_rect(mx, my):
                        # Empty map: a DOUBLE-click drops a dot (deliberate);
                        # a single click just dismisses any pinned bubble, so an
                        # idle click doesn't litter the map with targets.
                        tnow = time.time()
                        is_double = (
                            tnow - last_lclick_t <= DOUBLE_CLICK_S
                            and abs(mx - last_lclick_pos[0]) <= DOUBLE_CLICK_PX
                            and abs(my - last_lclick_pos[1]) <= DOUBLE_CLICK_PX)
                        if wspr_enabled and is_double:
                            mmx, mmy = screen_to_map(mx, my, zoom, cam_cx, cam_cy)
                            lon, lat = xy_to_lonlat(mmx, mmy, map_width, map_height)
                            _add_target(lat, lon)
                            last_lclick_t = 0.0  # consume, so a 3rd click won't re-add
                        else:
                            pinned_key = None
                            last_lclick_t = tnow
                            last_lclick_pos = (mx, my)
                    else:
                        # Nothing to add: just dismiss any pinned bubble.
                        pinned_key = None

            elif event.type == pygame.MOUSEMOTION:
                last_motion = time.time()

                if drag_target is not None:
                    # Moving a grabbed target dot: once past the threshold, the
                    # dot follows the cursor live (field is recomputed on release).
                    mx, my = event.pos
                    if (not dot_moved) and (abs(mx - down_pos[0]) >= DRAG_THRESHOLD_PX
                                            or abs(my - down_pos[1]) >= DRAG_THRESHOLD_PX):
                        dot_moved = True
                        set_map_cursor(True)
                    if dot_moved:
                        mmx, mmy = screen_to_map(mx, my, zoom, cam_cx, cam_cy)
                        lon, lat = xy_to_lonlat(mmx, mmy, map_width, map_height)
                        for t in targets:
                            if t["id"] == drag_target:
                                t["lat"], t["lon"] = round(lat, 4), round(lon, 4)
                                break
                        last_interaction = time.time()
                    continue

                if not pan_active:
                    continue

                mx, my = event.pos
                dx = mx - down_pos[0]
                dy = my - down_pos[1]

                if (not dragging) and (abs(dx) >= DRAG_THRESHOLD_PX or abs(dy) >= DRAG_THRESHOLD_PX):
                    dragging = True
                    set_map_cursor(True)  # hand while actually dragging (right button)

                if dragging:
                    effective_scale = base_scale * zoom
                    cam_cx = down_cam[0] - (dx / float(effective_scale))
                    cam_cy = down_cam[1] - (dy / float(effective_scale))
                    clamp_camera()
                    last_interaction = time.time()

            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1 and drag_target is not None:
                    if dot_moved:
                        _finalize_target_move(drag_target)
                    else:
                        # Grab-and-release without moving = pin/unpin the bubble.
                        key = ("target", drag_target)
                        pinned_key = None if pinned_key == key else key
                        alerted_until.pop(drag_target, None)  # acknowledge the ping
                    set_map_cursor(False)
                    drag_target = None
                    dot_moved = False

                elif pan_active and event.button == pan_button:
                    was_dragging = dragging
                    pan_active = False
                    dragging = False
                    set_map_cursor(False)
                    # A right-click that never turned into a drag = reset view.
                    if pan_button == 3 and not was_dragging:
                        reset_view()
                    pan_button = 0

        # ----------------------------- DRAW -----------------------------

        # While the pointer is merely moving (not dragging), the world is
        # identical frame-to-frame — re-blit the cached frame and just let the
        # cursor move, so it stays smooth instead of waiting on a full re-render.
        # Skip the shortcut on click/scroll/key frames, and while the flyout menu
        # is actually slid out (so it animates/responds) — not merely when the
        # cursor is in the region where it would eventually appear.
        moving = (time.time() - last_motion) <= LINES_HQ_IDLE_S
        panel_visible = (sat_ui.panel.x < sat_ui.panel.hidden_x - 1) or settings_ui.visible()
        fresh_cache = frame_cache is not None and (now_t - frame_cache_t) < 0.5
        if (moving and not dragging and drag_target is None and not force_render
                and not panel_visible and fresh_cache):
            screen.blit(frame_cache, (0, 0))
            pygame.display.flip()
            continue

        screen.fill((0, 0, 0))

        pygame.draw.rect(
            screen,
            (12, 12, 14),
            pygame.Rect(0, 0, screen_width, getattr(config, "SPACE_BAR_HEIGHT", 90))
        )

        screen.blit(title_surf, title_pos)

        # Two-line clock centered in the bar: QTH-local (with seconds) over UTC.
        clock_col = (230, 230, 236)
        lt, lt_label = _home_localtime(config.HOME_LAT, config.HOME_LON)
        local_surf = font_clock.render(lt.strftime("%H:%M:%S ") + lt_label, True, clock_col)
        utc_surf = font_clock.render(time.strftime("%H:%M", time.gmtime()) + " UTC",
                                     True, clock_col)
        clk_gap = 4
        clk_total = local_surf.get_height() + clk_gap + utc_surf.get_height()
        clk_cx = screen_width // 2
        clk_top = bar_h // 2 - clk_total // 2
        local_rect = local_surf.get_rect(midtop=(clk_cx, clk_top))
        utc_rect = utc_surf.get_rect(
            midtop=(clk_cx, clk_top + local_surf.get_height() + clk_gap))
        screen.blit(local_surf, local_rect)
        screen.blit(utc_surf, utc_rect)
        # Hover region for the time-sync bubble (a little padding around the clock).
        clock_rect = local_rect.union(utc_rect).inflate(20, 10)

        sw = get_space_weather()
        sw_text = f"SFI {sw['SFI']}   Kp {sw['Kp']}   A {sw['A']}   Sunspots {sw['Sunspots']}"
        sw_label = font_big.render(sw_text, True, (225, 225, 225))
        sw_rect = sw_label.get_rect(topright=(screen_width - 20, 10))
        screen.blit(sw_label, sw_rect)

        geom_label = font_small.render(f"Geomag: {sw['Geomag']}", True, (255, 176, 0))
        geom_rect = geom_label.get_rect(topright=(screen_width - 20, sw_rect.bottom + 5))
        screen.blit(geom_label, geom_rect)

        # Compact HF band-conditions strip along the bottom of the header,
        # showing the day- or night-relevant ratings for the QTH's current
        # daylight state. Reuses the same greyline solar math as the overlay.
        if getattr(config, "BAND_CONDITIONS_ENABLED", True):
            _utc = time.gmtime()
            _decl, _eqt = _solar_decl_and_eqtime_minutes_approx(_utc)
            _sslon = _subsolar_lon_deg_approx(_utc, _eqt)
            _is_day = _cos_zenith(
                math.radians(config.HOME_LAT), config.HOME_LON, _decl, _sslon) > 0
            draw_band_conditions(
                screen, font_small, sw.get("Bands", {}),
                screen_width // 2, bar_h - font_small.get_height() - 8, _is_day)

        zoom_surf = font_small.render(f"Zoom {zoom:.1f}x", True, (190, 190, 190))
        zoom_rect = zoom_surf.get_rect(topright=(screen_width - 20, geom_rect.bottom + 6))
        screen.blit(zoom_surf, zoom_rect)

        draw_status_row(screen, font_small, screen_width - 20, zoom_rect.bottom + 6)

        use_smooth = (time.time() - last_interaction) > IDLE_SMOOTH_DELAY_S
        src_rect = get_src_rect(zoom, cam_cx, cam_cy)
        cache_key = (src_rect.x, src_rect.y, src_rect.w, src_rect.h, use_smooth)

        if cache_key != cached_key:
            sub = world_img.subsurface(src_rect)
            if use_smooth:
                cached_scaled = pygame.transform.smoothscale(sub, (render_width, render_height))
            else:
                cached_scaled = pygame.transform.scale(sub, (render_width, render_height))
            cached_key = cache_key

        if cached_scaled is not None:
            screen.blit(cached_scaled, (offset_x, offset_y))

        # --- High-res tiles over the base for the visible region (deep zoom) ---
        if tile_mgr and tile_mgr.available and zoom >= tile_min_zoom:
            tkey = (src_rect.x, src_rect.y, src_rect.w, src_rect.h, use_smooth)
            if tkey != tile_key or tile_overlay is None:
                scaler = pygame.transform.smoothscale if use_smooth else pygame.transform.scale
                tile_overlay = pygame.Surface((render_width, render_height), pygame.SRCALPHA)
                lon_a, lat_top = xy_to_lonlat(src_rect.left, src_rect.top, map_width, map_height)
                lon_b, lat_bot = xy_to_lonlat(src_rect.right, src_rect.bottom, map_width, map_height)
                lon_min, lon_max = min(lon_a, lon_b), max(lon_a, lon_b)
                lat_min, lat_max = min(lat_top, lat_bot), max(lat_top, lat_bot)
                for c, r in tile_mgr.visible_tiles(lon_min, lon_max, lat_min, lat_max):
                    surf = tile_mgr.get(c, r)
                    if surf is None:
                        continue
                    tlon0, tlon1, tlat_s, tlat_n = tile_mgr.tile_bounds(c, r)
                    mx0, my0 = lonlat_to_xy(tlon0, tlat_n, map_width, map_height)
                    mx1, my1 = lonlat_to_xy(tlon1, tlat_s, map_width, map_height)
                    sx0, sy0 = map_to_screen(mx0, my0)
                    sx1, sy1 = map_to_screen(mx1, my1)
                    w, h = sx1 - sx0, sy1 - sy0
                    if w > 0 and h > 0:
                        tile_overlay.blit(scaler(surf, (w, h)), (sx0 - offset_x, sy0 - offset_y))
                tile_key = tkey
            screen.blit(tile_overlay, (offset_x, offset_y))
        elif tile_mgr and tile_mgr.cache:
            tile_mgr.clear()  # free tile RAM when zoomed back out

        draw_greyline_overlay(screen)

        # --- Political boundaries + coastlines (only zoomed in; cached per view) ---
        if world_boundaries and zoom >= boundary_min_zoom:
            bkey = (src_rect.x, src_rect.y, src_rect.w, src_rect.h)
            if bkey != boundary_key or boundary_overlay is None:
                boundary_overlay = pygame.Surface((render_width, render_height), pygame.SRCALPHA)
                # Visible lat/lon window (+margin) for offscreen culling.
                lon_a, lat_a = xy_to_lonlat(src_rect.left, src_rect.top, map_width, map_height)
                lon_b, lat_b = xy_to_lonlat(src_rect.right, src_rect.bottom, map_width, map_height)
                vlon0, vlon1 = min(lon_a, lon_b) - 2, max(lon_a, lon_b) + 2
                vlat0, vlat1 = min(lat_a, lat_b) - 2, max(lat_a, lat_b) + 2
                for pts_ll, (b0, b1, c0, c1) in world_boundaries:
                    if b1 < vlat0 or b0 > vlat1 or c1 < vlon0 or c0 > vlon1:
                        continue
                    for seg in split_path_at_dateline(pts_ll):
                        pts = []
                        for lat, lon in seg:
                            mx, my = lonlat_to_xy(lon, lat, map_width, map_height)
                            sx, sy = map_to_screen(mx, my)
                            pts.append((sx - offset_x, sy - offset_y))
                        if len(pts) >= 2:
                            pygame.draw.aalines(boundary_overlay, boundary_color, False, pts)
                if boundary_alpha < 1.0:
                    a = max(0, min(255, int(255 * boundary_alpha)))
                    boundary_overlay.fill((255, 255, 255, a), special_flags=pygame.BLEND_RGBA_MULT)
                boundary_key = bkey
            screen.blit(boundary_overlay, (offset_x, offset_y))

        draw_scale_bar(screen)

        # --- Line overlay (QSO + satellite tracks) ---
        home_r = max(4, int(round(8.0 / (zoom ** 0.5))))
        width_qrz = max(1, int(round(2.0 / (zoom ** 0.5))))
        enabled_sats = sat_ui.selected_sats()
        sim_now = iss_simulated_now() if enabled_sats else None
        screen_map_rect = pygame.Rect(offset_x, offset_y, render_width, render_height)

        def render_lines(target, m2s, clip_rect, scale):
            for qso in qrz_qsos:
                (lat1, lon1), (lat2, lon2) = qso["home"], qso["dx"]
                draw_gc_line(
                    target, qso_color, lat1, lon1, lat2, lon2,
                    map_width, map_height, m2s, width=width_qrz * scale
                )
            for sat in enabled_sats:
                draw_sat_track(
                    target, clip_rect, map_width, map_height,
                    m2s, sat.norad_id, sat.color, sim_now, width=3 * scale
                )

        # Supersample (2x + smoothscale) only when the pointer is still; while it
        # moves, draw lines straight to the screen at 1x to keep the frame rate
        # up so the software cursor stays smooth.
        lines_hq = (time.time() - last_motion) > LINES_HQ_IDLE_S

        if lines_hq:
            # Cache the supersampled overlay; rebuild only when the view, the
            # enabled-sat set, the QSO set, or the ~1s track epoch changes.
            track_epoch = int(sim_now) if sim_now else -1
            overlay_key = (src_rect.x, src_rect.y, src_rect.w, src_rect.h,
                           tuple(s.sat_id for s in enabled_sats),
                           qrz_version, track_epoch)
            if overlay_key != track_overlay_key or track_overlay is None:
                ss_w = render_width * SS_SCALE
                ss_h = render_height * SS_SCALE
                if ss_overlay is None or ss_overlay.get_size() != (ss_w, ss_h):
                    ss_overlay = pygame.Surface((ss_w, ss_h), pygame.SRCALPHA)
                ss_overlay.fill((0, 0, 0, 0))

                def map_to_screen_ss(mx, my, _ox=offset_x, _oy=offset_y):
                    sx, sy = map_to_screen(mx, my)
                    return (sx - _ox) * SS_SCALE, (sy - _oy) * SS_SCALE

                render_lines(ss_overlay, map_to_screen_ss,
                             pygame.Rect(0, 0, ss_w, ss_h), SS_SCALE)
                track_overlay = pygame.transform.smoothscale(
                    ss_overlay, (render_width, render_height)
                )
                track_overlay_key = overlay_key
            screen.blit(track_overlay, (offset_x, offset_y))
        else:
            render_lines(screen, map_to_screen, screen_map_rect, 1)

        # --- Markers at 1× on screen: sat icons/dots + QSO endpoint dots ---
        hover_targets = {}
        for sat in enabled_sats:
            pos = draw_sat_marker(screen, screen_map_rect, map_width, map_height,
                                  map_to_screen, sat, sim_now)
            if pos is not None:
                hover_targets[("sat", sat.norad_id)] = (pos, "sat", sat)

        for i, qso in enumerate(qrz_qsos):
            dlat, dlon = qso["dx"]
            qx, qy = map_to_screen(*lonlat_to_xy(dlon, dlat, map_width, map_height))
            cx, cy = int(qx), int(qy)
            if screen_map_rect.collidepoint(cx, cy):
                pygame.gfxdraw.filled_circle(screen, cx, cy, QSO_DOT_RADIUS, qso_color)
                pygame.gfxdraw.aacircle(screen, cx, cy, QSO_DOT_RADIUS, (0, 0, 0))
                pygame.gfxdraw.aacircle(screen, cx, cy, QSO_DOT_RADIUS, qso_color)
                hover_targets[("qso", i)] = ((cx, cy), "qso", qso)

        mx, my = lonlat_to_xy(home_latlon[1], home_latlon[0], map_width, map_height)
        sx, sy = map_to_screen(mx, my)
        pygame.draw.circle(screen, qth_color, (sx, sy), home_r)

        # --- WSPR target dots, colored by propagation status to the QTH ---
        # Defined unconditionally: the bubble block below reads it even when
        # there are no targets or WSPR is disabled.
        ft8_status = {}
        if wspr_enabled and targets:
            wspr_status = wspr_monitor.get_status()
            # FT8 is keyed by grid field (several dots can share one), WSPR by dot id.
            ft8_status = (ft8_monitor.get_status([t.get("field", "") for t in targets])
                          if ft8_enabled else {})
            tdot_r = getattr(config, "TARGET_DOT_RADIUS", 7)
            for t in targets:
                tx, ty = map_to_screen(*lonlat_to_xy(t["lon"], t["lat"], map_width, map_height))
                cx, cy = int(tx), int(ty)
                if not screen_map_rect.collidepoint(cx, cy):
                    continue
                st = wspr_status.get(t["id"], {})
                t_ft8 = ft8_status.get((t.get("field") or "").upper()[:2], {})
                # The bubble keeps the two sources apart, but the dot is a single
                # colour: take the more open verdict, so a band open on either
                # source shows open.
                summary = _merge_summary(st.get("summary", "quiet"), t_ft8)
                if summary in ("green", "yellow", "red"):
                    color = target_summary_color(summary)
                    pygame.gfxdraw.filled_circle(screen, cx, cy, tdot_r, color)
                    pygame.gfxdraw.aacircle(screen, cx, cy, tdot_r, (0, 0, 0))
                    pygame.gfxdraw.aacircle(screen, cx, cy, tdot_r, color)
                else:
                    # Nothing heard (and not a recent close): faint white, black ring.
                    pygame.gfxdraw.filled_circle(screen, cx, cy, tdot_r, (255, 255, 255, 60))
                    pygame.draw.circle(screen, (0, 0, 0), (cx, cy), tdot_r, 2)
                # Recently-opened ping: an expanding, fading ring to draw the eye.
                exp = alerted_until.get(t["id"])
                if exp and now_t < exp:
                    pulse = 0.5 + 0.5 * math.sin(now_t * 5.0)   # smooth 0..1
                    ring_r = tdot_r + 4 + int(round(9 * pulse))
                    a = int(210 * (1.0 - pulse)) + 30
                    ping = target_summary_color(summary) if summary == "green" else (255, 255, 255)
                    pygame.gfxdraw.aacircle(screen, cx, cy, ring_r, (*ping, a))
                    pygame.gfxdraw.aacircle(screen, cx, cy, ring_r - 1, (*ping, a))
                # Grid + per-band detail live in the hover bubble, not on the map.
                hover_targets[("target", t["id"])] = ((cx, cy), "target", (t, st))

        # A band opening (red -> green) chimes once, is logged, and flags its
        # dot with a ping ring so you can see WHICH target just opened.
        if wspr_enabled:
            openings = wspr_monitor.pop_openings()
            for op in openings:
                _log.info("WSPR opening: %s on %s", op["label"], op["band_label"])
                alerted_until[op["target_id"]] = time.time() + alert_highlight_s
            if openings:
                alerts.play()

        # Hovering an alerted dot acknowledges it: stop the ping immediately.
        if alerted_until and not cursor_hidden:
            hov = _marker_hit(hover_targets, mouse_pos[0], mouse_pos[1])
            if hov is not None and hov[0] == "target":
                alerted_until.pop(hov[1], None)

        # --- Info bubble: pinned target, else hovered dot ---
        # When the cursor is hidden (idle), clear all hover bubbles — otherwise
        # a bubble parked under the now-invisible cursor would stay stuck open.
        enabled_norads = {s.norad_id for s in enabled_sats}
        if pinned_key and pinned_key[0] == "sat" and pinned_key[1] not in enabled_norads:
            pinned_key = None  # an unchecked sat drops its pin
        # No bubble while a dot is being dragged — keeps the drag light and
        # avoids the bubble chasing the cursor.
        hovered_key = _marker_hit(hover_targets, mouse_pos[0], mouse_pos[1])
        # Cursor left the dot but is inside the bubble that dot opened: keep it.
        # Only for target bubbles -- sat/QSO bubbles stay dot-hover-only.
        if (hovered_key is None and bubble_layout is not None
                and bubble_key in hover_targets
                and bubble_layout["rect"].collidepoint(mouse_pos)):
            hovered_key = bubble_key
        target_key = None if (cursor_hidden or drag_target is not None) else (
            pinned_key if pinned_key in hover_targets else hovered_key)
        # Drill-down belongs to exactly one dot; moving to another (or to no
        # bubble at all) drops it so the next hover starts on the band summary.
        if detail_req is not None and target_key != ("target", detail_req["target_id"]):
            detail_req = None
            wspr_monitor.clear_detail()
        detail = None
        if detail_req is not None:
            if detail_req["source"] == "ft8":
                # Local: the spots are already in the rolling buckets.
                detail = ft8_monitor.get_detail(detail_req["field"], detail_req["band"])
                detail.update(source="FT8", state="ok", error="", truncated=False,
                              band_label=wspr_monitor.band_label(detail_req["band"]))
            else:
                detail = wspr_monitor.get_detail()
                if detail is not None:
                    detail["source"] = "WSPR"

        new_layout, new_layout_key = None, None
        if target_key is not None and target_key in hover_targets:
            bpos, kind, payload = hover_targets[target_key]
            if kind == "target":
                tgt, st = payload
                if detail is not None:
                    new_layout = draw_target_detail_bubble(
                        screen, bpos, tgt, detail, font_small, font_small,
                        screen_width, getattr(config, "SPACE_BAR_HEIGHT", 90),
                        pinned=(target_key == pinned_key), mouse_pos=mouse_pos)
                else:
                    new_layout = draw_target_bubble(
                        screen, bpos, tgt, st,
                        ft8_status.get((tgt.get("field") or "").upper()[:2], {}),
                        font_small, font_small,
                        screen_width, getattr(config, "SPACE_BAR_HEIGHT", 90),
                        pinned=(target_key == pinned_key),
                        metros=metros.top_metros_in_field(tgt.get("field", "")),
                        mouse_pos=mouse_pos)
                new_layout_key = target_key
            else:
                if kind == "sat":
                    bname, bcolor = payload.label, payload.color
                    blines, bnote = sat_radio.info(payload.norad_id)
                else:
                    bname, bcolor = payload["title"], qso_color
                    blines, bnote = payload["lines"], ""
                draw_sat_bubble(screen, bpos, bname, blines, bnote, bcolor,
                                font_small, font_small, screen_width,
                                getattr(config, "SPACE_BAR_HEIGHT", 90),
                                pinned=(target_key == pinned_key))
        bubble_layout, bubble_key = new_layout, new_layout_key

        # Header clock hover -> chrony (time sync) status bubble.
        if not cursor_hidden and clock_rect.collidepoint(mouse_pos):
            overlays.draw_chrony_bubble(screen, clock_rect, chrony_status.get(),
                                        font_big, font_small, screen_width, bar_h,
                                        gps=gps_status.get())

        sat_ui.draw(screen, font_big, font_small)
        settings_ui.draw(screen)

        # Only refresh the motion cache while interacting; idle frames skip the
        # full-screen copy. The freshness gate above forces a real render on the
        # first motion frame after idle, so the cursor never shows a stale scene.
        if interacting:
            frame_cache = screen.copy()
            frame_cache_t = time.time()
        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
