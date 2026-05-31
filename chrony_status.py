"""Background poller for local chrony (NTP) status.

The piMap header clock bubbles out chrony's tracking + reference-clock numbers
on hover. chronyc talks to chronyd over a local socket; a call is cheap but
still tens of milliseconds, so we poll it on a daemon thread and let the render
loop read the latest cached snapshot without ever blocking a frame.

If chronyc is missing or chronyd isn't answering, the snapshot carries
available=False plus a human reason, so the UI can say so plainly.
"""

import shutil
import subprocess
import threading
import time

POLL_INTERVAL_S = 5.0

_lock = threading.Lock()
_snapshot = {"available": False, "reason": "starting…", "ts": 0.0}
_started = False


def _run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=4)


def _f(parts, i):
    try:
        return float(parts[i])
    except (ValueError, IndexError):
        return None


def _i(parts, i):
    try:
        return int(parts[i])
    except (ValueError, IndexError):
        return None


def _poll_once():
    out = {"available": False, "ts": time.time()}

    chronyc = shutil.which("chronyc")
    if not chronyc:
        out["reason"] = "chrony not installed on this host"
        return out

    try:
        tr = _run([chronyc, "-c", "tracking"])
    except (subprocess.TimeoutExpired, OSError):
        out["reason"] = "chronyd not responding"
        return out
    if tr.returncode != 0 or not tr.stdout.strip():
        out["reason"] = "chronyd not running"
        return out

    f = tr.stdout.strip().split(",")
    if len(f) < 14:
        out["reason"] = "unexpected chronyc output"
        return out

    out["tracking"] = {
        "ref_name": f[1],
        "stratum": _i(f, 2),
        "last_offset": _f(f, 5),
        "rms_offset": _f(f, 6),
        "frequency": _f(f, 7),
        "skew": _f(f, 9),
        "root_delay": _f(f, 10),
        "root_dispersion": _f(f, 11),
        "update_interval": _f(f, 12),
        "leap": f[13],
    }

    sources = []
    try:
        sr = _run([chronyc, "-c", "sources"])
        if sr.returncode == 0:
            for line in sr.stdout.strip().splitlines():
                c = line.split(",")
                if len(c) < 10:
                    continue
                sources.append({
                    "mode": c[0],     # ^ server  = peer  # refclock
                    "state": c[1],    # * + - ? x ~
                    "name": c[2],
                    "reach": c[5],    # octal; "377" == full
                    "offset": _f(c, 7),
                    "error": _f(c, 9),
                })
    except (subprocess.TimeoutExpired, OSError):
        pass
    out["sources"] = sources
    out["available"] = True
    return out


def _loop():
    while True:
        try:
            snap = _poll_once()
        except Exception as e:  # never let the poller thread die
            snap = {"available": False, "reason": f"poll error: {e}", "ts": time.time()}
        with _lock:
            global _snapshot
            _snapshot = snap
        time.sleep(POLL_INTERVAL_S)


def start():
    """Start the background poller once; safe to call repeatedly."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="chrony-poll", daemon=True).start()


def get():
    """Return the latest snapshot dict (always non-None)."""
    with _lock:
        return _snapshot
