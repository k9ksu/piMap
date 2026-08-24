"""Live FT8 propagation monitor, fed by the PSKReporter MQTT stream.

Companion to wspr_monitor: same public shape (configure / get_status /
request_detail / get_detail), but a very different data path.

WSPR answers "is this path physically open" -- beacons run 24/7, so silence
means closed. FT8 answers "is this path open AND is anybody on it" -- far more
traffic, real callsigns you could work, but a quiet band looks identical to a
dead one. piMap shows both columns side by side and lets you judge.

How the locality works
----------------------
PSKReporter publishes every spot to a topic whose levels include the sender's
and receiver's 4-character grid squares:

    pskr/filter/v2/{band}/{mode}/{tx_call}/{rx_call}/{tx_grid4}/{rx_grid4}/...

MQTT wildcards match whole levels, so a radius around the QTH is enumerated
into the 4-char squares it covers and subscribed one topic per square, twice
(once with our square as the receiver, once as the sender). The broker does
the filtering; we never see the global firehose. At 800 km that is ~131
squares / 262 filters and about 35 spots a second, against roughly 15,000/sec
unfiltered.

Everything arrives on paho's network thread, is folded straight into per
(field, band) rolling buckets, and is dropped unless it belongs to a field we
actually have a target dot on. Raw spots are never accumulated globally.
"""

import math
import threading
import time
from collections import deque

try:
    import paho.mqtt.client as mqtt
except ImportError:                      # pragma: no cover - optional dependency
    mqtt = None

import applog
import config
import maidenhead

_log = applog.get_logger("ft8")
applog.set_status("ft8", "loading", "")

_HOST = getattr(config, "FT8_MQTT_HOST", "mqtt.pskreporter.info")
_PORT = int(getattr(config, "FT8_MQTT_PORT", 1883))
_RADIUS_KM = float(getattr(config, "FT8_HOME_RADIUS_KM", 800))
_WINDOW_MIN = float(getattr(config, "FT8_WINDOW_MINUTES", 15))
_GREEN_AGE_MIN = float(getattr(config, "FT8_GREEN_MAX_AGE_MIN", 6))
_GREEN_MIN_STATIONS = int(getattr(config, "FT8_GREEN_MIN_STATIONS", 5))
_YELLOW_MIN_STATIONS = int(getattr(config, "FT8_YELLOW_MIN_STATIONS", 2))
_BUCKET_CAP = int(getattr(config, "FT8_BUCKET_MAX_SPOTS", 400))
_MODE = getattr(config, "WSPR_MODE", "FT8")

# Thresholds on the best FT8 report (dB) for each target mode. These are NOT
# the WSPR numbers: FT8 reports come from stations running 25-100 W against
# WSPR's 1-5 W, so the same dB figure means a much weaker path here. An FT8
# decode is already proof the path carries FT8, so that row is lenient; CW and
# especially SSB need a lot more margin. Starting points, tune to taste.
_MODE_THRESHOLDS = dict(getattr(config, "FT8_MODE_THRESHOLDS", {
    "FT8": (-10, -21),
    "CW": (0, -10),
    "SSB": (12, 3),
}))
if _MODE not in _MODE_THRESHOLDS:
    _MODE = next(iter(_MODE_THRESHOLDS))

# PSKReporter band strings -> the integer MHz codes wspr_monitor already uses,
# so both columns key off the same band identity.
_BAND_CODES = {
    "2200m": -1, "630m": 0, "160m": 1, "80m": 3, "60m": 5, "40m": 7,
    "30m": 10, "20m": 14, "17m": 18, "15m": 21, "12m": 24, "10m": 28,
    "6m": 50, "4m": 70, "2m": 144, "1.25m": 222, "70cm": 430,
}

_lock = threading.Lock()
_client = None
_started = False
_home_lat = float(getattr(config, "HOME_LAT", 0.0))
_home_lon = float(getattr(config, "HOME_LON", 0.0))
_local_squares = set()      # 4-char grids counted as "here"
_target_fields = set()      # 2-char fields we have dots on; everything else dropped
_buckets = {}               # (field, band) -> deque[(t, snr, dx_call, dx_grid, local_call, inbound)]
_spots_seen = 0             # accepted into buckets
_spots_dropped = 0          # arrived but not for a target field
_connected = False
# get_status() is called from the render loop every frame, but the underlying
# buckets only change as fast as spots arrive. Recomputing a full scan at 60 fps
# burned real CPU on the Pi for no new information, so the snapshot is cached
# for a beat. get_detail() is only computed while a drill-down is open, so it
# is left uncached.
_STATUS_TTL_S = float(getattr(config, "FT8_STATUS_TTL_SECONDS", 2.0))
_status_cache = None
_status_cache_key = None
_status_cache_at = 0.0


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def _gc_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def local_squares(lat, lon, radius_km):
    """The 4-char grid squares within radius_km of (lat, lon).

    A square is 1 deg of latitude by 2 deg of longitude, so candidates are
    walked on that lattice and kept if the centre is within the radius plus
    half a diagonal -- otherwise squares straddling the boundary get clipped
    and the edge of the circle goes blind."""
    out = set()
    pad = _gc_km(lat, lon, lat + 0.5, lon + 1.0)
    dlat_max = int(radius_km / 111.0) + 2
    dlon_max = int(radius_km / (111.0 * max(0.15, math.cos(math.radians(lat))) * 2.0)) + 2
    for dlat in range(-dlat_max, dlat_max + 1):
        la = lat + dlat * 1.0
        if not -90.0 < la < 90.0:
            continue
        for dlon in range(-dlon_max, dlon_max + 1):
            lo = lon + dlon * 2.0
            if lo <= -180.0 or lo > 180.0:
                continue
            if _gc_km(lat, lon, la, lo) <= radius_km + pad:
                out.add(maidenhead.latlon_to_maidenhead(la, lo, 4).upper())
    return out


def home_scope_label():
    return "%d km" % int(_RADIUS_KM)


def modes():
    return list(_MODE_THRESHOLDS.keys())


def set_mode(name):
    global _MODE
    if name in _MODE_THRESHOLDS:
        _MODE = name


def band_code(band_str):
    return _BAND_CODES.get(band_str)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def configure(targets):
    """Set the fields we care about and start the MQTT client on first call.

    Spots for any other field are discarded the moment they arrive, so the
    memory cost tracks the number of dots on the map, not the feed rate."""
    global _started, _client
    fields = {(t.get("field") or "").upper()[:2] for t in targets}
    fields.discard("")
    with _lock:
        gone = set(_buckets) - {k for k in _buckets if k[0] in fields}
        for k in gone:
            _buckets.pop(k, None)
        _target_fields.clear()
        _target_fields.update(fields)
    if _started:
        return
    _started = True
    if mqtt is None:
        # piMap imports this module unconditionally, so a missing optional
        # dependency must degrade to "no FT8 column", never take down the map.
        applog.set_status("ft8", "failed", "paho-mqtt not installed")
        _log.warning("FT8 monitor disabled: paho-mqtt is not installed "
                     "(sudo apt install python3-paho-mqtt)")
        return
    _local_squares.update(local_squares(_home_lat, _home_lon, _RADIUS_KM))
    _log.info("FT8 monitor: %s of %.2f,%.2f -> %d grid squares, %d topic filters",
              home_scope_label(), _home_lat, _home_lon,
              len(_local_squares), len(_local_squares) * 2)
    _client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id="pimap-%d" % (int(time.time()) % 100000))
    _client.on_connect = _on_connect
    _client.on_disconnect = _on_disconnect
    _client.on_message = _on_message
    # paho owns reconnect/backoff and runs its own network thread.
    _client.reconnect_delay_set(min_delay=2, max_delay=120)
    try:
        _client.connect_async(_HOST, _PORT, keepalive=60)
        _client.loop_start()
    except Exception as e:
        applog.set_status("ft8", "failed", str(e))
        _log.warning("FT8 MQTT connect failed: %s", e)


def _on_connect(client, userdata, flags, reason_code, properties=None):
    global _connected
    if reason_code != 0:
        applog.set_status("ft8", "failed", "connect rc=%s" % reason_code)
        _log.warning("FT8 MQTT refused: %s", reason_code)
        return
    _connected = True
    # Resubscribe every time: a reconnect starts from a clean session.
    subs = ([("pskr/filter/v2/+/FT8/+/+/+/%s/+/+" % s, 0) for s in sorted(_local_squares)] +
            [("pskr/filter/v2/+/FT8/+/+/%s/+/+/+" % s, 0) for s in sorted(_local_squares)])
    for i in range(0, len(subs), 100):     # keep each SUBSCRIBE packet sane
        client.subscribe(subs[i:i + 100])
    applog.set_status("ft8", "ok", "%d squares" % len(_local_squares))
    _log.info("FT8 MQTT connected, subscribed %d filters", len(subs))


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    global _connected
    _connected = False
    applog.set_status("ft8", "stale", "reconnecting")
    _log.warning("FT8 MQTT disconnected (%s) - paho will retry", reason_code)


def _on_message(client, userdata, msg):
    """Runs on paho's network thread ~35x/sec. Keep it cheap: parse, decide
    which end is us, drop anything not on a target field, append, done."""
    global _spots_seen, _spots_dropped
    try:
        d = _json_loads(msg.payload)
        sl = (d.get("sl") or "").upper()
        rl = (d.get("rl") or "").upper()
        band = _BAND_CODES.get(d.get("b"))
        snr = d.get("rp")
        if band is None or not isinstance(snr, int) or not sl or not rl:
            return
        rx_local = rl[:4] in _local_squares
        tx_local = sl[:4] in _local_squares
        if rx_local == tx_local:
            # Both ends local (a local QSO) or neither (shouldn't happen given
            # the subscription) - neither tells us anything about a DX path.
            return
        inbound = rx_local            # someone near me decoded a distant station
        dx_grid = sl if inbound else rl
        field = dx_grid[:2]
        if field not in _target_fields:
            _spots_dropped += 1
            return
        key = (field, band)
        rec = (time.time(), snr,
               d.get("sc") if inbound else d.get("rc"), dx_grid,
               d.get("rc") if inbound else d.get("sc"), inbound)
        with _lock:
            q = _buckets.get(key)
            if q is None:
                q = _buckets[key] = deque(maxlen=_BUCKET_CAP)
            q.append(rec)
        _spots_seen += 1
    except Exception:
        return


def _json_loads(payload):
    import json
    return json.loads(payload.decode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# Classification / snapshot
# ---------------------------------------------------------------------------
def _classify(stations, age_min, best_snr):
    """How many DISTINCT stations matters more than raw SNR here.

    FT8 is dense enough that almost any path shows one or two decodes at some
    point in a 15-minute window -- keying green off SNR alone painted 30 of 33
    cells green, which is just "somebody was on" rather than "the band is
    open". A real opening looks like a crowd: several distinct DX callsigns,
    recently, at a workable signal level."""
    if stations <= 0:
        return "none"
    green_snr, yellow_snr = _MODE_THRESHOLDS.get(_MODE, (-10, -21))
    if (stations >= _GREEN_MIN_STATIONS and best_snr >= green_snr
            and age_min <= _GREEN_AGE_MIN):
        return "green"
    if stations >= _YELLOW_MIN_STATIONS and best_snr >= yellow_snr:
        return "yellow"
    return "weak"


def _live(q, cutoff):
    return [r for r in q if r[0] >= cutoff]


def get_status(fields):
    """{field: {band: {...}}} for the requested fields. Never blocks long.

    Cached for _STATUS_TTL_S -- the render loop asks every frame, the answer
    changes on the order of once a second."""
    global _status_cache, _status_cache_key, _status_cache_at
    now = time.time()
    want = frozenset((f or "").upper()[:2] for f in fields)
    if (_status_cache is not None and _status_cache_key == want
            and now - _status_cache_at < _STATUS_TTL_S):
        return _status_cache
    cutoff = now - _WINDOW_MIN * 60.0
    out = {}
    with _lock:
        # Sweep expired spots out of EVERY bucket, not just the ones asked for.
        # deques are append-ordered, so the oldest are always on the left. The
        # maxlen cap alone bounds memory but never releases it: a band that
        # went quiet after a burst would pin its spots until the process died.
        for k in list(_buckets):
            q = _buckets[k]
            while q and q[0][0] < cutoff:
                q.popleft()
            if not q:
                del _buckets[k]
        items = [(k, list(v)) for k, v in _buckets.items() if k[0] in want]
    for (field, band), rows in items:
        rows = [r for r in rows if r[0] >= cutoff]
        if not rows:
            continue
        best = max(r[1] for r in rows)
        newest = max(r[0] for r in rows)
        calls = {r[2] for r in rows if r[2]}
        age_min = max(0.0, (now - newest) / 60.0)
        out.setdefault(field, {})[band] = {
            "cnt": len(rows), "stations": len(calls), "best_snr": best,
            "age_min": age_min, "state": _classify(len(calls), age_min, best),
            "n_in": sum(1 for r in rows if r[5]),
            "n_out": sum(1 for r in rows if not r[5]),
        }
    _status_cache, _status_cache_key, _status_cache_at = out, want, now
    return out


def get_detail(field, band):
    """Per-station drill-down for one (field, band), strongest first. Local --
    no network call, the spots are already in memory."""
    cutoff = time.time() - _WINDOW_MIN * 60.0
    now = time.time()
    key = ((field or "").upper()[:2], band)
    with _lock:
        rows = [r for r in _buckets.get(key, ()) if r[0] >= cutoff]
    groups = {}
    for t, snr, dx, dx_grid, local_call, inbound in rows:
        g = groups.get((dx, inbound))
        if g is None:
            g = groups[(dx, inbound)] = {"sign": dx or "?", "loc": dx_grid,
                                         "best_snr": snr, "count": 0,
                                         "age_min": (now - t) / 60.0,
                                         "outbound": not inbound, "by": local_call}
        g["count"] += 1
        if snr > g["best_snr"]:
            g["best_snr"], g["by"] = snr, local_call
        g["age_min"] = min(g["age_min"], max(0.0, (now - t) / 60.0))
        if len(dx_grid) > len(g["loc"]):
            g["loc"] = dx_grid
    allg = sorted(groups.values(), key=lambda g: (-g["best_snr"], g["age_min"]))
    return {
        "hearing": [g for g in allg if not g["outbound"]],
        "heard_by": [g for g in allg if g["outbound"]],
        "n_in": sum(1 for r in rows if r[5]),
        "n_out": sum(1 for r in rows if not r[5]),
        "window_min": _WINDOW_MIN,
        "home_scope": home_scope_label(),
    }


def stats():
    with _lock:
        buckets = len(_buckets)
        held = sum(len(v) for v in _buckets.values())
    return {"connected": _connected, "squares": len(_local_squares),
            "fields": len(_target_fields), "buckets": buckets, "held": held,
            "accepted": _spots_seen, "dropped": _spots_dropped}
