# -*- coding: utf-8 -*-
"""
piMap.py - QSO great-circle map with QRZ logbook and space weather.

Controls:
  - Mouse wheel: zoom around cursor (unless mouse is over Sat Panel)
  - Left click + drag: pan
  - Right click: reset view (zoom=1, centered on home QTH)
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

Controls: wheel = zoom, left-drag = pan, right-click = reset, ESC = exit.
"""

import sys
import os
import time
import math
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
from maidenhead import maidenhead_to_latlon
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
import sat_tracker
import sat_radio
import geo_boundaries
import map_tiles
import applog
import chrony_status
import gps_status
import n3fjp_status
import overlays
from overlays import (
    ISS_NORAD, QSO_DOT_RADIUS,
    draw_gc_line, draw_sat_track, draw_sat_marker, draw_sat_bubble,
    _marker_hit, draw_status_row, _sat_color, _fmt_pass,
    iss_simulated_now, _apply_map_brightness,
)

_log = applog.get_logger("piMap")


# ----------------------------- UX TUNING -----------------------------

MIN_ZOOM = float(getattr(config, "MIN_ZOOM", 1.0))
MAX_ZOOM = float(getattr(config, "MAX_ZOOM", 8.0))
ZOOM_STEP = float(getattr(config, "ZOOM_STEP", 1.15))

DRAG_THRESHOLD_PX = 6
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

    left_down = False
    dragging = False
    down_pos = (0, 0)
    down_cam = (cam_cx, cam_cy)

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
        interacting = (dragging or left_down or panel_animating
                       or (now_t - last_motion) < 0.4
                       or (now_t - last_interaction) < 0.4)
        dt = clock.tick(60 if interacting else idle_fps) / 1000.0
        mouse_pos = pygame.mouse.get_pos()
        sat_ui.update(dt, mouse_pos)

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

                if event.button == 1 and not sat_ui.wheel_is_for_panel((mx, my)):
                    hit = _marker_hit(hover_targets, mx, my)
                    if hit is not None:
                        # Click a sat/QSO dot: toggle its pinned info bubble.
                        pinned_key = None if pinned_key == hit else hit
                        continue
                    # Click empty map: dismiss any pin, then begin pan.
                    pinned_key = None
                    if inside_map_rect(mx, my):
                        left_down = True
                        dragging = False
                        down_pos = (mx, my)
                        down_cam = (cam_cx, cam_cy)

            elif event.type == pygame.MOUSEMOTION:
                last_motion = time.time()
                if not left_down:
                    continue

                mx, my = event.pos
                dx = mx - down_pos[0]
                dy = my - down_pos[1]

                if (not dragging) and (abs(dx) >= DRAG_THRESHOLD_PX or abs(dy) >= DRAG_THRESHOLD_PX):
                    dragging = True

                if dragging:
                    effective_scale = base_scale * zoom
                    cam_cx = down_cam[0] - (dx / float(effective_scale))
                    cam_cy = down_cam[1] - (dy / float(effective_scale))
                    clamp_camera()
                    last_interaction = time.time()

            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1 and left_down:
                    left_down = False
                    dragging = False

                elif event.button == 3:
                    reset_view()

        # ----------------------------- DRAW -----------------------------

        # While the pointer is merely moving (not dragging), the world is
        # identical frame-to-frame — re-blit the cached frame and just let the
        # cursor move, so it stays smooth instead of waiting on a full re-render.
        # Skip the shortcut on click/scroll/key frames, and while the flyout menu
        # is actually slid out (so it animates/responds) — not merely when the
        # cursor is in the region where it would eventually appear.
        moving = (time.time() - last_motion) <= LINES_HQ_IDLE_S
        panel_visible = sat_ui.panel.x < sat_ui.panel.hidden_x - 1
        fresh_cache = frame_cache is not None and (now_t - frame_cache_t) < 0.5
        if (moving and not dragging and not force_render and not panel_visible
                and fresh_cache):
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

        # --- Info bubble: pinned target, else hovered dot ---
        # When the cursor is hidden (idle), clear all hover bubbles — otherwise
        # a bubble parked under the now-invisible cursor would stay stuck open.
        enabled_norads = {s.norad_id for s in enabled_sats}
        if pinned_key and pinned_key[0] == "sat" and pinned_key[1] not in enabled_norads:
            pinned_key = None  # an unchecked sat drops its pin
        target_key = None if cursor_hidden else (
            pinned_key if pinned_key in hover_targets
            else _marker_hit(hover_targets, mouse_pos[0], mouse_pos[1]))
        if target_key is not None and target_key in hover_targets:
            bpos, kind, payload = hover_targets[target_key]
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

        # Header clock hover -> chrony (time sync) status bubble.
        if not cursor_hidden and clock_rect.collidepoint(mouse_pos):
            overlays.draw_chrony_bubble(screen, clock_rect, chrony_status.get(),
                                        font_big, font_small, screen_width, bar_h,
                                        gps=gps_status.get())

        sat_ui.draw(screen, font_big, font_small)

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
