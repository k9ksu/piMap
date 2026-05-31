#!/usr/bin/env python3
"""Split a full equirectangular world image into a COLS x ROWS tile grid.

One-time tool. Usage:
    python3 make_tiles.py [source.jpg]

Default source is the downloaded 21600x10800 Blue Marble intermediate.
Tiles are written to data/maptiles/t_{col}_{row}.jpg (col 0..COLS-1 west->east,
row 0..ROWS-1 north->south), each covering an equal lon/lat cell.
"""

import os
import sys

from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # trusted NASA source

COLS, ROWS = 16, 8
SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bmng_21600x10800.jpg"
OUT = os.path.join(os.path.dirname(__file__), "data", "maptiles")


def main():
    os.makedirs(OUT, exist_ok=True)
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    tw, th = w // COLS, h // ROWS
    print(f"source {w}x{h} -> {COLS}x{ROWS} grid, tiles {tw}x{th}")
    for r in range(ROWS):
        for c in range(COLS):
            box = (c * tw, r * th, (c + 1) * tw, (r + 1) * th)
            im.crop(box).save(os.path.join(OUT, f"t_{c}_{r}.jpg"), quality=88)
    print(f"wrote {COLS * ROWS} tiles to {OUT}")


if __name__ == "__main__":
    main()
