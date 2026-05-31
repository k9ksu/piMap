"""Amateur-satellite radio info (uplink/downlink/mode) from the SatNOGS DB.

One background fetch of the transmitter list, cached to disk (refreshed
weekly), indexed by NORAD id. info() is non-blocking: it reads the in-memory
index and returns "loading" until the background load finishes, so the render
loop never waits on the network.
"""

import json
import os
import threading
import time

import requests

import applog


_URL = "https://db.satnogs.org/api/transmitters/?format=json"
_CACHE = os.path.join(os.path.dirname(__file__), "data", "satnogs_tx_cache.json")
_REFRESH_S = 7 * 24 * 3600  # refresh weekly; frequencies rarely change

_log = applog.get_logger("freq")
_lock = threading.Lock()
_index = {}        # norad(int) -> [transmitter dict, ...]
_loaded_at = 0.0

_TYPE_ORDER = {"Transponder": 0, "Transceiver": 1, "Transmitter": 2}


def _build_index(txs):
    idx = {}
    for t in txs:
        n = t.get("norad_cat_id")
        if n:
            idx.setdefault(int(n), []).append(t)
    return idx


def _fetch():
    resp = requests.get(_URL, timeout=30)
    resp.raise_for_status()
    txs = resp.json()
    if not isinstance(txs, list) or not txs:
        raise RuntimeError("unexpected SatNOGS response")
    with open(_CACHE, "w") as f:
        json.dump(txs, f)
    return txs


def _load():
    global _index, _loaded_at
    if os.path.exists(_CACHE):
        age = time.time() - os.path.getmtime(_CACHE)
        if age < _REFRESH_S:
            try:
                with open(_CACHE) as f:
                    txs = json.load(f)
                _index = _build_index(txs)
                _loaded_at = time.time()
                applog.set_status("freq", "ok", f"{len(_index)} sats, cache {int(age/3600)}h")
                _log.info("Radio DB: %d sats from cache", len(_index))
                return
            except Exception:
                pass
    try:
        txs = _fetch()
        _index = _build_index(txs)
        _loaded_at = time.time()
        applog.set_status("freq", "ok", f"{len(_index)} sats, fresh")
        _log.info("Radio DB: fetched %d sats", len(_index))
    except Exception as e:
        if os.path.exists(_CACHE):
            try:
                with open(_CACHE) as f:
                    txs = json.load(f)
                _index = _build_index(txs)
                _loaded_at = time.time()
                applog.set_status("freq", "stale", str(e))
                _log.warning("Radio DB fetch failed (%s); using stale cache", e)
                return
            except Exception:
                pass
        applog.set_status("freq", "failed", str(e))
        _log.warning("Radio DB unavailable: %s", e)


def _fmt(t):
    dl, dh = t.get("downlink_low"), t.get("downlink_high")
    ul, uh = t.get("uplink_low"), t.get("uplink_high")
    parts = []
    if dl:
        parts.append(f"Dn {dl/1e6:.3f}" + (f"-{dh/1e6:.3f}" if dh and dh != dl else ""))
    if ul:
        parts.append(f"Up {ul/1e6:.3f}" + (f"-{uh/1e6:.3f}" if uh and uh != ul else ""))
    line = "  ".join(parts) if parts else (t.get("description") or "")
    mode = t.get("mode") or ""
    if mode:
        line += f"  {mode}"
    if t.get("invert"):
        line += " inv"
    return line.strip()


def info(norad, max_lines=5):
    """Return (lines, note). Non-blocking.

    lines: formatted active-transmitter strings (downlink/uplink/mode).
    note:  "" on success, else a short status ("loading...", "no active...").
    """
    if not _index:
        return [], "loading radio data..."
    txs = _index.get(int(norad), [])
    if not txs:
        return [], "no radio data"
    active = [t for t in txs if t.get("status") == "active"]
    if not active:
        return [], "no active transmitters"
    active.sort(key=lambda t: (_TYPE_ORDER.get(t.get("type"), 3),
                               t.get("downlink_low") or 0))
    lines = []
    seen = set()
    for t in active:
        ln = _fmt(t)
        if ln and ln not in seen:
            seen.add(ln)
            lines.append(ln)
        if len(lines) >= max_lines:
            break
    return lines, ""


def preload_async():
    applog.set_status("freq", "loading", "")

    def _run():
        with _lock:
            if not _index:
                _load()

    threading.Thread(target=_run, daemon=True).start()
