"""Political boundary polylines from Natural Earth GeoJSON.

Loads world country borders plus US state borders as lists of (lat, lon)
polylines for the map overlay. GeoJSON stores coordinates as [lon, lat].
"""

import json
import os

_DIR = os.path.join(os.path.dirname(__file__), "data", "boundaries")


def _polylines(path, keep=None):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return []
    out = []
    for feat in data.get("features", []):
        if keep and not keep(feat.get("properties", {})):
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "LineString":
            out.append([(lat, lon) for lon, lat in coords])
        elif gtype == "MultiLineString":
            for line in coords:
                out.append([(lat, lon) for lon, lat in line])
    return out


def _chaikin(points, iterations):
    """Smooth a polyline by Chaikin corner-cutting (endpoints preserved)."""
    for _ in range(iterations):
        if len(points) < 3:
            break
        smoothed = [points[0]]
        for a, b in zip(points, points[1:]):
            smoothed.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            smoothed.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        smoothed.append(points[-1])
        points = smoothed
    return points


def _bbox(pts):
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return (min(lats), max(lats), min(lons), max(lons))


def load(state_countries=("USA",), smooth_iters=0, coastline=True):
    """Return [(polyline, bbox), ...] for country borders, sub-national borders
    of the given countries (ADM0_A3), and coastlines. Optionally smoothed.

    polyline is [(lat, lon), ...]; bbox is (min_lat, max_lat, min_lon, max_lon)
    for cheap offscreen culling at render time.
    """
    # Smooth the sparse/angular political borders; coastlines are already dense.
    borders = _polylines(os.path.join(_DIR, "countries.geojson"))
    borders += _polylines(
        os.path.join(_DIR, "states_provinces.geojson"),
        keep=lambda p: p.get("ADM0_A3") in state_countries,
    )
    if smooth_iters > 0:
        borders = [_chaikin(p, smooth_iters) for p in borders]

    polys = borders
    if coastline:
        polys = polys + _polylines(os.path.join(_DIR, "coastline.geojson"))
    return [(p, _bbox(p)) for p in polys if len(p) >= 2]
