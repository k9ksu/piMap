"""On-demand high-resolution map tiles for deep zoom.

The world is split into a COLS x ROWS grid of equal lon/lat cells (see
make_tiles.py). Only the tiles covering the current view are loaded, with an
LRU cache, so the full high-res image is never resident at once.
"""

import os
from collections import OrderedDict

import pygame

_DIR = os.path.join(os.path.dirname(__file__), "data", "maptiles")
COLS, ROWS = 16, 8
DLON = 360.0 / COLS
DLAT = 180.0 / ROWS


class TileManager:
    def __init__(self, cache_max=20):
        self.cache = OrderedDict()       # (c, r) -> Surface
        self.cache_max = cache_max
        self.available = os.path.isdir(_DIR) and any(
            n.startswith("t_") and n.endswith(".jpg") for n in os.listdir(_DIR)
        )

    def tile_bounds(self, c, r):
        """Return (lon0, lon1, lat_south, lat_north) for tile (c, r)."""
        lon0 = -180.0 + c * DLON
        lat_n = 90.0 - r * DLAT
        return lon0, lon0 + DLON, lat_n - DLAT, lat_n

    def visible_tiles(self, lon_min, lon_max, lat_min, lat_max):
        c0 = max(0, int((lon_min + 180.0) // DLON))
        c1 = min(COLS - 1, int((lon_max + 180.0) // DLON))
        r0 = max(0, int((90.0 - lat_max) // DLAT))
        r1 = min(ROWS - 1, int((90.0 - lat_min) // DLAT))
        return [(c, r) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]

    def get(self, c, r):
        key = (c, r)
        surf = self.cache.get(key)
        if surf is not None:
            self.cache.move_to_end(key)
            return surf
        try:
            surf = pygame.image.load(os.path.join(_DIR, f"t_{c}_{r}.jpg")).convert()
        except Exception:
            return None
        self.cache[key] = surf
        while len(self.cache) > self.cache_max:
            self.cache.popitem(last=False)
        return surf

    def clear(self):
        self.cache.clear()
