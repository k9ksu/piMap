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
~700 MB, too much for a Pi). The lower levels are made from clean (untouched) copies
kept aside while building, so nothing is compressed or sharpened twice.

The finish, for a map seen across a room:
  tone      a midtone lift (piMap's MAP_BRIGHTNESS 1.15, without its flat grey wash):
            the land as bright, the oceans deep, the contrast kept. The browser then
            only adds what MAP_BRIGHTNESS asks beyond it.
  sharpen   a light unsharp mask on the shrunk levels (3 and below), the usual touch
            for downscaled satellite imagery; level 4 is the source, untouched.

Offline, about a minute; run once after install:

    python3 tools/build_pyramid.py            (skips if already built)
    python3 tools/build_pyramid.py --force
"""

from __future__ import annotations

import json
import os
import sys

import shutil

from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "maptiles")
OUT = os.path.join(ROOT, "data", "pyramid")
COLS, ROWS = 16, 8          # the slices
TILE = 675
TOP = 4                     # the full-resolution level
QUALITY = 92
VERSION = "2"               # bump when the finish changes: browsers fetch the new tiles
TONE_BRIGHTNESS = 1.15      # the MAP_BRIGHTNESS the tone stands for
# unsharp mask per level: (radius, percent, threshold); None = untouched
SHARPEN = {0: (1.0, 70, 2), 1: (1.0, 70, 2), 2: (1.0, 70, 2), 3: (0.8, 40, 2), 4: None}
_TONE_LUT = [min(255, max(0, round(255 * (i / 255) ** 0.80 * 0.94 + 10))) for i in range(256)] * 3


def finish(im: Image.Image, z: int) -> Image.Image:
    """A clean tile -> the tile served at level z: sharpened (if shrunk), then the tone."""
    sh = SHARPEN.get(z)
    if sh:
        im = im.filter(ImageFilter.UnsharpMask(radius=sh[0], percent=sh[1], threshold=sh[2]))
    return im.point(_TONE_LUT)


def level_grid(z: int) -> tuple[int, int]:
    """(columns, rows) of tiles at level z."""
    return 2 ** (z + 1), 2 ** z


def manifest() -> dict:
    return {"version": VERSION, "tone": TONE_BRIGHTNESS, "tile": TILE, "levels": TOP + 1,
            "grids": [list(level_grid(z)) for z in range(TOP + 1)],
            "width": [TILE * level_grid(z)[0] for z in range(TOP + 1)]}


def _path(z: int, x: int, y: int) -> str:
    return os.path.join(OUT, str(z), f"{x}_{y}.jpg")


def _clean(z: int, x: int, y: int) -> str:
    return os.path.join(OUT, "_clean", str(z), f"{x}_{y}.png")


def built_version() -> str | None:
    try:
        with open(os.path.join(OUT, "pyramid.json")) as f:
            return json.load(f).get("version")
    except (OSError, ValueError):
        return None


def build(force: bool = False, log=print) -> bool:
    done = os.path.join(OUT, "pyramid.json")
    if os.path.isfile(done) and not force and built_version() == VERSION:
        log(f"pyramid already built in {OUT} (--force to rebuild)")
        return False
    missing = [f"t_{c}_{r}.jpg" for r in range(ROWS) for c in range(COLS)
               if not os.path.isfile(os.path.join(SRC, f"t_{c}_{r}.jpg"))]
    if missing:
        raise SystemExit(f"missing {len(missing)} slices in {SRC} (e.g. {missing[0]})")
    if os.path.isfile(done):
        os.remove(done)                           # half-built never looks finished
    for z in range(TOP + 1):
        os.makedirs(os.path.join(OUT, str(z)), exist_ok=True)
        if z < TOP:
            os.makedirs(os.path.join(OUT, "_clean", str(z)), exist_ok=True)

    # levels 4 and 3 straight from the slices
    for r in range(ROWS):
        for c in range(COLS):
            with Image.open(os.path.join(SRC, f"t_{c}_{r}.jpg")) as im:
                im = im.convert("RGB")
                if im.size != (2 * TILE, 2 * TILE):
                    im = im.resize((2 * TILE, 2 * TILE), Image.LANCZOS)
                for j in range(2):
                    for i in range(2):
                        tile = im.crop((i * TILE, j * TILE, (i + 1) * TILE, (j + 1) * TILE))
                        finish(tile, TOP).save(_path(TOP, 2 * c + i, 2 * r + j), quality=QUALITY)
                half = im.resize((TILE, TILE), Image.LANCZOS)
                half.save(_clean(TOP - 1, c, r))
                finish(half, TOP - 1).save(_path(TOP - 1, c, r), quality=QUALITY)
        log(f"  slices row {r + 1}/{ROWS}")

    # each lower level from four tiles of the level above
    for z in range(TOP - 2, -1, -1):
        cols, rows = level_grid(z)
        for y in range(rows):
            for x in range(cols):
                quad = Image.new("RGB", (2 * TILE, 2 * TILE))
                for j in range(2):
                    for i in range(2):
                        with Image.open(_clean(z + 1, 2 * x + i, 2 * y + j)) as child:
                            quad.paste(child, (i * TILE, j * TILE))
                small = quad.resize((TILE, TILE), Image.LANCZOS)
                small.save(_clean(z, x, y))
                finish(small, z).save(_path(z, x, y), quality=QUALITY)
        log(f"  level {z}: {cols}x{rows}")

    shutil.rmtree(os.path.join(OUT, "_clean"), ignore_errors=True)
    with open(done, "w") as f:
        json.dump(manifest(), f)
    log(f"pyramid built in {OUT}")
    return True


if __name__ == "__main__":
    build(force="--force" in sys.argv)
