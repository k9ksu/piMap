"""Directional WSPR propagation monitor for the map's target dots.

Polls the wspr.live ClickHouse HTTP API (db1.wspr.live) in the background and,
for each user-placed target, reports whether there is recent WSPR propagation
between the QTH's Maidenhead *field* and the target's field, per band.

Same non-blocking pattern as space_weather / the sat modules: the render loop
calls get_status() every frame and always gets a cached snapshot; the network
work happens on a daemon thread. configure() may be called at any time when the
target set changes.

The "home" end of a path is a real distance circle around HOME_LAT/HOME_LON
(WSPR_HOME_RADIUS_KM), not the 2-char Maidenhead field. A field is a 10x20
degree box and you are rarely in the middle of it -- K9KSU sits in the NW
corner of EM, which reaches ~1490 km east but only ~240 km west, so field
matching credited this QTH with openings that only existed 700-1300 km to its
east. Set WSPR_HOME_RADIUS_KM = 0 to go back to field matching.

Status per band (thresholds from config):
  green  = fresh spots (recent + enough of them) -> band open now
  yellow = some spots in the window but sparse/older -> marginal
  red    = nothing in the window -> closed

An "opening" is edge-triggered on the *dot*: it fires only when a target's
summary transitions non-green -> green, and never again while it stays green,
so a path that is open all afternoon chimes exactly once. The cooldown is only
an anti-flap floor for a path that keeps dropping out and coming back.
"""

import threading
import time
import urllib.parse

import requests

import applog
import config

_URL = getattr(config, "WSPR_DB_URL", "https://db1.wspr.live/")
_WINDOW_MIN = int(getattr(config, "WSPR_WINDOW_MINUTES", 30))
_POLL_S = float(getattr(config, "WSPR_POLL_SECONDS", 300))
_GREEN_MIN = int(getattr(config, "WSPR_GREEN_MIN_SPOTS", 2))
_GREEN_AGE_MIN = float(getattr(config, "WSPR_GREEN_MAX_AGE_MIN", 16))
_ALERT_COOLDOWN_S = float(getattr(config, "WSPR_ALERT_COOLDOWN_SECONDS", 1800))
# How many polls a target stays "red" (recently closed) after dropping from
# active to nothing-heard before it fades to the quiet white state.
_RECENT_CLOSE_POLLS = int(getattr(config, "WSPR_RECENT_CLOSE_POLLS", 5))
# "Local" = within this many km of the QTH. 0 falls back to Maidenhead-field
# matching on the home end (the old behavior).
_HOME_RADIUS_KM = float(getattr(config, "WSPR_HOME_RADIUS_KM", 800))
_HOME_LAT = float(getattr(config, "HOME_LAT", 0.0))
_HOME_LON = float(getattr(config, "HOME_LON", 0.0))

# Per-mode (green, yellow) thresholds on the best reported WSPR SNR (dB, in the
# 2.5 kHz reference bandwidth wsprnet uses). Harder modes need a stronger path:
# FT8 decodes very weak, CW needs more, SSB needs a solid signal. A dot is green
# if the path's best WSPR SNR clears the green bar, yellow past the yellow bar,
# else "weak" (heard but unlikely for that mode).
_MODE_THRESHOLDS = dict(getattr(config, "WSPR_MODE_THRESHOLDS", {
    "FT8": (-12, -20),
    "CW": (-4, -13),
    "SSB": (6, -3),
}))
_mode = getattr(config, "WSPR_MODE", "FT8")
if _mode not in _MODE_THRESHOLDS:
    _mode = next(iter(_MODE_THRESHOLDS))
# Bands to display, as wspr.live integer band codes (MHz), low -> high.
_BANDS = list(getattr(config, "WSPR_BANDS", [1, 3, 7, 10, 14, 18, 21, 24, 28, 50]))

_BAND_LABELS = {
    -1: "2200m", 0: "630m", 1: "160m", 3: "80m", 5: "60m", 7: "40m",
    10: "30m", 14: "20m", 18: "17m", 21: "15m", 24: "12m", 28: "10m",
    50: "6m", 70: "4m", 144: "2m", 222: "1.25m", 430: "70cm",
    902: "33cm", 1296: "23cm",
}

_log = applog.get_logger("wspr")
applog.set_status("wspr", "loading", "")

_lock = threading.Lock()
_wake = threading.Event()  # set by configure() to trigger an immediate re-poll
_home_field = None
_targets = []          # list of {"id":..., "field": "JO", "label": "..."}
_status = {}           # target_id -> {"summary": state, "bands": [ {..}, .. ]}
_openings = []         # queued opening events for the audio/alert consumer
_last_green_at = {}    # target_id -> monotonic time we last alerted
_prev_summary = {}     # target_id -> previous poll's dot summary (edge detect)
_seen_first_poll = False
_poll_count = 0        # increments each completed poll (for recency windows)
_last_active_poll = {} # target_id -> _poll_count when it was last green/yellow
_thread = None


def band_label(band):
    return _BAND_LABELS.get(band, f"{band}MHz")


def configure(home_field, targets):
    """Set the QTH field and the current target list, and start the poller.

    targets: iterable of dicts each with at least "id" and "field" (2-char).
    Safe to call repeatedly as targets are added/removed."""
    global _home_field, _targets, _thread
    with _lock:
        _home_field = (home_field or "").upper()[:2]
        _targets = [dict(t) for t in targets]
    if _thread is None:
        _thread = threading.Thread(target=_run, daemon=True)
        _thread.start()
    else:
        _wake.set()  # targets changed — re-poll now instead of waiting out the TTL


def get_status():
    """Snapshot: {target_id: {"summary": state, "bands": [...]}}. Never blocks."""
    with _lock:
        return {tid: {"summary": v["summary"], "bands": list(v["bands"])}
                for tid, v in _status.items()}


def pop_openings():
    """Drain and return queued opening events since the last call."""
    with _lock:
        out = _openings[:]
        _openings.clear()
    return out


# --- Per-band drill-down --------------------------------------------------
# One (target, band) cell at a time: the individual spots behind the summary,
# fetched on demand from a throwaway thread so the map never blocks and the
# 5-minute summary poll is not disturbed.
_DETAIL_LIMIT = int(getattr(config, "WSPR_DETAIL_MAX_SPOTS", 60))
_detail = None      # {"key":(tid,band), "state":"loading"|"ok"|"error", "spots":[...]}
_detail_seq = 0     # newest request wins; stale replies are dropped


def request_detail(target_id, field, band, label=""):
    """Start fetching the spots behind one (target, band) cell. Returns at once;
    poll get_detail() for progress. A newer request supersedes an in-flight one."""
    global _detail, _detail_seq
    with _lock:
        _detail_seq += 1
        seq = _detail_seq
        home = _home_field
        _detail = {"key": (target_id, band), "label": label or field, "field": field,
                   "band": band, "band_label": band_label(band), "home": home,
                   "state": "loading", "spots": [], "error": "",
                   "hearing": [], "heard_by": [], "n_in": 0, "n_out": 0,
                   "truncated": False, "window_min": _WINDOW_MIN,
                   "home_scope": home_scope_label()}
    threading.Thread(target=_fetch_detail, args=(seq, target_id, home, field, band),
                     daemon=True).start()


def get_detail():
    """Snapshot of the current drill-down, or None. Never blocks."""
    with _lock:
        if not _detail:
            return None
        d = dict(_detail)
        d["spots"] = list(_detail["spots"])
        return d


def clear_detail():
    global _detail
    with _lock:
        _detail = None


def _group_by_station(spots):
    """Collapse repeat spots of one DX callsign -- the same station heard by
    several receivers in our field, or over several transmit cycles -- into a
    single row. Best SNR wins (with the distance/power from that same spot),
    plus how many times it was spotted and how recently. Strongest first."""
    by = {}
    for s in spots:
        g = by.get(s["dx"])
        if g is None:
            g = by[s["dx"]] = {"sign": s["dx"], "loc": s["dx_loc"],
                               "best_snr": s["snr"], "km": s["km"], "dbm": s["dbm"],
                               "count": 0, "age_min": s["age_min"]}
        g["count"] += 1
        if s["snr"] > g["best_snr"]:
            g["best_snr"], g["km"], g["dbm"] = s["snr"], s["km"], s["dbm"]
        if s["age_min"] < g["age_min"]:
            g["age_min"] = s["age_min"]
        if len(s["dx_loc"]) > len(g["loc"]):
            g["loc"] = s["dx_loc"]        # prefer the more precise grid square
    return sorted(by.values(), key=lambda g: (-g["best_snr"], g["age_min"]))


def _detail_query(home, field, band):
    """Each direction gets its OWN limit. A single "ORDER BY time LIMIT n" over
    both directions lets a busy side crowd the other out of the result set --
    and "who are we hearing" is usually the sparser half, so it is exactly the
    half that would go missing. The outbound flag comes from the query rather
    than being re-derived from tx_loc downstream."""
    cols = ("dateDiff('second', time, now()) AS age_s, "
            "tx_sign, tx_loc, rx_sign, rx_loc, snr, power, distance")

    def half(home_side, dx_side, flag):
        return ("SELECT %s, %d AS outbound FROM wspr.rx "
                "WHERE time > subtractMinutes(now(), %d) AND band = %d "
                "AND %s AND substr(%s_loc,1,2)='%s' "
                "ORDER BY time DESC LIMIT %d"
                % (cols, flag, _WINDOW_MIN, band, _home_cond(home_side, home),
                   dx_side, field, _DETAIL_LIMIT))

    # outbound: we transmit (home=tx), they receive.  inbound: the reverse.
    return ("SELECT * FROM (%s) UNION ALL SELECT * FROM (%s)"
            % (half("tx", "rx", 1), half("rx", "tx", 0)))


def _fetch_detail(seq, tid, home, field, band):
    """Runs off-thread. Splits each spot into the DX end (in the target's field)
    and the home end (in our field), so the UI can show both directions:
    inbound  = a station in the target field heard BY our field ("hearing them")
    outbound = a station in our field heard IN the target field ("they hear us")."""
    try:
        if not field or (_HOME_RADIUS_KM <= 0 and not home):
            raise ValueError("home/target grid field not set")
        sql = _detail_query(home, field, band)
        url = _URL + "?query=" + urllib.parse.quote(sql + " FORMAT JSON")
        resp = requests.get(url, timeout=25)
        resp.raise_for_status()
        spots = []
        for r in resp.json().get("data", []):
            try:
                outbound = bool(int(r.get("outbound", 0)))
                spots.append({
                    "dx": r["rx_sign"] if outbound else r["tx_sign"],
                    "dx_loc": (r.get("rx_loc") if outbound else r.get("tx_loc")) or "",
                    "home_sign": r["tx_sign"] if outbound else r["rx_sign"],
                    "home_loc": (r.get("tx_loc") if outbound else r.get("rx_loc")) or "",
                    "outbound": outbound,
                    "snr": int(r["snr"]),
                    "dbm": int(r.get("power") or 0),
                    "km": int(r.get("distance") or 0),
                    "age_min": max(0.0, float(r["age_s"]) / 60.0),
                })
            except (KeyError, TypeError, ValueError):
                continue
        inbound = [s for s in spots if not s["outbound"]]
        outbound = [s for s in spots if s["outbound"]]
        payload = {"state": "ok", "spots": spots, "error": "",
                   "hearing": _group_by_station(inbound),
                   "heard_by": _group_by_station(outbound),
                   "n_in": len(inbound), "n_out": len(outbound),
                   # Each half is limited separately, so either can be partial.
                   "truncated": (len(inbound) >= _DETAIL_LIMIT
                                 or len(outbound) >= _DETAIL_LIMIT)}
    except Exception as e:
        payload = {"state": "error", "spots": [], "error": str(e),
                   "hearing": [], "heard_by": [], "n_in": 0, "n_out": 0,
                   "truncated": False}
        _log.warning("WSPR detail fetch failed (%s %s): %s", field, band_label(band), e)
    with _lock:
        # Drop the reply if the user has since clicked a different cell or closed it.
        if _detail_seq == seq and _detail is not None and _detail.get("key") == (tid, band):
            _detail.update(payload)


def _home_cond(side, home_field):
    """SQL predicate for "this end of the spot is at my QTH". side is tx or rx.
    A distance circle when WSPR_HOME_RADIUS_KM > 0, else the old field match."""
    if _HOME_RADIUS_KM > 0:
        return ("greatCircleDistance(%.6f, %.6f, %s_lon, %s_lat) < %d"
                % (_HOME_LON, _HOME_LAT, side, side, int(_HOME_RADIUS_KM * 1000)))
    return "substr(%s_loc,1,2)='%s'" % (side, home_field)


def home_scope_label():
    """Short human description of the home end, for the UI."""
    return ("%d km" % int(_HOME_RADIUS_KM)) if _HOME_RADIUS_KM > 0 \
        else "%s field" % (_home_field or "??")


def _build_query(home_field, fields):
    inlist = ",".join("'%s'" % f for f in sorted(set(fields)))
    tx_home = _home_cond("tx", home_field)
    rx_home = _home_cond("rx", home_field)
    # "other" is whichever end is NOT us, so the caller can group by target field.
    return (
        "SELECT band, "
        "if(%s, substr(rx_loc,1,2), substr(tx_loc,1,2)) AS other, "
        "count() AS cnt, "
        "greatest(dateDiff('second', max(time), now()), 0) AS age_s, "
        "max(snr) AS best_snr "
        "FROM wspr.rx "
        "WHERE time > subtractMinutes(now(), %d) "
        "AND ((%s AND substr(rx_loc,1,2) IN (%s)) "
        "OR (%s AND substr(tx_loc,1,2) IN (%s))) "
        "GROUP BY band, other"
        % (tx_home, _WINDOW_MIN, tx_home, inlist, rx_home, inlist)
    )


def modes():
    return list(_MODE_THRESHOLDS.keys())


def get_mode():
    return _mode


def set_mode(name):
    """Select the target mode (FT8/CW/SSB...). Triggers a prompt re-poll so the
    dots re-classify against the new thresholds within a second or two."""
    global _mode
    if name in _MODE_THRESHOLDS:
        _mode = name
        _wake.set()


def _classify(cnt, age_min, best_snr):
    """green/yellow/weak/none for the current mode, from spot count, freshness,
    and best SNR. 'weak' = path heard but the SNR is too low for this mode;
    'none' = nothing heard on the band in the window."""
    if cnt <= 0:
        return "none"
    green_snr, yellow_snr = _MODE_THRESHOLDS.get(_mode, (-12, -20))
    snr = best_snr if isinstance(best_snr, (int, float)) else -999.0
    if snr >= green_snr and age_min <= _GREEN_AGE_MIN and cnt >= _GREEN_MIN:
        return "green"
    if snr >= yellow_snr:
        return "yellow"
    return "weak"


def _poll_once():
    global _seen_first_poll, _poll_count
    with _lock:
        home = _home_field
        targets = [dict(t) for t in _targets]
    if not home or not targets:
        with _lock:
            _status.clear()
        if not targets:
            _prev_summary.clear()
            _last_green_at.clear()
            _last_active_poll.clear()
        applog.set_status("wspr", "ok", "no targets")
        return

    fields = [t["field"] for t in targets]
    sql = _build_query(home, fields)
    url = _URL + "?query=" + urllib.parse.quote(sql + " FORMAT JSON")
    resp = requests.get(url, timeout=25)
    resp.raise_for_status()
    rows = resp.json().get("data", [])

    # Index rows by (other_field, band) -> (cnt, age_min, best_snr).
    by_field = {}
    for r in rows:
        try:
            other = r["other"]
            band = int(r["band"])
            cnt = int(r["cnt"])
            age_min = float(r["age_s"]) / 60.0
            snr = r.get("best_snr")
        except (KeyError, TypeError, ValueError):
            continue
        by_field.setdefault(other, {})[band] = (cnt, age_min, snr)

    now_m = time.monotonic()
    new_status = {}
    openings = []
    for t in targets:
        tid, fld = t["id"], t["field"]
        seen = by_field.get(fld, {})
        bands = []
        rank = {"green": 3, "yellow": 2, "weak": 1, "none": 0}
        best = "none"
        for band in _BANDS:
            cnt, age_min, snr = seen.get(band, (0, None, None))
            state = _classify(cnt, age_min if age_min is not None else 1e9, snr)
            # For display, both "weak" (too weak for the mode) and "none" read as
            # red; the bubble still shows the spot count/SNR when there are spots.
            disp = "red" if state in ("weak", "none") else state
            bands.append({
                "band": band, "label": band_label(band), "state": disp,
                "cnt": cnt, "age_min": age_min, "best_snr": snr,
            })
            if rank[state] > rank[best]:
                best = state

        # Map-dot summary. green/yellow = likely for this mode. "weak" (heard but
        # under the mode's SNR bar) shows red. Nothing heard shows red only if the
        # path was active within the last _RECENT_CLOSE_POLLS polls, else quiet.
        if best == "green":
            summary = "green"
        elif best == "yellow":
            summary = "yellow"
        elif best == "weak":
            summary = "red"
        else:  # none heard on any band
            last = _last_active_poll.get(tid)
            recently_closed = last is not None and (_poll_count - last) <= _RECENT_CLOSE_POLLS
            summary = "red" if recently_closed else "quiet"
        if best in ("green", "yellow", "weak"):
            _last_active_poll[tid] = _poll_count

        # Opening = the DOT turning green: an edge from any non-green summary to
        # green. A target seen for the first time (including one just dropped on
        # the map) has no previous state, so an already-open path announces
        # itself once. Never fires on the very first poll of the session.
        prev = _prev_summary.get(tid)
        if summary == "green" and prev != "green" and _seen_first_poll:
            last = _last_green_at.get(tid, 0.0)
            if now_m - last >= _ALERT_COOLDOWN_S:
                _last_green_at[tid] = now_m
                greens = [b["label"] for b in bands if b["state"] == "green"]
                openings.append({
                    "target_id": tid, "label": t.get("label", fld),
                    "field": fld, "bands": greens,
                    "band_label": ", ".join(greens) or "?",
                })
        _prev_summary[tid] = summary

        new_status[tid] = {"summary": summary, "bands": bands}

    # Forget recency for targets that no longer exist.
    live_ids = {t["id"] for t in targets}
    for tid in list(_last_active_poll):
        if tid not in live_ids:
            _last_active_poll.pop(tid, None)
    for tid in list(_prev_summary):
        if tid not in live_ids:
            _prev_summary.pop(tid, None)
            _last_green_at.pop(tid, None)

    with _lock:
        _status.clear()
        _status.update(new_status)
        _openings.extend(openings)
    _seen_first_poll = True
    _poll_count += 1
    open_ct = sum(1 for v in new_status.values() if v["summary"] == "green")
    applog.set_status("wspr", "ok", f"{open_ct}/{len(targets)} open")


def _run():
    while True:
        try:
            _poll_once()
        except Exception as e:
            applog.set_status("wspr", "failed", str(e))
            _log.warning("WSPR poll failed: %s", e)
        # Wait out the poll interval, but wake early if the targets change.
        _wake.wait(_POLL_S)
        _wake.clear()
