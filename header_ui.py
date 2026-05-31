"""Header-bar UI helpers: font selection, callsign title rendering, QTH-local
clock resolution, and hex-color parsing."""

import os
from datetime import datetime, timezone, timedelta

import pygame

import config

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
HEADER_FONTS = {
    "AnonymousPro": "AnonymousPro-Bold.ttf",
    "ShareTechMono": "ShareTechMono-Regular.ttf",
    "SpaceMono": "SpaceMono-Bold.ttf",
    "JetBrainsMono": "JetBrainsMono-Bold.ttf",
    "OverpassMono": "Overpass-Mono.ttf",
    "B612Mono": "B612Mono-Bold.ttf",
}


def header_font_path():
    name = getattr(config, "HEADER_FONT", "AnonymousPro")
    return os.path.join(_FONT_DIR, HEADER_FONTS.get(name, "AnonymousPro-Bold.ttf"))


def parse_hex_color(s, fallback=(245, 245, 245)):
    try:
        c = pygame.Color(s)
        return (c.r, c.g, c.b)
    except Exception:
        return fallback


_home_tz = None
_home_tz_resolved = False


def home_localtime(home_lat, home_lon):
    """QTH-local datetime + zone label, derived from the home coordinates.

    Uses timezonefinder (DST-accurate) if installed; otherwise falls back to a
    longitude offset (no DST), so the clock is always driven by lat/lon."""
    global _home_tz, _home_tz_resolved
    if not _home_tz_resolved:
        _home_tz_resolved = True
        try:
            from timezonefinder import TimezoneFinder
            from zoneinfo import ZoneInfo
            name = TimezoneFinder().timezone_at(lat=home_lat, lng=home_lon)
            if name:
                _home_tz = ZoneInfo(name)
        except Exception:
            _home_tz = None

    now = datetime.now(timezone.utc)
    if _home_tz is not None:
        lt = now.astimezone(_home_tz)
        return lt, lt.strftime("%Z")
    off = round(home_lon / 15.0)
    return now + timedelta(hours=off), f"UTC{off:+d}"


def _fit_font_to_ink(path, text, target_ink_h):
    """Pick a font size so the rendered ink height of `text` ~= target_ink_h."""
    def make(sz):
        try:
            return pygame.font.Font(path, sz)
        except Exception:
            return pygame.font.SysFont("monospace", sz, bold=True)

    size = max(8, int(target_ink_h))
    font = make(size)
    for _ in range(5):
        surf = font.render(text or "X", True, (255, 255, 255)).convert_alpha()
        rects = pygame.mask.from_surface(surf).get_bounding_rects()
        if not rects:
            break
        ink_h = max(r.bottom for r in rects) - min(r.top for r in rects)
        if ink_h <= 0 or abs(ink_h - target_ink_h) <= 1:
            break
        size = max(8, int(round(size * target_ink_h / ink_h)))
        font = make(size)
    return font


def make_title(callsign, bar_h):
    """Render the callsign sized to HEADER_FILL of the bar, in HEADER_COLOR,
    and return (surface, draw_pos) vertically centered by its ink."""
    fill = float(getattr(config, "HEADER_FILL", 0.80))
    color = parse_hex_color(getattr(config, "HEADER_COLOR", "#F0F0F6"))
    target = max(8, int(bar_h * fill))
    font = _fit_font_to_ink(header_font_path(), callsign, target)
    surf = font.render(callsign, True, color)
    rects = pygame.mask.from_surface(surf).get_bounding_rects()
    if rects:
        ink_cy = (min(r.top for r in rects) + max(r.bottom for r in rects)) // 2
    else:
        ink_cy = surf.get_height() // 2
    return surf, (20, bar_h // 2 - ink_cy)
