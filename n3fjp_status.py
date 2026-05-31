"""Optional N3FJP API listener: trigger an immediate QRZ refresh on log.

N3FJP's logging apps run a TCP API server (default port 1100). When the user
logs a contact, the server PUSHES an unsolicited message:

    <CMD><ENTEREVENT><QSOCOUNT>765</QSOCOUNT><CALL>LA9A</CALL>...</CMD>

We hold a connection open and watch for ENTEREVENT. Each one bumps an internal
counter; piMap polls trigger_count() and, when it changes, does an immediate
QRZ fetch — killing the cold-start delay before a new QSO appears on the map.

This is a courtesy accelerator, not a data source: QRZ remains the source of
truth. If N3FJP is off, unreachable, or the link drops, this stays quietly in
its reconnect loop and piMap's normal adaptive polling carries on unaffected.

MASTER SWITCH: controlled entirely by config.QRZ_N3FJP_TRIGGER_ENABLE. When
False (the default), start() is a no-op and no socket is ever opened.
"""

import logging
import re
import socket
import threading
import time

_log = logging.getLogger("n3fjp")

# unsolicited contact-logged push from N3FJP
_ENTEREVENT_RE = re.compile(r"<ENTEREVENT>", re.IGNORECASE)

_lock = threading.Lock()
_state = {
    "enabled": False,
    "connected": False,
    "trigger_count": 0,   # increments once per logged contact
    "last_call": None,
    "last_event_ts": 0.0,
    "reason": "disabled",
}
_started = False


def _set(**kw):
    with _lock:
        _state.update(kw)


def _loop(host, port):
    backoff = 2
    down_logged = False   # so a closed N3FJP logs once, not every retry
    while True:
        try:
            _set(reason=f"connecting to {host}:{port}")
            s = socket.create_connection((host, port), timeout=8)
            s.settimeout(30)
            _set(connected=True, reason="connected")
            _log.info("N3FJP: connected to %s:%s", host, port)
            down_logged = False
            backoff = 2
            buf = ""
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue  # idle is normal; keep waiting for a push
                if not chunk:
                    raise ConnectionError("server closed connection")
                buf += chunk.decode(errors="replace")
                # process complete <CMD>...</CMD> frames
                while "</CMD>" in buf:
                    frame, buf = buf.split("</CMD>", 1)
                    if _ENTEREVENT_RE.search(frame):
                        m = re.search(r"<CALL>([^<]*)</CALL>", frame, re.IGNORECASE)
                        call = (m.group(1).strip() if m else "") or None
                        with _lock:
                            _state["trigger_count"] += 1
                            _state["last_call"] = call
                            _state["last_event_ts"] = time.time()
                            n = _state["trigger_count"]
                        _log.info("N3FJP: contact logged (%s) -> QRZ refresh #%d",
                                  call or "?", n)
        except Exception as e:
            _set(connected=False, reason=f"{e}")
            # Log only the first failure of a down period; stay quiet on the
            # repeated retries while N3FJP remains closed/unreachable.
            if not down_logged:
                _log.warning("N3FJP: %s — will keep retrying quietly until it "
                             "reconnects", e)
                down_logged = True
            try:
                s.close()
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def start(host, port, enabled):
    """Start the listener thread, but ONLY if enabled. Safe to call repeatedly.

    When enabled is False this is a true no-op: no thread, no socket."""
    global _started
    if not enabled:
        _set(enabled=False, reason="disabled (QRZ_N3FJP_TRIGGER_ENABLE=False)")
        return
    if _started:
        return
    _started = True
    _set(enabled=True, reason="starting")
    threading.Thread(target=_loop, args=(host, int(port)),
                     name="n3fjp", daemon=True).start()


def trigger_count():
    """Monotonic count of contacts logged since start (0 if disabled/never)."""
    with _lock:
        return _state["trigger_count"]


def get():
    with _lock:
        return dict(_state)
