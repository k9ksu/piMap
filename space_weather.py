"""Space-weather data from HamQSL (N0NBH), refreshed in the background.

`get_space_weather()` returns a snapshot dict immediately (never blocks the
render loop). The first call kicks off a background fetch; subsequent calls
get real values as soon as the fetch completes. Stale snapshots trigger a
refresh in the background, so the UI never pauses on the network.
"""

import threading
import time
import xml.etree.ElementTree as ET

import requests

import applog


_URL = "https://www.hamqsl.com/solarxml.php"
_TTL_S = 600.0  # refresh every 10 minutes

_log = applog.get_logger("swx")
applog.set_status("swx", "loading", "")

_lock = threading.Lock()
_cache = {
    "SFI": "?",
    "Kp": "?",
    "A": "?",
    "Sunspots": "?",
    "Geomag": "Unknown",
    # HamQSL <calculatedconditions>: {band_name: {"day": rating, "night": rating}}
    # e.g. {"80m-40m": {"day": "Fair", "night": "Good"}, ...}
    "Bands": {},
    "_fetched_at": 0.0,
}
_refresh_in_progress = False


def _parse(xml_text):
    root = ET.fromstring(xml_text)
    sd = root.find("solardata")
    if sd is None:
        raise RuntimeError("HamQSL response missing <solardata>")

    def text_of(tag):
        el = sd.find(tag)
        return el.text.strip() if (el is not None and el.text) else ""

    # Calculated HF band conditions: one <band name=".." time="day|night">Rating</band>
    # per band group. Nest them by band -> time so the UI can show the set that
    # matches whether the QTH is currently in daylight.
    bands = {}
    cc = sd.find("calculatedconditions")
    if cc is not None:
        for b in cc.findall("band"):
            name = (b.get("name") or "").strip()
            when = (b.get("time") or "").strip().lower()
            rating = (b.text or "").strip()
            if name and when:
                bands.setdefault(name, {})[when] = rating

    return {
        "SFI": text_of("solarflux"),
        "Kp": text_of("kindex"),
        "A": text_of("aindex"),
        "Sunspots": text_of("sunspots"),
        "Geomag": text_of("geomagfield").title() or "Unknown",
        "Bands": bands,
    }


def _refresh():
    global _refresh_in_progress
    try:
        resp = requests.get(_URL, timeout=10)
        resp.raise_for_status()
        data = _parse(resp.text)
        data["_fetched_at"] = time.time()
        with _lock:
            _cache.update(data)
        applog.set_status("swx", "ok", f"SFI {data['SFI']} Kp {data['Kp']}")
        _log.info("Space weather: SFI %s Kp %s A %s Sunspots %s",
                  data["SFI"], data["Kp"], data["A"], data["Sunspots"])
    except Exception as e:
        applog.set_status("swx", "failed", str(e))
        _log.warning("Space weather fetch failed: %s", e)
    finally:
        with _lock:
            _refresh_in_progress = False


def get_space_weather():
    global _refresh_in_progress
    now = time.time()
    with _lock:
        stale = (now - _cache["_fetched_at"]) > _TTL_S
        spawn = stale and not _refresh_in_progress
        if spawn:
            _refresh_in_progress = True
        snapshot = dict(_cache)
    if spawn:
        threading.Thread(target=_refresh, daemon=True).start()
    return snapshot
