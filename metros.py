"""Largest metro areas per Maidenhead field, for the WSPR target-dot bubble.

Loads a world-cities dataset (GeoNames cities15000) once in the background and
answers "the N biggest metros inside this 2-char grid field". ASCII city names
are used so any font renders them. Cached to data/metros_cache.json like the
other on-disk datasets; the render loop never blocks on it.
"""

import io
import json
import os
import threading
import zipfile

import requests

import applog

_URL = "https://download.geonames.org/export/dump/cities15000.zip"
_CACHE = os.path.join(os.path.dirname(__file__), "data", "metros_cache.json")
_MIN_POP = 100000  # keep genuine metros; fields are large so small towns add noise

_log = applog.get_logger("metro")
_cities = []       # [name, cc, lat, lon, pop], sorted by population descending
_ready = False
_field_cache = {}  # (field, n) -> ["City, CC", ...]


def ready():
    return _ready


def start():
    threading.Thread(target=_load, daemon=True).start()


def _parse_dump(txt):
    cities = []
    for line in txt.splitlines():
        f = line.split("\t")
        try:
            pop = int(f[14])
        except (IndexError, ValueError):
            continue
        if pop < _MIN_POP:
            continue
        try:
            cities.append([f[2], f[8], float(f[4]), float(f[5]), pop])  # asciiname, cc, lat, lon
        except (IndexError, ValueError):
            continue
    cities.sort(key=lambda c: -c[4])
    return cities


def _load():
    global _cities, _ready
    try:
        with open(_CACHE) as fh:
            _cities = json.load(fh)
        _ready = True
        _log.info("Metros: %d from cache", len(_cities))
        return
    except (OSError, ValueError):
        pass
    try:
        r = requests.get(_URL, timeout=60)
        r.raise_for_status()
        txt = zipfile.ZipFile(io.BytesIO(r.content)).read("cities15000.txt").decode("utf-8")
        _cities = _parse_dump(txt)
        _ready = True
        try:
            os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
            with open(_CACHE, "w") as fh:
                json.dump(_cities, fh)
        except OSError as e:
            _log.warning("Metros: could not write cache: %s", e)
        _log.info("Metros: %d fetched", len(_cities))
    except Exception as e:
        _log.warning("Metros fetch failed: %s", e)


def _field_bbox(field):
    lon0 = (ord(field[0]) - ord('A')) * 20 - 180
    lat0 = (ord(field[1]) - ord('A')) * 10 - 90
    return lon0, lat0, lon0 + 20, lat0 + 10


def top_metros_in_field(field, n=3):
    """The n most-populous metros inside the 2-char grid field, as
    ["City, CC", ...]. Returns [] if none in the field, or None if the dataset
    is not loaded yet."""
    if not _ready:
        return None
    if not field or len(field) < 2:
        return []
    field = field[:2].upper()
    key = (field, n)
    if key in _field_cache:
        return _field_cache[key]
    lon0, lat0, lon1, lat1 = _field_bbox(field)
    out = []
    for name, cc, lat, lon, _pop in _cities:  # already sorted by population desc
        if lon0 <= lon < lon1 and lat0 <= lat < lat1:
            out.append(f"{name}, {cc}")
            if len(out) >= n:
                break
    _field_cache[key] = out
    return out
