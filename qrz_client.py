# qrz_client.py

import calendar
import html
import logging
import time

import requests

import config
from adif_parser import parse_adif
from maidenhead import maidenhead_to_latlon

_log = logging.getLogger("qrz")


def _newest_n(records, n=None):
    """Return the newest N records. QRZ returns the log oldest-first with no
    sort option, so order by QRZ logid (monotonic, newest = largest), falling
    back to QSO_DATE+TIME_ON when a logid is missing."""
    if n is None:
        n = getattr(config, "QRZ_MAX_QSOS", 25)

    def key(r):
        lid = r.get("APP_QRZLOG_LOGID") or ""
        lid = int(lid) if str(lid).isdigit() else -1
        return (lid, (r.get("QSO_DATE") or "") + (r.get("TIME_ON") or ""))

    return sorted(records, key=key, reverse=True)[:n]


def _extract_qrz_field(raw, key):
    """Pull a value from QRZ's '&'-delimited KEY=VALUE response (e.g. REASON,
    STATUS). Returns the value string, or '' if absent."""
    for part in raw.replace("\n", "&").split("&"):
        if part.startswith(key + "="):
            return part[len(key) + 1:].strip()
    return ""


def _qso_epoch(rec):
    """UTC epoch seconds for a QSO from ADIF QSO_DATE (YYYYMMDD) + TIME_ON
    (HHMM or HHMMSS). Returns None if the date/time can't be parsed."""
    d = (rec.get("QSO_DATE") or "").strip()
    t = (rec.get("TIME_ON") or "").strip()
    if len(d) != 8 or not d.isdigit():
        return None
    if len(t) not in (4, 6) or not t.isdigit():
        return None
    try:
        tm = (int(d[0:4]), int(d[4:6]), int(d[6:8]),
              int(t[0:2]), int(t[2:4]), int(t[4:6]) if len(t) == 6 else 0,
              0, 0, 0)
        return calendar.timegm(tm)  # QRZ logbook times are UTC
    except ValueError:
        return None


# Cache so steady-state polls cost only a ~170-byte STATUS call: QSO data is
# re-fetched only when the logbook's QSO COUNT changes.
_cache = {"count": None, "records": []}


def _api_post(action, option=None, timeout=15):
    """POST to the QRZ logbook API; return (raw_text, err)."""
    if not config.QRZ_ENABLE:
        return "", "QRZ disabled in config"
    api_key = (config.QRZ_API_KEY or "").strip()
    if not api_key or "YOUR_QRZ_LOGBOOK_API_KEY_HERE" in api_key:
        return "", "QRZ API key not set"
    data = {"KEY": api_key, "ACTION": action}
    if option is not None:
        data["OPTION"] = option
    hdr = {"User-Agent": f"PiMap/1.0 ({config.QRZ_USER_AGENT_CALLSIGN})"}
    try:
        resp = requests.post("https://logbook.qrz.com/api", data=data,
                             headers=hdr, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.Timeout as e:
        m = f"HTTP timeout after {timeout}s: {e}"
        _log.warning("QRZ API: %s", m)
        return "", m
    except requests.exceptions.HTTPError as e:
        m = f"HTTP {getattr(e.response, 'status_code', '?')}: {e}"
        _log.warning("QRZ API: %s", m)
        return "", m
    except requests.exceptions.RequestException as e:
        m = f"HTTP error: {e}"
        _log.warning("QRZ API: %s", m)
        return "", m
    return resp.text.strip(), ""


def _status_count():
    """Return (count:int|None, err). Cheap (~170 B) change detector via STATUS."""
    raw, err = _api_post("STATUS")
    if err:
        return None, err
    if "RESULT=OK" not in raw:
        reason = _extract_qrz_field(raw, "REASON") or "QRZ STATUS not OK"
        _log.warning("QRZ API: %s", reason)
        return None, reason
    c = _extract_qrz_field(raw, "COUNT")
    return (int(c) if c.isdigit() else None), ""


def _afterlogid_floor(n):
    """Logid floor that yields ~the newest n records via AFTERLOGID, found with
    a cheap TYPE:LOGIDS fetch (~10-20 KB, ids only). None on failure."""
    raw, err = _api_post("FETCH", "TYPE:LOGIDS")
    if err or "RESULT=OK" not in raw:
        return None
    ids = [int(x) for x in _extract_qrz_field(raw, "LOGIDS").split(",")
           if x.isdigit()]
    if not ids:
        return None
    ids.sort()
    return ids[-n] if len(ids) >= n else ids[0]


def fetch_recent_qsos():
    """Return (records, err): the newest QRZ_MAX_QSOS QSOs.

    Courteous, multi-tier fetch:
      1. STATUS (~170 B) every call -> detect change via QSO COUNT.
      2. Only when COUNT changes, TYPE:LOGIDS (~KB) to find the recent-tail
         logid floor.
      3. AFTERLOGID:<floor> to pull just that tail as ADIF (not the whole book).
    Falls back to cached records on a transient error, and to a full fetch if
    the logid path fails."""
    n = getattr(config, "QRZ_MAX_QSOS", 25)

    count, err = _status_count()
    if err:
        if _cache["records"]:
            return _cache["records"], ""   # keep last good data on a blip
        return [], err

    if count == _cache["count"] and _cache["records"]:
        return _cache["records"], ""       # nothing new -> no heavy fetch

    floor = _afterlogid_floor(n)
    option = f"AFTERLOGID:{floor},TYPE:ADIF" if floor is not None else "TYPE:ADIF"
    recs, ferr = _fetch_adif(option)
    if ferr:
        if _cache["records"]:
            return _cache["records"], ""
        return [], ferr

    newest = _newest_n(recs, n)
    _cache["count"] = count
    _cache["records"] = newest
    return newest, ""


def _fetch_adif(option):
    """POST a FETCH with the given OPTION; return (records, err) of raw ADIF
    dicts (NOT sliced to newest-N)."""
    if not config.QRZ_ENABLE:
        return [], "QRZ disabled in config"

    api_key = (config.QRZ_API_KEY or "").strip()
    if not api_key or "YOUR_QRZ_LOGBOOK_API_KEY_HERE" in api_key:
        return [], "QRZ API key not set"

    data = {
        "KEY": api_key,
        "ACTION": "FETCH",
        "OPTION": option,
    }

    headers = {
        "User-Agent": f"PiMap/1.0 ({config.QRZ_USER_AGENT_CALLSIGN})"
    }

    try:
        resp = requests.post(
            "https://logbook.qrz.com/api",
            data=data,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout as e:
        msg = f"HTTP timeout after 15s: {e}"
        _log.warning("QRZ API: %s", msg)
        return [], msg
    except requests.exceptions.HTTPError as e:
        code = getattr(e.response, "status_code", "?")
        msg = f"HTTP {code}: {e}"
        _log.warning("QRZ API: %s", msg)
        return [], msg
    except requests.exceptions.RequestException as e:
        msg = f"HTTP error: {e}"
        _log.warning("QRZ API: %s", msg)
        return [], msg

    raw = resp.text.strip()

    # Uncomment if you ever want to see the full raw:
    # print("--- QRZ RAW ---\n", raw[:800], "\n---------------")

    # ----------------------------------------------------------
    # CASE 1: RESULT=... & ADIF=... (your current response)
    # ----------------------------------------------------------
    if "RESULT=" in raw and "ADIF=" in raw:
        # Basic check for OK result
        if "RESULT=OK" not in raw:
            reason = _extract_qrz_field(raw, "REASON") or _extract_qrz_field(raw, "RESULT")
            msg = f"QRZ non-OK result: {reason}" if reason else "QRZ returned non-OK result"
            _log.warning("QRZ API: %s", msg)
            return [], msg
        # Even on OK, QRZ may include a REASON warning (e.g. capped count).
        warn = _extract_qrz_field(raw, "REASON")
        if warn:
            _log.warning("QRZ API warning: %s", warn)

        # Take EVERYTHING after the first "ADIF=" as the ADIF blob
        idx = raw.find("ADIF=")
        adif_raw = raw[idx + len("ADIF="):]

        # QRZ escapes < and > as &lt; and &gt;
        adif_text = html.unescape(adif_raw)

        try:
            records = parse_adif(adif_text)
        except Exception as e:
            return [], f"ADIF parse error: {e}"

        if not records:
            return [], "No QSOs parsed from ADIF"

        return records, ""

    # ----------------------------------------------------------
    # CASE 2: Raw ADIF (no RESULT=, just <call:..> ... <eor>)
    # ----------------------------------------------------------
    if "<call:" in raw.lower() or "<eor" in raw.lower():
        adif_text = html.unescape(raw)
        try:
            records = parse_adif(adif_text)
        except Exception as e:
            return [], f"ADIF parse error: {e}"

        if not records:
            return [], "No QSOs parsed from ADIF"

        return records, ""

    # ----------------------------------------------------------
    # CASE 3: HTML / unknown
    # ----------------------------------------------------------
    if "<html" in raw.lower():
        print("\n=========== QRZ HTML ERROR ===========")
        print(raw[:1200])
        print("======================================\n")
        return [], "QRZ returned an HTML page / error"

    print("\n=========== RAW QRZ RESPONSE (unknown) ===========")
    print(raw[:1200])
    print("=================================================\n")
    return [], "Unrecognized QRZ response format"


def _fmt_qso_lines(rec):
    """Compact info lines for a QSO bubble from an ADIF record."""
    def g(k):
        return (rec.get(k) or "").strip()

    lines = []
    name = g("NAME").title()
    if name:
        lines.append(name)

    loc = ", ".join(b for b in (g("QTH").title(), g("STATE"), g("COUNTRY")) if b)
    if loc:
        lines.append(loc)

    bm = "  ".join(b for b in (g("BAND"), g("MODE")) if b)
    if g("FREQ"):
        bm = f"{bm}  {g('FREQ')} MHz".strip()
    if bm:
        lines.append(bm)

    if g("GRIDSQUARE"):
        lines.append(f"Grid {g('GRIDSQUARE')}")

    d, t = g("QSO_DATE"), g("TIME_ON")
    dt = f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""
    if len(t) >= 4:
        dt = f"{dt} {t[0:2]}:{t[2:4]}Z".strip()
    if dt:
        lines.append(dt)

    extras = []
    if g("RST_SENT") or g("RST_RCVD"):
        extras.append(f"RST {g('RST_SENT')}/{g('RST_RCVD')}")
    if g("DISTANCE"):
        extras.append(f"{g('DISTANCE')} km")
    if extras:
        lines.append("  ".join(extras))

    return lines


def make_qsos_from_qrz(records, home_latlon):
    """Return [{home, dx, title, lines}] for each QSO with a usable grid."""
    qsos = []
    if home_latlon is None:
        return qsos
    for rec in records:
        grid = rec.get("GRIDSQUARE") or rec.get("GRIDSQ") or rec.get("LOCATOR")
        if not grid:
            continue
        try:
            dx_lat, dx_lon = maidenhead_to_latlon(grid)
        except Exception:
            continue
        qsos.append({
            "home": home_latlon,
            "dx": (dx_lat, dx_lon),
            "title": (rec.get("CALL") or "?").strip().upper(),
            "lines": _fmt_qso_lines(rec),
            "ts": _qso_epoch(rec),  # UTC epoch, or None if unparseable
        })
    return qsos


def poll_interval(qsos, window_min, idle_s, active_min_s, now=None):
    """Adaptive QRZ poll cadence in seconds.

    When no session is active (newest QSO older than window, or no timestamps),
    return idle_s. During a session, ramp linearly from idle_s down to
    active_min_s as the most recent QSO gets fresher: a contact logged seconds
    ago polls at active_min_s; one near the window edge polls near idle_s. This
    spends fast requests only when you're truly mid-run.
    """
    if not window_min or window_min <= 0:
        return idle_s
    timed = [q for q in qsos if q.get("ts") is not None]
    if not timed:
        return idle_s
    if now is None:
        now = time.time()
    window_s = window_min * 60.0
    age = now - max(q["ts"] for q in timed)
    if age >= window_s:
        return idle_s  # session stale
    # frac: 0.0 just-logged -> 1.0 at window edge
    frac = max(0.0, min(1.0, age / window_s))
    return active_min_s + (idle_s - active_min_s) * frac


def select_session_qsos(qsos, window_min, now=None):
    """Pick which QSOs to display based on logging activity.

    If the most recent QSO is newer than `window_min`, return only the current
    logging session: the run of QSOs working backward from newest where each
    consecutive pair is within `window_min` of the other (stop at the first
    larger gap). Otherwise (session gone stale, or window disabled), return the
    input unchanged so the caller's last-N list is shown.

    Returns (display_qsos, session_active).
    """
    if not window_min or window_min <= 0:
        return qsos, False

    timed = [q for q in qsos if q.get("ts") is not None]
    if not timed:
        return qsos, False

    if now is None:
        now = time.time()
    window_s = window_min * 60.0

    newest = max(timed, key=lambda q: q["ts"])
    if now - newest["ts"] > window_s:
        return qsos, False  # stale -> fall back to last N

    # Walk newest -> oldest, keep while gaps stay within the window.
    ordered = sorted(timed, key=lambda q: q["ts"], reverse=True)
    session = [ordered[0]]
    for prev, cur in zip(ordered, ordered[1:]):
        if prev["ts"] - cur["ts"] > window_s:
            break
        session.append(cur)
    return session, True
