"""Satellite ground tracks from Celestrak's amateur-radio group + SGP4.

One HTTP fetch pulls every amateur-radio TLE; they're cached to disk and
refreshed every few hours. Any satellite is propagated by NORAD catalog
number. Per-satellite ground tracks are cached briefly so the render loop
isn't re-propagating thousands of points every frame.
"""

import math
import os
import threading
import time
from datetime import datetime, timezone

import requests
from sgp4.api import Satrec, jday

import config
import applog


_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle"
_TLE_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "amateur_tle_cache.txt")
_TLE_REFRESH_S = 6 * 3600   # refresh the TLE set every 6 hours when healthy
_TLE_RETRY_S = 15 * 60      # while degraded, re-attempt the fetch this often
_TLE_FAIL_LIMIT = 6         # consecutive failures before the tag escalates to red
_TRACK_CACHE_S = 1.0        # reuse a computed ground track for this long

_log = applog.get_logger("sat")
_lock = threading.Lock()
_tles = {}            # norad(int) -> (name, line1, line2)
_satrecs = {}         # norad(int) -> Satrec
_tles_loaded_at = 0.0   # last *successful* fresh load; drives the 6h cadence
_last_attempt_at = 0.0  # last fetch attempt (ok or fail); drives the 15m retry
_fetch_failures = 0     # consecutive failed fetches; >=_TLE_FAIL_LIMIT -> red
_track_cache = {}     # norad(int) -> (computed_at_realtime, [(lat, lon), ...])


def _parse_group(text):
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out = {}
    for i in range(len(lines) - 2):
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            try:
                norad = int(lines[i + 1][2:7])
            except ValueError:
                continue
            out[norad] = (lines[i].strip(), lines[i + 1], lines[i + 2])
    return out


def _fetch():
    resp = requests.get(_TLE_URL, timeout=15)
    resp.raise_for_status()
    tles = _parse_group(resp.text)
    if not tles:
        raise RuntimeError("no TLEs parsed from amateur group")
    with open(_TLE_CACHE_PATH, "w") as f:
        f.write(resp.text)
    return tles


def _set_tles(tles):
    global _tles, _tles_loaded_at
    _tles = tles
    _tles_loaded_at = time.time()
    _satrecs.clear()
    _track_cache.clear()


def _load():
    global _fetch_failures, _last_attempt_at
    # Fast path: a fresh on-disk cache (e.g. written by a sibling run) counts
    # as a clean success without touching the network.
    if os.path.exists(_TLE_CACHE_PATH):
        age = time.time() - os.path.getmtime(_TLE_CACHE_PATH)
        if age < _TLE_REFRESH_S:
            with open(_TLE_CACHE_PATH) as f:
                tles = _parse_group(f.read())
            if tles:
                _set_tles(tles)
                _fetch_failures = 0
                _last_attempt_at = time.time()
                applog.set_status("tle", "ok", f"{len(tles)} sats, cache {int(age/60)}m")
                _log.info("TLEs: loaded %d sats from cache (age %dm)", len(tles), int(age / 60))
                return
    _last_attempt_at = time.time()
    try:
        tles = _fetch()
        _set_tles(tles)
        _fetch_failures = 0
        applog.set_status("tle", "ok", f"{len(tles)} sats, fresh")
        _log.info("TLEs: fetched %d sats fresh", len(tles))
        return
    except Exception as e:
        _fetch_failures += 1
        # Keep serving whatever set we already have (in memory, or the on-disk
        # cache on a cold-start failure). Below _TLE_FAIL_LIMIT this is a
        # transient "stale"; at/after it escalates to "failed" (red). Either
        # way the retry cadence drops to _TLE_RETRY_S until a fetch succeeds.
        if not _tles and os.path.exists(_TLE_CACHE_PATH):
            with open(_TLE_CACHE_PATH) as f:
                tles = _parse_group(f.read())
            if tles:
                _set_tles(tles)
        if _tles:
            state = "failed" if _fetch_failures >= _TLE_FAIL_LIMIT else "stale"
            applog.set_status("tle", state, f"{_fetch_failures} fails: {e}")
            _log.warning(
                "TLE fetch failed (#%d, %s); serving cached %d sats [%s], retry in %dm",
                _fetch_failures, e, len(_tles), state, int(_TLE_RETRY_S / 60),
            )
            return
        applog.set_status("tle", "failed", str(e))
        _log.error("TLEs unavailable, no cache: %s", e)
        raise


def _ensure_loaded():
    with _lock:
        now = time.time()
        if not _tles:
            _load()
        elif _fetch_failures > 0:
            # Degraded: retry every _TLE_RETRY_S until a fetch succeeds. A fresh
            # cache written by another process is picked up here too (fast path).
            if now - _last_attempt_at >= _TLE_RETRY_S:
                _load()
        elif now - _tles_loaded_at >= _TLE_REFRESH_S:
            _load()


def _get_satrec(norad):
    _ensure_loaded()
    if norad not in _satrecs:
        tle = _tles.get(norad)
        if tle is None:
            return None
        _satrecs[norad] = Satrec.twoline2rv(tle[1], tle[2])
    return _satrecs[norad]


def _gmst_radians(jd_ut1):
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_sec = (67310.54841
                + (876600.0 * 3600.0 + 8640184.812866) * t
                + 0.093104 * t * t
                - 6.2e-6 * t * t * t)
    gmst_sec %= 86400.0
    if gmst_sec < 0:
        gmst_sec += 86400.0
    return math.radians(gmst_sec / 240.0)


def _teme_to_subpoint(r_teme_km, jd, fr):
    x, y, z = r_teme_km
    rxy = math.hypot(x, y)
    lat = math.degrees(math.atan2(z, rxy))
    ra = math.atan2(y, x)
    lon = math.degrees(ra - _gmst_radians(jd + fr))
    lon = ((lon + 180.0) % 360.0) - 180.0
    return lat, lon


def _propagate(sat, unix_t):
    dt = datetime.fromtimestamp(unix_t, tz=timezone.utc)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute,
                  dt.second + dt.microsecond * 1e-6)
    e, r, _v = sat.sgp4(jd, fr)
    if e != 0:
        return None
    return _teme_to_subpoint(r, jd, fr)


def subpoint(norad, now_unix):
    """Current (lat, lon) sub-point for a satellite, or None if unavailable."""
    sat = _get_satrec(norad)
    if sat is None:
        return None
    return _propagate(sat, now_unix)


def groundtrack(norad, now_unix, step_s=10.0):
    """One-orbit ground track from now_unix forward (cached ~1s).

    Track length is derived from the satellite's own mean motion, so slower
    orbits (RS-44, FO-29) get a proportionally longer track.
    """
    now_real = time.time()
    cached = _track_cache.get(norad)
    if cached and (now_real - cached[0]) < _TRACK_CACHE_S:
        return cached[1]

    sat = _get_satrec(norad)
    if sat is None:
        _track_cache[norad] = (now_real, [])
        return []

    period_min = (2.0 * math.pi / sat.no_kozai) if sat.no_kozai > 0 else 95.0
    total_s = period_min * 60.0

    pts = []
    t = 0.0
    while t <= total_s:
        sp = _propagate(sat, now_unix + t)
        if sp is not None:
            pts.append(sp)
        t += step_s

    _track_cache[norad] = (now_real, pts)
    return pts


def all_sats():
    """Return [(norad, name), ...] for every loaded TLE (empty until loaded)."""
    return sorted(((nid, _tles[nid][0]) for nid in _tles), key=lambda t: t[1])


# ----------------------------- LOOK ANGLES / PASSES -----------------------------

def _eci_to_ecef(r, gmst):
    x, y, z = r
    c, s = math.cos(gmst), math.sin(gmst)
    return (x * c + y * s, -x * s + y * c, z)


def _observer_ecef(lat_deg, lon_deg, h_km=0.0):
    a = 6378.137
    e2 = 6.69437999014e-3
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    slat, clat = math.sin(lat), math.cos(lat)
    n = a / math.sqrt(1.0 - e2 * slat * slat)
    return ((n + h_km) * clat * math.cos(lon),
            (n + h_km) * clat * math.sin(lon),
            (n * (1.0 - e2) + h_km) * slat)


def _elevation_deg(r_ecef, obs, lat_r, lon_r):
    dx = r_ecef[0] - obs[0]
    dy = r_ecef[1] - obs[1]
    dz = r_ecef[2] - obs[2]
    slat, clat = math.sin(lat_r), math.cos(lat_r)
    slon, clon = math.sin(lon_r), math.cos(lon_r)
    up = clat * clon * dx + clat * slon * dy + slat * dz
    east = -slon * dx + clon * dy
    north = -slat * clon * dx - slat * slon * dy + clat * dz
    return math.degrees(math.atan2(up, math.hypot(east, north)))


def next_pass_unix(norad, lat, lon, now_unix,
                   min_el_deg=10.0, horizon_s=6 * 3600, step_s=60.0):
    """Unix time of the next moment this sat is above min_el_deg at the QTH.

    Returns now_unix if it's already up, or None if no pass within horizon_s.
    Coarse (step_s) — fine for menu ordering, not for AOS to the second.
    """
    sat = _get_satrec(norad)
    if sat is None:
        return None
    obs = _observer_ecef(lat, lon)
    lat_r, lon_r = math.radians(lat), math.radians(lon)
    dt0 = datetime.fromtimestamp(now_unix, tz=timezone.utc)
    jd, fr0 = jday(dt0.year, dt0.month, dt0.day, dt0.hour, dt0.minute,
                   dt0.second + dt0.microsecond * 1e-6)
    dstep = step_s / 86400.0
    steps = int(horizon_s / step_s) + 1
    for i in range(steps):
        fr = fr0 + i * dstep
        e, r, _v = sat.sgp4(jd, fr)
        if e != 0:
            continue
        rec = _eci_to_ecef(r, _gmst_radians(jd + fr))
        if _elevation_deg(rec, obs, lat_r, lon_r) >= min_el_deg:
            return now_unix + i * step_s
    return None


_schedule_lock = threading.Lock()
_schedule = {}              # norad -> pass_unix or None
_schedule_computed_at = 0.0
_scheduler_started = False


def _compute_schedule(lat, lon):
    _ensure_loaded()
    min_el = float(getattr(config, "PASS_MIN_ELEVATION", 10.0))
    now = time.time()
    result = {}
    for norad in list(_tles.keys()):
        try:
            result[norad] = next_pass_unix(norad, lat, lon, now, min_el_deg=min_el)
        except Exception:
            result[norad] = None
    global _schedule, _schedule_computed_at
    with _schedule_lock:
        _schedule = result
        _schedule_computed_at = time.time()
    n_up = sum(1 for v in result.values() if v is not None)
    _log.info("Pass schedule: %d/%d sats have a pass within 6h", n_up, len(result))


def start_pass_scheduler(lat, lon, interval_s=120.0):
    """Recompute the whole next-pass schedule in a background thread."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _run():
        while True:
            try:
                _compute_schedule(lat, lon)
            except Exception as e:
                _log.warning("pass schedule failed: %s", e)
            time.sleep(interval_s)

    threading.Thread(target=_run, daemon=True).start()


def get_schedule():
    """Return (computed_at_unix, {norad: pass_unix or None})."""
    with _schedule_lock:
        return _schedule_computed_at, dict(_schedule)


def preload_async():
    """Load the TLE set in a background thread so status is known at startup."""
    applog.set_status("tle", "loading", "")

    def _run():
        try:
            _ensure_loaded()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
