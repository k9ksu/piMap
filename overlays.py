"""Map overlay rendering: QSO/satellite track lines, satellite markers, the
ISS icon (halo + drop shadow), info bubbles, the source-status row, and the
map-brightness filter. These are stateless draw helpers (plus a couple of
module-level caches for the ISS icon and the sim clock)."""

import math
import os
import time
import colorsys

import pygame
import pygame.gfxdraw

import config
from geo import lonlat_to_xy, split_path_at_dateline, great_circle_points, clamp
from header_ui import parse_hex_color
import sat_tracker
import sat_radio
import applog

ISS_NORAD = 25544
SAT_DOT_RADIUS = int(getattr(config, "SAT_DOT_RADIUS", 6))
QSO_DOT_RADIUS = int(getattr(config, "QSO_DOT_RADIUS", 4))
ISS_HALO_COLOR = parse_hex_color(getattr(config, "ISS_ICON_HALO_COLOR", "#FFFFFF"))

_iss_icon_cache = None
_iss_icon_load_failed = False
_iss_sim_t0 = None


def iss_simulated_now():
    global _iss_sim_t0
    real_now = time.time()
    if _iss_sim_t0 is None:
        _iss_sim_t0 = real_now
    return _iss_sim_t0 + (real_now - _iss_sim_t0) * config.ISS_TIME_SPEED + config.ISS_TIME_OFFSET_S


def _polyline_strip_polygon(pts, width):
    """Build a closed polygon that outlines a polyline with the given thickness."""
    n = len(pts)
    if n < 2:
        return []
    r = width / 2.0
    left = []
    right = []
    for i in range(n):
        if i == 0:
            tdx = pts[1][0] - pts[0][0]
            tdy = pts[1][1] - pts[0][1]
        elif i == n - 1:
            tdx = pts[-1][0] - pts[-2][0]
            tdy = pts[-1][1] - pts[-2][1]
        else:
            tdx = pts[i + 1][0] - pts[i - 1][0]
            tdy = pts[i + 1][1] - pts[i - 1][1]
        length = math.hypot(tdx, tdy)
        if length == 0:
            continue
        nx = -tdy / length * r
        ny = tdx / length * r
        left.append((int(pts[i][0] + nx), int(pts[i][1] + ny)))
        right.append((int(pts[i][0] - nx), int(pts[i][1] - ny)))
    return left + list(reversed(right))


def draw_gc_line(surface, color, lat1, lon1, lat2, lon2,
                 map_width, map_height, map_to_screen_fn, width=2):
    gc_points = great_circle_points(lat1, lon1, lat2, lon2, n=800)
    segments = split_path_at_dateline(gc_points)

    for seg in segments:
        pts_screen = []
        for plat, plon in seg:
            mx, my = lonlat_to_xy(plon, plat, map_width, map_height)
            sx, sy = map_to_screen_fn(mx, my)
            pts_screen.append((sx, sy))

        if len(pts_screen) < 2:
            continue
        if width <= 1:
            pygame.draw.aalines(surface, color, False, pts_screen)
        else:
            polygon = _polyline_strip_polygon(pts_screen, width)
            if len(polygon) >= 3:
                pygame.gfxdraw.filled_polygon(surface, polygon, color)
                pygame.gfxdraw.aapolygon(surface, polygon, color)


def _scale_alpha(surf, opacity):
    if opacity < 1.0:
        a = max(0, min(255, int(255 * opacity)))
        surf.fill((255, 255, 255, a), special_flags=pygame.BLEND_RGBA_MULT)


def _decorate_icon(icon, halo_color, halo_t, halo_alpha,
                   shadow_x, shadow_y, shadow_alpha, shadow_gray=0.0):
    """Composite a drop shadow + silhouette-tracing halo behind the icon so it
    reads over any map background. Returns a new, larger surface (icon centered)."""
    iw, ih = icon.get_size()
    pad = max(int(halo_t) + 1, abs(int(shadow_x)) + 1, abs(int(shadow_y)) + 1, 1)
    W, H = iw + 2 * pad, ih + 2 * pad
    out = pygame.Surface((W, H), pygame.SRCALPHA)
    mask = pygame.mask.from_surface(icon)

    if (shadow_x or shadow_y) and shadow_alpha > 0:
        g = max(0, min(255, int(255 * shadow_gray)))
        sh = mask.to_surface(setcolor=(g, g, g, 255), unsetcolor=(0, 0, 0, 0))
        layer = pygame.Surface((W, H), pygame.SRCALPHA)
        layer.blit(sh, (pad + shadow_x, pad + shadow_y))
        _scale_alpha(layer, shadow_alpha)
        out.blit(layer, (0, 0))

    if halo_t > 0 and halo_alpha > 0:
        sil = mask.to_surface(setcolor=(*halo_color, 255), unsetcolor=(0, 0, 0, 0))
        layer = pygame.Surface((W, H), pygame.SRCALPHA)
        for r in range(1, int(halo_t) + 1):
            for ang in range(0, 360, 30):
                ox = pad + round(r * math.cos(math.radians(ang)))
                oy = pad + round(r * math.sin(math.radians(ang)))
                layer.blit(sil, (ox, oy))
        _scale_alpha(layer, halo_alpha)
        out.blit(layer, (0, 0))

    out.blit(icon, (pad, pad))
    return out


def _get_iss_icon():
    global _iss_icon_cache, _iss_icon_load_failed
    if _iss_icon_cache is not None or _iss_icon_load_failed:
        return _iss_icon_cache
    try:
        raw = pygame.image.load(os.path.join(os.path.dirname(__file__), config.ISS_ICON_FILE)).convert_alpha()
    except Exception as e:
        print(f"Error loading ISS icon {config.ISS_ICON_FILE}: {e}")
        _iss_icon_load_failed = True
        return None
    halo_px = getattr(config, "ISS_ICON_HALO_PX", 0)
    # Supersample so a fractional (sub-pixel) halo is possible, then scale down.
    ss = 2 if (halo_px > 0 and halo_px != int(halo_px)) else 1

    w, h = raw.get_size()
    scale = (config.ISS_ICON_WIDTH_PX * ss) / w
    icon = pygame.transform.smoothscale(raw, (int(w * scale), int(h * scale)))

    thickness = max(1, round(halo_px * ss)) if halo_px > 0 else 0
    shadow_x = round(getattr(config, "ISS_ICON_SHADOW_X", 0) * ss)
    shadow_y = round(getattr(config, "ISS_ICON_SHADOW_Y", 0) * ss)
    decorated = _decorate_icon(
        icon, ISS_HALO_COLOR, thickness,
        getattr(config, "ISS_ICON_HALO_ALPHA", 1.0),
        shadow_x, shadow_y, getattr(config, "ISS_ICON_SHADOW_ALPHA", 0.0),
        getattr(config, "ISS_ICON_SHADOW_GRAY", 0.0))
    if ss != 1:
        decorated = pygame.transform.smoothscale(
            decorated, (decorated.get_width() // ss, decorated.get_height() // ss))
    _iss_icon_cache = decorated
    return _iss_icon_cache


def _sat_color(i):
    """Distinct track color per catalog index via golden-ratio hue spread."""
    h = (i * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.65, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def _fmt_pass(pass_unix, now):
    if pass_unix is None:
        return "--"
    secs = pass_unix - now
    if secs <= 0:
        return "now"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    return f"{int(secs // 3600)}h{int((secs % 3600) // 60):02d}m"


def draw_sat_track(surface, map_rect, map_width, map_height, map_to_screen_fn,
                   norad, color, now_unix, width=3):
    try:
        pts = sat_tracker.groundtrack(norad, now_unix)
    except Exception as e:
        print(f"Sat {norad} track unavailable: {e}")
        return
    if not pts:
        return
    segments = split_path_at_dateline(pts)

    old_clip = surface.get_clip()
    surface.set_clip(map_rect)
    for seg in segments:
        pts_screen = []
        for lat, lon in seg:
            mx, my = lonlat_to_xy(lon, lat, map_width, map_height)
            sx, sy = map_to_screen_fn(mx, my)
            pts_screen.append((sx, sy))
        if len(pts_screen) > 1:
            polygon = _polyline_strip_polygon(pts_screen, width)
            if len(polygon) >= 3:
                pygame.gfxdraw.filled_polygon(surface, polygon, color)
                pygame.gfxdraw.aapolygon(surface, polygon, color)
    surface.set_clip(old_clip)


def draw_sat_marker(surface, map_rect, map_width, map_height, map_to_screen_fn,
                    sat, now_unix):
    """Draw the sat's marker(s). Returns the primary on-screen (x, y) of the
    un-wrapped dot if it's within the map, else None (for hover/click hit-test)."""
    sp = sat_tracker.subpoint(sat.norad_id, now_unix)
    if sp is None:
        return None
    cur_lat, cur_lon = sp
    mx, my = lonlat_to_xy(cur_lon, cur_lat, map_width, map_height)

    # ISS keeps its image icon; everything else is a filled dot in its color.
    icon = _get_iss_icon() if sat.norad_id == ISS_NORAD else None

    primary = None
    old_clip = surface.get_clip()
    surface.set_clip(map_rect)
    for dx in (-map_width, 0, map_width):
        sx, sy = map_to_screen_fn(mx + dx, my)
        cx, cy = int(sx), int(sy)
        if icon is not None:
            rect = icon.get_rect(center=(cx, cy))
            if rect.colliderect(map_rect):
                surface.blit(icon, rect)
        elif map_rect.collidepoint(cx, cy):
            pygame.gfxdraw.filled_circle(surface, cx, cy, SAT_DOT_RADIUS, sat.color)
            pygame.gfxdraw.aacircle(surface, cx, cy, SAT_DOT_RADIUS, (0, 0, 0))
            pygame.gfxdraw.aacircle(surface, cx, cy, SAT_DOT_RADIUS, sat.color)
        if dx == 0 and map_rect.collidepoint(cx, cy):
            primary = (cx, cy)
    surface.set_clip(old_clip)
    return primary


def draw_sat_bubble(surface, pos, name, lines, note, color, font_title, font_body,
                    screen_w, top_margin, pinned):
    px, py = pos
    pad = 8
    gap = 3
    title_s = font_title.render(name, True, (255, 255, 255))
    body = lines if lines else [note]
    body_s = [font_body.render(t, True, (215, 225, 235)) for t in body]

    w = max([title_s.get_width()] + [s.get_width() for s in body_s]) + pad * 2
    h = title_s.get_height() + gap + sum(s.get_height() + gap for s in body_s) + pad * 2

    bx = px + 14
    by = py - h - 12
    if bx + w > screen_w - 4:
        bx = px - w - 14
    if by < top_margin + 4:
        by = py + 14
    bx = max(4, bx)

    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((10, 10, 14, 238))
    surface.blit(bg, (bx, by))
    border = color if color != (0, 0, 0) else (210, 210, 210)
    rect = pygame.Rect(bx, by, w, h)
    pygame.draw.rect(surface, border, rect, 2 if not pinned else 3, border_radius=6)

    y = by + pad
    surface.blit(title_s, (bx + pad, y))
    y += title_s.get_height() + gap
    for s in body_s:
        surface.blit(s, (bx + pad, y))
        y += s.get_height() + gap


def _marker_hit(targets, mx, my, radius=16):
    """targets: key -> (pos, kind, payload). Returns nearest key within radius."""
    best, best_d2 = None, radius * radius
    for key, entry in targets.items():
        pos = entry[0]
        d2 = (pos[0] - mx) ** 2 + (pos[1] - my) ** 2
        if d2 <= best_d2:
            best_d2 = d2
            best = key
    return best


_STATUS_COLORS = {
    "ok": (0, 210, 0),
    "loading": (255, 176, 0),
    "stale": (255, 140, 0),
    "failed": (230, 60, 60),
}
_STATUS_LABELS = [("tle", "TLE"), ("swx", "SWX"), ("qrz", "QRZ"), ("freq", "FRQ")]


def draw_status_row(surface, font, right_x, top_y):
    """Right-aligned row of colored source-status tags (dot + label)."""
    statuses = applog.get_status()
    dot_r = 5
    gap = 14
    inner = 5

    items = []
    total = 0
    for key, label in _STATUS_LABELS:
        state, _detail = statuses.get(key, ("loading", ""))
        color = _STATUS_COLORS.get(state, (120, 120, 120))
        text = font.render(label, True, color)
        w = dot_r * 2 + inner + text.get_width()
        items.append((color, text, w))
        total += w
    total += gap * (len(items) - 1) if items else 0

    x = right_x - total
    for color, text, w in items:
        cy = top_y + text.get_height() // 2
        pygame.draw.circle(surface, color, (x + dot_r, cy), dot_r)
        surface.blit(text, (x + dot_r * 2 + inner, top_y))
        x += w + gap


def _chrony_fmt_sec(s, signed=True):
    """Seconds -> compact magnitude string with sign (ns/us/ms/s)."""
    if s is None:
        return "--"
    sign = "-" if s < 0 else ("+" if signed else "")
    a = abs(s)
    if a < 1e-6:
        v, u = a * 1e9, "ns"
    elif a < 1e-3:
        v, u = a * 1e6, "us"
    elif a < 1.0:
        v, u = a * 1e3, "ms"
    else:
        v, u = a, "s"
    prec = 3 if v < 10 else (2 if v < 100 else 1)
    return f"{sign}{v:.{prec}f} {u}"


def _chrony_fmt_ppm(v, signed=True):
    if v is None:
        return "--"
    sign = "-" if v < 0 else ("+" if signed else "")
    return f"{sign}{abs(v):.3f} ppm"


def _chrony_accuracy_phrase(t):
    """Plain-English one-liner: how tight the clock is held, +/- a magnitude.

    Uses RMS offset as the practical accuracy estimate (the averaged error,
    not a lucky instant). Returns e.g. 'Clock accurate to +/- 0.4 millionths
    of a second'."""
    rms = t.get("rms_offset")
    if rms is None:
        return None
    a = abs(rms)
    if a < 1e-6:
        return f"Clock accurate to +/- {a * 1e9:.0f} billionths of a second"
    if a < 1e-3:
        return f"Clock accurate to +/- {a * 1e6:.1f} millionths of a second"
    if a < 1.0:
        return f"Clock accurate to +/- {a * 1e3:.1f} thousandths of a second"
    return f"Clock accurate to +/- {a:.2f} seconds"


# chrony source state glyph -> color (matches chronyc's *+-?x~ column)
_CHRONY_STATE_COLOR = {
    "*": (0, 210, 0),       # current synced source (best)
    "+": (120, 200, 120),   # combined
    "-": (150, 150, 160),   # not combined
    "?": (230, 60, 60),     # unreachable / no data
    "x": (230, 60, 60),     # falseticker
    "~": (255, 176, 0),     # too variable
}


def _chrony_source_color(state, reach):
    """Health color for a source, reachability-aware.

    chrony marks a source '?' both when it has no data AND when it is simply
    not a selection candidate (e.g. a 'noselect' reference clock like NMEA that
    only labels the second for a PPS source). A '?' source that is fully
    reachable is doing its job, so show it neutral rather than alarming red.
    """
    if reach in (None, "", "0"):
        return (230, 60, 60)            # genuinely no data -> red
    if state in ("*", "+"):
        return (0, 210, 0) if state == "*" else (120, 200, 120)
    if state == "x":
        return (230, 60, 60)            # falseticker -> red
    if state == "~":
        return (255, 176, 0)            # too variable -> amber
    if state == "?":
        # Reachable but not selectable (noselect / coarse reference): neutral.
        return (150, 150, 160) if reach == "377" else (255, 176, 0)
    return _CHRONY_STATE_COLOR.get(state, (150, 150, 160))


def draw_chrony_bubble(surface, anchor_rect, data, font_title, font_body,
                       screen_w, top_margin, gps=None):
    """Hover bubble for the header clock: chrony tracking + reference clocks,
    plus GNSS satellite stats, or a plain 'unavailable' message."""
    pad = 9
    gap = 3
    col_gap = 18
    accent = (120, 200, 255)
    label_col = (170, 178, 190)
    val_col = (225, 232, 240)

    avail = bool(data and data.get("available"))
    if avail:
        t = data.get("tracking", {})
        title = f"chrony  {t.get('ref_name', '?')}  (stratum {t.get('stratum', '?')})"
    else:
        title = "Time sync"

    title_s = font_title.render(title, True, accent)

    # Build (label, value, value_color) rows.
    # A ("__full__", text, color) row is a single full-width line (plain-English
    # summary) that does not participate in the label/value column layout.
    rows = []
    if avail:
        t = data["tracking"]
        phrase = _chrony_accuracy_phrase(t)
        if phrase:
            rows.append(("__full__", phrase, accent))
            rows.append((None, None, None))  # spacer under the summary
        rows += [
            ("Last offset", _chrony_fmt_sec(t.get("last_offset")), val_col),
            ("RMS offset", _chrony_fmt_sec(t.get("rms_offset"), False), val_col),
            ("Frequency", _chrony_fmt_ppm(t.get("frequency")), val_col),
            ("Skew", _chrony_fmt_ppm(t.get("skew"), False), val_col),
            ("Root delay", _chrony_fmt_sec(t.get("root_delay"), False), val_col),
            ("Root disp.", _chrony_fmt_sec(t.get("root_dispersion"), False), val_col),
            ("Leap", str(t.get("leap", "--")), val_col),
        ]
        # Reference clocks (mode '#') get their own labeled rows below.
        refs = [s for s in data.get("sources", []) if s.get("mode") == "#"]
        if refs:
            rows.append((None, None, None))  # spacer
            for s in refs:
                reach = s.get("reach")
                state = s.get("state")
                col = _chrony_source_color(state, reach)
                name = s.get("name", "?")
                if state == "*":
                    name += "  (synced)"
                elif state == "?" and reach == "377":
                    name += "  (ref)"   # reachable but not selected (e.g. noselect)
                if reach not in (None, "377"):
                    name += f"  r{reach}"
                val = _chrony_fmt_sec(s.get("offset"))
                rows.append((name, val, col))
    else:
        reason = (data or {}).get("reason", "chrony status unavailable")
        rows = [("", reason, (255, 176, 0))]

    # --- GNSS satellite stats (from the u-blox module via gps_status) ---
    if gps and gps.get("available"):
        g = gps["gnss"]
        rows.append((None, None, None))  # spacer
        fix = g.get("fix", "?")
        fix_col = (0, 210, 0) if fix == "3D" else (255, 176, 0)
        rows.append(("GNSS fix", fix, fix_col))
        rows.append(("Sats used", f"{g['used']} / {g['tracked']} tracked", val_col))
        # per-constellation: "GPS 8  GLO 6  GAL 3" (used counts)
        per = g.get("per", {})
        order = ["GPS", "GLO", "GAL", "BDS", "QZSS", "SBAS"]
        parts = [f"{k} {per[k]['used']}" for k in order
                 if k in per and per[k]["used"]]
        if parts:
            rows.append(("By system", "  ".join(parts), val_col))
        snr = g.get("best_cno", 0)
        snr_col = (0, 210, 0) if snr >= 35 else (255, 176, 0) if snr >= 25 else (230, 60, 60)
        rows.append(("Signal", f"{g.get('avg_cno', 0)} avg / {snr} best dBHz", snr_col))
        if "hdop" in g:
            hd = g["hdop"]
            hd_col = (0, 210, 0) if hd <= 2 else (255, 176, 0) if hd <= 5 else (230, 60, 60)
            rows.append(("HDOP", f"{hd:.2f}", hd_col))
    elif gps and not gps.get("available"):
        rows.append((None, None, None))
        rows.append(("GNSS", gps.get("reason", "unavailable"), (255, 176, 0)))

    # Render label/value surfaces; measure column widths.
    rendered = []
    lab_w = 0
    val_w = 0
    full_w = 0
    for lab, val, col in rows:
        if lab is None and val is None:
            rendered.append(None)  # spacer
            continue
        if lab == "__full__":
            fs = font_body.render(val, True, col)
            full_w = max(full_w, fs.get_width())
            rendered.append(("full", fs))
            continue
        ls = font_body.render(lab, True, label_col) if lab else None
        vs = font_body.render(val, True, col) if val is not None else None
        if ls:
            lab_w = max(lab_w, ls.get_width())
        if vs:
            val_w = max(val_w, vs.get_width())
        rendered.append((ls, vs))

    body_w = lab_w + (col_gap if lab_w and val_w else 0) + val_w
    w = max(title_s.get_width(), body_w, full_w) + pad * 2

    line_h = font_body.get_height() + gap
    spacer_h = gap + 2
    h = pad + title_s.get_height() + gap
    for r in rendered:
        h += spacer_h if r is None else line_h
    h += pad

    # Position below the clock, centered, clamped to the screen.
    bx = anchor_rect.centerx - w // 2
    by = anchor_rect.bottom + 8
    bx = max(4, min(bx, screen_w - w - 4))
    if by < top_margin + 2:
        by = top_margin + 2

    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((10, 10, 14, 240))
    surface.blit(bg, (bx, by))
    pygame.draw.rect(surface, accent, pygame.Rect(bx, by, w, h), 2, border_radius=6)

    y = by + pad
    surface.blit(title_s, (bx + pad, y))
    y += title_s.get_height() + gap
    val_x = bx + pad + lab_w + (col_gap if lab_w and val_w else 0)
    for r in rendered:
        if r is None:
            y += spacer_h
            continue
        if r[0] == "full":
            surface.blit(r[1], (bx + pad, y))
            y += line_h
            continue
        ls, vs = r
        if ls:
            surface.blit(ls, (bx + pad, y))
        if vs:
            # right-align value within the value column
            surface.blit(vs, (val_x + (val_w - vs.get_width()), y))
        y += line_h


def _apply_map_brightness(surface, brightness):
    if abs(brightness - 1.0) < 1e-6:
        return surface

    s = surface.copy()
    if brightness < 1.0:
        m = int(clamp(255.0 * brightness, 0, 255))
        s.fill((m, m, m), special_flags=pygame.BLEND_RGB_MULT)
    else:
        add = int(clamp(255.0 * (brightness - 1.0) * 0.8, 0, 120))
        if add > 0:
            s.fill((add, add, add), special_flags=pygame.BLEND_RGB_ADD)
    return s
