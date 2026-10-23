#!/usr/bin/env python3
"""Build piMap 2.0's map tile pyramid from the Blue Marble slices.

piMap's high-res imagery is the 21600x10800 Blue Marble cut into 16x8 slices of
1350x1350 (data/maptiles/t_{col}_{row}.jpg, see make_tiles.py). The browser wants the
same imagery at several sizes, so that at any zoom it draws tiles about the size they
appear on screen: never stretched soft, never needlessly large.

    level 0   1350 x  675    2 x  1 tiles
    level 1   2700 x 1350    4 x  2
    level 2   5400 x 2700    8 x  4
    level 3  10800 x 5400   16 x  8
    level 4  21600 x 10800  32 x 16   (full resolution)

Every tile is 675x675, written to data/pyramid/{level}/{x}_{y}.jpg. Level 4 is each
slice cut in four, level 3 each slice scaled to half, and each lower level four tiles
of the one above, so the full-size image is never in memory at once (it would be
~700 MB, too much for a Pi). Offline, about a minute; run once after install:

    python3 tools/build_pyramid.py            (skips if already built)
    python3 tools/build_pyramid.py --force
"""

from __future__ import annotations

import json
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "maptiles")
OUT = os.path.join(ROOT, "data", "pyramid")
COLS, ROWS = 16, 8          # the slices
TILE = 675
TOP = 4                     # the full-resolution level
QUALITY = 86


def level_grid(z: int) -> tuple[int, int]:
    """(columns, rows) of tiles at level z."""
    return 2 ** (z + 1), 2 ** z


def manifest() -> dict:
    return {"tile": TILE, "levels": TOP + 1,
            "grids": [list(level_grid(z)) for z in range(TOP + 1)],
            "width": [TILE * level_grid(z)[0] for z in range(TOP + 1)]}


def _path(z: int, x: int, y: int) -> str:
    return os.path.join(OUT, str(z), f"{x}_{y}.jpg")


def build(force: bool = False, log=print) -> bool:
    done = os.path.join(OUT, "pyramid.json")
    if os.path.isfile(done) and not force:
        log(f"pyramid already built in {OUT} (--force to rebuild)")
        return False
    missing = [f"t_{c}_{r}.jpg" for r in range(ROWS) for c in range(COLS)
               if not os.path.isfile(os.path.join(SRC, f"t_{c}_{r}.jpg"))]
    if missing:
        raise SystemExit(f"missing {len(missing)} slices in {SRC} (e.g. {missing[0]})")
    for z in range(TOP + 1):
        os.makedirs(os.path.join(OUT, str(z)), exist_ok=True)

    # levels 4 and 3 straight from the slices
    for r in range(ROWS):
        for c in range(COLS):
            with Image.open(os.path.join(SRC, f"t_{c}_{r}.jpg")) as im:
                im = im.convert("RGB")
                if im.size != (2 * TILE, 2 * TILE):
                    im = im.resize((2 * TILE, 2 * TILE), Image.LANCZOS)
                for j in range(2):
                    for i in range(2):
                        im.crop((i * TILE, j * TILE, (i + 1) * TILE, (j + 1) * TILE)) \
                          .save(_path(TOP, 2 * c + i, 2 * r + j), quality=QUALITY)
                im.resize((TILE, TILE), Image.LANCZOS).save(_path(TOP - 1, c, r), quality=QUALITY)
        log(f"  slices row {r + 1}/{ROWS}")

    # each lower level from four tiles of the level above
    for z in range(TOP - 2, -1, -1):
        cols, rows = level_grid(z)
        for y in range(rows):
            for x in range(cols):
                quad = Image.new("RGB", (2 * TILE, 2 * TILE))
                for j in range(2):
                    for i in range(2):
                        with Image.open(_path(z + 1, 2 * x + i, 2 * y + j)) as child:
                            quad.paste(child, (i * TILE, j * TILE))
                quad.resize((TILE, TILE), Image.LANCZOS).save(_path(z, x, y), quality=QUALITY)
        log(f"  level {z}: {cols}x{rows}")

    with open(done, "w") as f:
        json.dump(manifest(), f)
    log(f"pyramid built in {OUT}")
    return True


if __name__ == "__main__":
    build(force="--force" in sys.argv)
