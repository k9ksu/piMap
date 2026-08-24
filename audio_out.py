"""System audio-output (the TV / HDMI-2 sink) volume control via wpctl.

piMap runs on HDMI-2, whose PipeWire sink is the vc4-hdmi-1 card. We resolve
that sink's numeric id by scanning `wpctl inspect`, then get/set its volume.
Sets are coalesced onto a worker thread so dragging the slider never blocks the
render loop; the newest requested value always wins.
"""

import json
import re
import subprocess
import threading
import time

import applog
import config

_CARD = getattr(config, "TV_AUDIO_CARD", "vc4-hdmi-1")  # HDMI-2
_MAX = float(getattr(config, "TV_VOLUME_MAX", 1.0))

_log = applog.get_logger("audio")
_lock = threading.Lock()
_sink_id = None
_tv_node = None         # HDMI-2 sink node.name (stable across reboots)
_cached = None          # (volume float 0..1, muted bool) or None
_pending = None         # latest requested volume awaiting the worker
_evt = threading.Event()
_started = False


def _wpctl(*args, timeout=4):
    return subprocess.run(["wpctl", *args], capture_output=True, text=True, timeout=timeout)


def _resolve_sink():
    global _sink_id, _tv_node
    try:
        st = _wpctl("status")
        if st.returncode != 0:
            return None
        ids, in_sinks = [], False
        for ln in st.stdout.splitlines():
            if "Sinks:" in ln:
                in_sinks = True
                continue
            if in_sinks and ("Sources:" in ln or "Filters:" in ln):
                break
            if in_sinks:
                m = re.search(r'(\d+)\.\s', ln)
                if m:
                    ids.append(int(m.group(1)))
        for sid in ids:
            insp = _wpctl("inspect", str(sid)).stdout
            if _CARD in insp:
                _sink_id = sid
                m = re.search(r'node\.name = "([^"]+)"', insp)
                if m:
                    _tv_node = m.group(1)
                _log.info("TV audio: HDMI-2 sink is id %d (%s) node=%s",
                          sid, _CARD, _tv_node)
                return sid
    except Exception as e:
        _log.warning("TV audio: sink resolve failed: %s", e)
    return None


def tv_node_name():
    if _tv_node is None:
        _resolve_sink()
    return _tv_node


def _find_stream_node(pid):
    """PipeWire node id of the given process's audio output stream, or None."""
    try:
        data = json.loads(subprocess.run(["pw-dump"], capture_output=True,
                                         text=True, timeout=5).stdout)
        for o in data:
            props = (o.get("info") or {}).get("props") or {}
            if (props.get("media.class") == "Stream/Output/Audio"
                    and str(props.get("application.process.id")) == str(pid)):
                return o.get("id")
    except Exception:
        pass
    return None


def route_pid_to_tv(pid):
    """Move a process's audio stream onto the HDMI-2 sink. The default sink is
    HDMI-1, and SDL's device selection is unreliable here, so we re-target the
    stream by name via pw-metadata. Retries because the stream node can appear a
    moment after the mixer initializes. Runs on a background thread."""
    def _r():
        node = tv_node_name()
        if not node:
            _log.warning("TV audio: no HDMI-2 node to route to")
            return
        for _ in range(24):
            nid = _find_stream_node(pid)
            if nid is not None:
                try:
                    subprocess.run(["pw-metadata", str(nid), "target.object", node],
                                   capture_output=True, text=True, timeout=4)
                    _log.info("TV audio: routed stream node %d -> HDMI-2 (%s)", nid, node)
                except Exception as e:
                    _log.warning("TV audio: route failed: %s", e)
                return
            time.sleep(0.25)
        _log.warning("TV audio: audio stream for pid %d not found to route", pid)
    threading.Thread(target=_r, daemon=True).start()


def _sink():
    global _sink_id
    if _sink_id is None:
        _resolve_sink()
    return _sink_id


def _read_volume():
    sid = _sink()
    if sid is None:
        return None
    try:
        out = _wpctl("get-volume", str(sid)).stdout
        vol = float(out.split()[1])
        return (vol, "MUTED" in out)
    except Exception as e:
        _log.warning("TV audio: get-volume failed: %s", e)
        return None


def _apply_volume(v):
    global _sink_id
    sid = _sink()
    if sid is None:
        return
    try:
        _wpctl("set-volume", str(sid), f"{v:.2f}")
    except Exception as e:
        _sink_id = None  # force re-resolve next time (id may have changed)
        _log.warning("TV audio: set-volume failed: %s", e)


def _worker():
    global _pending, _cached
    _cached = _read_volume()
    while True:
        _evt.wait()
        _evt.clear()
        with _lock:
            v = _pending
            _pending = None
        if v is not None:
            _apply_volume(v)
            with _lock:
                muted = _cached[1] if _cached else False
                _cached = (v, muted)


def start():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_worker, daemon=True).start()


def get_cached():
    """(volume 0..1, muted) or None if not read yet. Non-blocking."""
    with _lock:
        return _cached


def refresh():
    """Re-read the sink volume on the worker (call when the panel opens)."""
    def _r():
        global _cached
        v = _read_volume()
        if v is not None:
            with _lock:
                _cached = v
    threading.Thread(target=_r, daemon=True).start()


def set_volume(v):
    """Request a new TV volume (0..1). Coalesced; newest wins. Non-blocking."""
    global _pending, _cached
    v = max(0.0, min(_MAX, float(v)))
    with _lock:
        _pending = v
        if _cached is not None:
            _cached = (v, _cached[1])  # optimistic UI update
        else:
            _cached = (v, False)
    _evt.set()


def toggle_mute():
    def _t():
        global _cached
        sid = _sink()
        if sid is None:
            return
        try:
            _wpctl("set-mute", str(sid), "toggle")
        except Exception as e:
            _log.warning("TV audio: mute toggle failed: %s", e)
        v = _read_volume()
        if v is not None:
            with _lock:
                _cached = v
    threading.Thread(target=_t, daemon=True).start()
