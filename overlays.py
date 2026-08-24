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


_TARGET_STATE_COLORS = {
    "green": (60, 200, 90),
    "yellow": (235, 190, 40),
    "red": (220, 70, 60),
    "quiet": (210, 210, 214),  # nothing heard for a while — subtle, not alarming
}
_TARGET_GRAY = (150, 150, 156)


def target_summary_color(state):
    return _TARGET_STATE_COLORS.get(state, _TARGET_GRAY)


_BAND_NAMES = {
    -1: "2200m", 0: "630m", 1: "160m", 3: "80m", 5: "60m", 7: "40m",
    10: "30m", 14: "20m", 18: "17m", 21: "15m", 24: "12m", 28: "10m",
    50: "6m", 70: "4m", 144: "2m", 222: "1.25m", 430: "70cm",
    902: "33cm", 1296: "23cm",
}


def _band_label(band):
    """Fallback label for a band the WSPR side never reported (FT8 can see a
    band WSPR_BANDS does not list, e.g. 2m)."""
    return _BAND_NAMES.get(band, "%sMHz" % band)


def _anchor_bubble(px, py, w, h, screen_w, top_margin):
    """Place a bubble beside a marker, flipping left/down to stay on screen."""
    bx = px + 14
    by = py - h - 12
    if bx + w > screen_w - 4:
        bx = px - w - 14
    if by < top_margin + 4:
        by = py + 14
    return max(4, bx), by


def draw_target_detail_bubble(surface, pos, target, detail, font_title, font_body,
                              screen_w, top_margin, pinned, mouse_pos=None,
                              max_rows=6):
    """Drill-down view for one (target, band) cell: which stations are actually
    being heard on this path, one row per callsign, strongest first.

    Two sections, because WSPR is one-directional per spot:
      WE HEAR THEM  - a station in the target's field, spotted by a receiver
                      in our grid field
      THEY HEAR US  - a station in our field, spotted over there
    Returns {"rect":..., "band_rows": [], "back": Rect} -- same contract as
    draw_target_bubble so the caller can hit-test either view the same way."""
    px, py = pos
    pad, gap = 8, 4
    body_h = font_body.get_height()

    title = target.get("label") or target.get("field") or "target"
    title_s = font_title.render("WSPR -> %s" % title, True, (255, 255, 255))
    back_txt = "< back to bands"
    back_s = font_body.render(back_txt, True, (150, 200, 245))
    src = detail.get("source", "WSPR")
    band_s = font_body.render(
        "%s %s | home %s | %d min" % (src, detail.get("band_label", "?"),
                                      detail.get("home_scope", "?"),
                                      detail.get("window_min", 30)),
        True, (150, 225, 165) if src == "FT8" else (150, 195, 235))

    lines = []   # (surface, indent) -- built first so we can size the bubble
    def add(text, color, indent=0):
        lines.append((font_body.render(text, True, color), indent))

    state = detail.get("state")
    if state == "loading":
        add("loading spots...", (150, 160, 175))
    elif state == "error":
        add("fetch failed:", (235, 120, 110))
        add((detail.get("error") or "")[:46], (170, 130, 128))
    else:
        for key, heading, tint in (("hearing", "WE HEAR THEM", (150, 225, 165)),
                                   ("heard_by", "THEY HEAR US", (150, 195, 235))):
            group = detail.get(key) or []
            n_spots = detail.get("n_in" if key == "hearing" else "n_out", 0)
            if not group:
                add("%s  --  nothing" % heading, (120, 122, 128))
                continue
            add("%s  (%d sta / %d spots)" % (heading, len(group), n_spots), tint)
            for g in group[:max_rows]:
                if "km" in g:          # WSPR rows know the path length
                    txt = ("%-9s %-6s %+4d dB  x%-3d %2.0fm  %5d km"
                           % (g["sign"][:9], (g["loc"] or "")[:6], g["best_snr"],
                              g["count"], g["age_min"], g["km"]))
                else:                  # FT8 rows name the local end instead
                    txt = ("%-9s %-6s %+4d dB  x%-3d %3.1fm  %s"
                           % (g["sign"][:9], (g["loc"] or "")[:6], g["best_snr"],
                              g["count"], g["age_min"], (g.get("by") or "?")[:9]))
                add(txt, (225, 230, 238), indent=8)
            if len(group) > max_rows:
                add("+%d more" % (len(group) - max_rows), (140, 145, 155), indent=8)
    if detail.get("truncated"):
        add("(spot cap reached - partial view)", (200, 170, 90))

    head_w = back_s.get_width() + 18 + band_s.get_width()
    w = max([title_s.get_width(), head_w] +
            [ls.get_width() + ind for ls, ind in lines]) + pad * 2
    h = (title_s.get_height() + gap + body_h + gap + 1 + gap
         + sum(ls.get_height() + gap for ls, _ in lines) + pad * 2)

    bx, by = _anchor_bubble(px, py, w, h, screen_w, top_margin)
    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((10, 10, 14, 242))
    surface.blit(bg, (bx, by))
    pygame.draw.rect(surface,
                     (110, 200, 130) if src == "FT8" else (110, 165, 215),
                     pygame.Rect(bx, by, w, h), 3 if pinned else 2, border_radius=6)

    y = by + pad
    surface.blit(title_s, (bx + pad, y))
    y += title_s.get_height() + gap

    back_rect = pygame.Rect(bx + pad, y, back_s.get_width() + 8, body_h)
    if back_rect.collidepoint(mouse_pos or (-1, -1)):
        pygame.draw.rect(surface, (34, 44, 58), back_rect.inflate(6, 2),
                         border_radius=4)
    surface.blit(back_s, (bx + pad, y))
    surface.blit(band_s, (bx + w - pad - band_s.get_width(), y))
    y += body_h + gap
    pygame.draw.line(surface, (60, 70, 84), (bx + pad, y), (bx + w - pad, y), 1)
    y += gap

    for ls, ind in lines:
        surface.blit(ls, (bx + pad + ind, y))
        y += ls.get_height() + gap

    return {"rect": pygame.Rect(bx, by, w, h), "band_rows": [], "back": back_rect}


def draw_target_bubble(surface, pos, target, status, ft8_status, font_title,
                       font_body, screen_w, top_margin, pinned, metros=None,
                       mouse_pos=None):
    """Hover/pinned bubble for a WSPR target dot: title, the largest metros in
    its grid field, then a per-band table carrying BOTH sources side by side.

    The two columns answer different questions and are deliberately not merged:
      FT8   is the path open AND is anybody on it (PSKReporter, live)
      WSPR  is the path physically open at all (beacons, 24/7)
    A band can easily be green under one and blank under the other -- FT8 goes
    quiet at 3am on a wide-open band, WSPR shows nothing on a band with no
    beacons -- and which of those you are looking at matters.

    Returns {"rect":..., "band_rows": [(band, source, Rect), ...], "back": None}
    with one entry per CELL, so a click can drill into that source's spots."""
    px, py = pos
    pad, gap, dot_r, inner = 8, 4, 5, 8
    ft8_status = ft8_status or {}

    title = target.get("label") or target.get("field") or "target"
    title_s = font_title.render("%s  [%s]" % (title, target.get("field", "??")),
                                True, (255, 255, 255))

    if metros is None:
        metro_surfs = [font_body.render("cities loading...", True, (140, 142, 148))]
    elif metros:
        metro_surfs = [font_body.render("* %s" % m, True, (170, 195, 220)) for m in metros]
    else:
        metro_surfs = [font_body.render("no major metros", True, (140, 142, 148))]

    wspr_by_band = {b.get("band"): b for b in status.get("bands", [])}
    all_bands = sorted(set(wspr_by_band) | set(ft8_status),
                       key=lambda b: (b is None, b))

    def ft8_cell(band):
        v = ft8_status.get(band)
        if not v:
            return "none", "--"
        return v["state"], "%d sta %+d" % (v["stations"], v["best_snr"])

    def wspr_cell(band):
        b = wspr_by_band.get(band)
        if not b or b.get("cnt", 0) <= 0:
            return "none", "--"
        snr = b.get("best_snr")
        age = b.get("age_min")
        return (b["state"],
                "%d sp %s%s" % (b["cnt"],
                                ("%+d" % snr) if isinstance(snr, int) else "",
                                (" %.0fm" % age) if age is not None else ""))

    rows = []
    for band in all_bands:
        lbl = wspr_by_band.get(band, {}).get("label") or _band_label(band)
        f_state, f_txt = ft8_cell(band)
        w_state, w_txt = wspr_cell(band)
        rows.append((band,
                     font_body.render(lbl, True, (225, 230, 238)),
                     f_state, font_body.render(f_txt, True, (185, 195, 205)),
                     w_state, font_body.render(w_txt, True, (185, 195, 205))))

    hd_band = font_body.render("band", True, (130, 135, 145))
    hd_ft8 = font_body.render("FT8", True, (150, 225, 165))
    hd_wspr = font_body.render("WSPR", True, (150, 195, 235))
    hint_s = font_body.render("click a cell for stations | middle-click removes",
                              True, (120, 122, 128))

    band_w = max([r[1].get_width() for r in rows] + [hd_band.get_width()])
    ft8_w = max([r[3].get_width() for r in rows] + [hd_ft8.get_width()])
    wspr_w = max([r[5].get_width() for r in rows] + [hd_wspr.get_width()])
    cell_ft8_w = dot_r * 2 + inner + ft8_w
    cell_wspr_w = dot_r * 2 + inner + wspr_w
    row_h = max([r[1].get_height() for r in rows] + [font_body.get_height()])
    table_w = band_w + inner * 2 + cell_ft8_w + inner * 2 + cell_wspr_w

    metro_w = max([s.get_width() for s in metro_surfs], default=0)
    metro_h = sum(s.get_height() + gap for s in metro_surfs)
    w = max(title_s.get_width(), table_w, hint_s.get_width(), metro_w) + pad * 2
    h = (title_s.get_height() + gap + metro_h
         + row_h + gap + 1 + gap                       # column header + rule
         + len(rows) * (row_h + gap)
         + gap + hint_s.get_height() + pad * 2)

    bx, by = _anchor_bubble(px, py, w, h, screen_w, top_margin)
    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((10, 10, 14, 238))
    surface.blit(bg, (bx, by))
    border = target_summary_color(status.get("summary", "gray"))
    pygame.draw.rect(surface, border, pygame.Rect(bx, by, w, h),
                     3 if pinned else 2, border_radius=6)

    x_band = bx + pad
    x_ft8 = x_band + band_w + inner * 2
    x_wspr = x_ft8 + cell_ft8_w + inner * 2

    y = by + pad
    surface.blit(title_s, (x_band, y))
    y += title_s.get_height() + gap
    for ms in metro_surfs:
        surface.blit(ms, (x_band, y))
        y += ms.get_height() + gap

    surface.blit(hd_band, (x_band, y))
    surface.blit(hd_ft8, (x_ft8 + dot_r * 2 + inner, y))
    surface.blit(hd_wspr, (x_wspr + dot_r * 2 + inner, y))
    y += row_h + gap
    pygame.draw.line(surface, (58, 64, 76), (bx + pad, y), (bx + w - pad, y), 1)
    y += gap

    band_rows = []
    for band, band_s, f_state, f_s, w_state, w_s in rows:
        cy = y + row_h // 2
        surface.blit(band_s, (x_band, y))
        for xcol, cw, state, txt_s, src in (
                (x_ft8, cell_ft8_w, f_state, f_s, "ft8"),
                (x_wspr, cell_wspr_w, w_state, w_s, "wspr")):
            cell = pygame.Rect(xcol - 4, y, cw + 8, row_h)
            if band is not None and cell.collidepoint(mouse_pos or (-1, -1)):
                pygame.draw.rect(surface, (34, 40, 50), cell.inflate(4, 2),
                                 border_radius=4)
            pygame.draw.circle(surface, target_summary_color(
                "red" if state in ("weak", "none") else state),
                (xcol + dot_r, cy), dot_r)
            surface.blit(txt_s, (xcol + dot_r * 2 + inner, y))
            if band is not None:
                band_rows.append((band, src, cell))
        y += row_h + gap
    surface.blit(hint_s, (bx + pad, y + gap))
    return {"rect": pygame.Rect(bx, by, w, h), "band_rows": band_rows, "back": None}


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
_STATUS_LABELS = [("tle", "TLE"), ("swx", "SWX"), ("qrz", "QRZ"), ("freq", "FRQ"),
                  ("wspr", "WSP")]


_BAND_ORDER = [
    ("80m-40m", "80-40"),
    ("30m-20m", "30-20"),
    ("17m-15m", "17-15"),
    ("12m-10m", "12-10"),
]
_COND_COLORS = {
    "good": (60, 200, 90),
    "fair": (235, 190, 40),
    "poor": (220, 70, 60),
}
_COND_GRAY = (120, 120, 120)


def draw_band_conditions(surface, font, bands, center_x, top_y, is_day):
    """Compact HF band-condition strip from HamQSL's calculated conditions.

    Shows the day- or night-relevant Good/Fair/Poor rating per band group,
    color-coded, horizontally centered on center_x. `bands` is the nested
    {band: {"day"/"night": rating}} dict from space_weather. Returns the drawn
    bounding Rect."""
    when = "day" if is_day else "night"
    gap = 18
    inner = 6
    label_col = (170, 170, 176)

    head = font.render(f"HF {when.title()}:", True, (150, 150, 156))
    cells = []
    total = head.get_width() + gap
    for key, short in _BAND_ORDER:
        rating = (bands.get(key, {}) or {}).get(when, "") or ""
        col = _COND_COLORS.get(rating.lower(), _COND_GRAY)
        lab = font.render(short, True, label_col)
        val = font.render(rating or "--", True, col)
        cells.append((lab, val))
        total += lab.get_width() + inner + val.get_width() + gap
    total -= gap  # drop the trailing gap

    left = center_x - total // 2
    x = left
    surface.blit(head, (x, top_y))
    x += head.get_width() + gap
    for lab, val in cells:
        surface.blit(lab, (x, top_y))
        surface.blit(val, (x + lab.get_width() + inner, top_y))
        x += lab.get_width() + inner + val.get_width() + gap
    return pygame.Rect(left, top_y, total, head.get_height())


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
