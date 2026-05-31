"""Background poller for u-blox GNSS satellite status, for the header bubble.

Same pattern as chrony_status: poll on a daemon thread, cache the snapshot,
the render loop just reads it. Data comes from `ubxtool -p NAV-SAT` (which
also carries NAV-PVT fixType/numSV and NAV-DOP in gpsd's periodic stream).
"""

import re
import shutil
import subprocess
import threading
import time

POLL_INTERVAL_S = 10.0

_GNSS = {0: "GPS", 1: "SBAS", 2: "GAL", 3: "BDS", 4: "IMES", 5: "QZSS", 6: "GLO"}
_FIX = {0: "no fix", 1: "DR", 2: "2D", 3: "3D", 4: "GNSS+DR", 5: "time"}

_SAT_RE = re.compile(r"gnssId\s+(\d+).*?cno\s+(\d+).*?flags\s+x([0-9a-fA-F]+)")

_lock = threading.Lock()
_snapshot = {"available": False, "reason": "starting…", "ts": 0.0}
_started = False


def _poll_once():
    out = {"available": False, "ts": time.time()}
    ubxtool = shutil.which("ubxtool")
    if not ubxtool:
        out["reason"] = "ubxtool not installed"
        return out
    try:
        r = subprocess.run([ubxtool, "-p", "NAV-SAT"],
                           capture_output=True, text=True, timeout=8)
    except (subprocess.TimeoutExpired, OSError):
        out["reason"] = "GPS not responding"
        return out

    text = r.stdout or ""
    sats = []
    for line in text.splitlines():
        if "gnssId" not in line or "cno" not in line:
            continue
        m = _SAT_RE.search(line)
        if m:
            sats.append((int(m.group(1)), int(m.group(2)), int(m.group(3), 16)))
    if not sats:
        out["reason"] = "no GNSS data" if "UBX-NAV-SAT" not in text else "no satellites"
        return out

    used = [(g, c) for g, c, f in sats if f & 0x08]
    per = {}
    for g, c, f in sats:
        lbl = _GNSS.get(g, f"id{g}")
        d = per.setdefault(lbl, {"used": 0, "seen": 0})
        d["seen"] += 1
        if f & 0x08:
            d["used"] += 1
    used_cno = sorted((c for _, c in used), reverse=True)

    g = {
        "tracked": len(sats),
        "with_signal": sum(1 for _, c, _f in sats if c > 0),
        "used": len(used),
        "per": per,
        "best_cno": used_cno[0] if used_cno else 0,
        "avg_cno": round(sum(used_cno) / len(used_cno)) if used_cno else 0,
    }
    mfix = re.search(r"fixType\s+(\d+)", text)
    if mfix:
        g["fix"] = _FIX.get(int(mfix.group(1)), mfix.group(1))
    mh = re.search(r"hDOP\s+(\d+)", text)
    if mh:
        g["hdop"] = int(mh.group(1)) / 100.0

    out["gnss"] = g
    out["available"] = True
    return out


def _loop():
    while True:
        try:
            snap = _poll_once()
        except Exception as e:
            snap = {"available": False, "reason": f"poll error: {e}", "ts": time.time()}
        with _lock:
            global _snapshot
            _snapshot = snap
        time.sleep(POLL_INTERVAL_S)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="gps-poll", daemon=True).start()


def get():
    with _lock:
        return _snapshot
