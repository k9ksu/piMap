"""piMap 2.0 server: piMap's data, served to an HTML5 map that draws itself.

The browser (web/) draws everything: the Blue Marble pyramid with smooth zoom, the grey
line, satellites propagated in the browser so they glide. This server keeps doing what
piMap's Python has always done well, through piMap's own modules, and hands the results
over as small JSON:

  GET  /                     the map
  GET  /api/config           callsign, colours, fonts, home, zoom limits, tile pyramid
  GET  /api/state            home, TV view, space weather, status tags (polled ~5 s)
  GET  /api/tles             the amateur TLE set (Celestrak, cached 6 h, as piMap)
  GET  /tiles/{z}/{x}_{y}.jpg   the pyramid (tools/build_pyramid.py), cached forever
  GET/POST /api/tv-view      the zoom the TV shows, centred on home

Home is HOME_LAT/HOME_LON from config.py, unless a GPS fix arrives: on FieldLab from
MQTT (PIMAP_MQTT_HOST, topic fieldlab/location). Everything else is the same
config.py piMap already uses.

On the FieldLab kiosk it also answers the kiosk's page contract (/_fieldlab/status,
/_fieldlab/meta), so the kiosk can show and supervise it like any other page.

    python3 -m pimap2.server            (PORT, default 8090)
"""

from __future__ import annotations

import json
import logging
import uuid
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .settings import ROOT, get, load_config

cfg, CONFIG_SOURCE = load_config()

import applog          # noqa: E402  (piMap's modules import config; it's registered above)
import geo_boundaries  # noqa: E402
import sat_radio       # noqa: E402
import n3fjp_status    # noqa: E402
import qrz_client      # noqa: E402
import metros          # noqa: E402
import wspr_monitor    # noqa: E402
import ft8_monitor     # noqa: E402
from maidenhead import latlon_to_field, latlon_to_maidenhead  # noqa: E402
import sat_tracker     # noqa: E402
import space_weather   # noqa: E402

log = logging.getLogger("pimap2")

VERSION = "2.0.0-phase1"
WEB = Path(ROOT) / "web"
PYRAMID = Path(ROOT) / "data" / "pyramid"
DATA_DIR = Path(os.environ.get("PIMAP_DATA_DIR", os.path.join(ROOT, "data")))
VIEW_FILE = DATA_DIR / "tv_view.json"
KIOSK = os.environ.get("PIMAP_KIOSK", "0") == "1"
PAGE_ID = os.environ.get("PIMAP_PAGE_ID", "pimap")          # the kiosk's name for this page
PAGE_TITLE = os.environ.get("PIMAP_PAGE_TITLE", "piMap")
GPS_MAX_AGE_S = 6 * 3600
# piMap's status row (overlays._STATUS_LABELS): component -> label. QRZ and WSPR aren't
# running in piMap 2.0 yet (phases 2 and 3): they show grey, "off", not amber.
STATUS_TAGS = {"tle": "TLE", "swx": "SWX", "qrz": "QRZ", "freq": "FRQ", "wspr": "WSP"}
NOT_YET: set = set()


# ---- home ---------------------------------------------------------------------------
class Home:
    """Where the map is centred and the QTH dot sits: config, or a GPS fix."""

    def __init__(self):
        self.lock = threading.Lock()
        self.config = (float(get(cfg, "HOME_LAT", 0.0)), float(get(cfg, "HOME_LON", 0.0)))
        self.gps: tuple[float, float] | None = None
        self.gps_t = 0.0

    def set_gps(self, lat: float, lon: float, ts: float | None = None) -> None:
        with self.lock:
            self.gps, self.gps_t = (round(lat, 4), round(lon, 4)), ts or time.time()

    def now(self) -> dict:
        with self.lock:
            if self.gps and time.time() - self.gps_t < GPS_MAX_AGE_S:
                lat, lon, src = *self.gps, "gps"
            else:
                lat, lon, src = *self.config, "config"
        return {"lat": lat, "lon": lon, "source": src, "tz": tz_name(lat, lon)}


_tz_cache: dict = {}


def tz_name(lat: float, lon: float) -> str | None:
    """The IANA zone at home, for the header clock (with DST). None without timezonefinder."""
    key = (round(lat, 1), round(lon, 1))
    if key not in _tz_cache:
        try:
            from timezonefinder import TimezoneFinder
            _tz_cache[key] = TimezoneFinder().timezone_at(lat=lat, lng=lon)
        except Exception:
            _tz_cache[key] = None
    return _tz_cache[key]


def parse_fix(payload: bytes) -> tuple[float, float, float] | None:
    """fieldlab/location JSON -> (lat, lon, ts), or None if it isn't a usable fix."""
    try:
        d = json.loads(payload) if payload else None
        lat, lon = float(d["lat"]), float(d["lon"])
        ts = float(d.get("ts") or time.time())
    except (ValueError, KeyError, TypeError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon, ts


home = Home()


def start_mqtt() -> None:
    host = os.environ.get("PIMAP_MQTT_HOST")
    if not host:
        return
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.warning("PIMAP_MQTT_HOST set but paho-mqtt isn't installed: no GPS home")
        return
    topic = os.environ.get("PIMAP_GPS_TOPIC", "fieldlab/location")
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pimap2")
    c.reconnect_delay_set(1, 30)
    c.on_connect = lambda client, *a: client.subscribe(topic, qos=1)

    def on_message(client, userdata, msg):
        fix = parse_fix(msg.payload)
        if fix:
            home.set_gps(*fix)
    c.on_message = on_message
    c.connect_async(host, int(os.environ.get("PIMAP_MQTT_PORT", "1883")), keepalive=30)
    c.loop_start()
    log.info("following GPS home on %s %s", host, topic)


# ---- the TV view --------------------------------------------------------------------
def zoom_limits() -> tuple[float, float]:
    return float(get(cfg, "MIN_ZOOM", 1.0)), float(get(cfg, "MAX_ZOOM", 8.0))


def load_tv_zoom() -> float:
    lo, hi = zoom_limits()
    try:
        z = float(json.loads(VIEW_FILE.read_text())["zoom"])
    except (OSError, ValueError, KeyError, TypeError):
        return lo
    return min(max(z, lo), hi)


def save_tv_zoom(z: float) -> float:
    lo, hi = zoom_limits()
    z = round(min(max(float(z), lo), hi), 3)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = VIEW_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"zoom": z}))
        tmp.replace(VIEW_FILE)
    except OSError as e:
        log.warning("can't save the TV view: %s", e)
    return z


TV_SETTINGS_FILE = DATA_DIR / "tv_settings.json"
TV_SETTING_KEYS = ("night", "borders", "qsos", "targets", "tracks", "footprints", "chime")


def load_tv_settings() -> dict:
    """What the TV draws (the left menu's TV column): all on unless saved otherwise."""
    out = {k: True for k in TV_SETTING_KEYS}
    out["chime"] = bool(get(cfg, "ALERT_SOUND_ENABLED", True))
    try:
        saved = json.loads(TV_SETTINGS_FILE.read_text())
        out.update({k: bool(saved[k]) for k in TV_SETTING_KEYS if k in saved})
    except (OSError, ValueError, TypeError):
        pass
    return out


def save_tv_settings(st: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = TV_SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(TV_SETTINGS_FILE)
    except OSError as e:
        log.warning("can't save the TV settings: %s", e)


tv = {"zoom": load_tv_zoom(), "settings": load_tv_settings(), "changed": time.time()}


# ---- background: keep piMap's sources fresh -----------------------------------------
class Worker:
    """Keeps the TLEs and space weather current, the way piMap's main loop did."""

    def __init__(self):
        self.beat_t: float | None = None
        self.tle_version = 0
        self._tle_count = 0
        self.started = time.time()

    def run(self) -> None:
        while True:
            try:
                n = len(sat_tracker.all_tles())
                if n != self._tle_count:
                    self._tle_count = n
                    self.tle_version += 1
                space_weather.get_space_weather()     # refreshes itself when stale
            except Exception:
                log.exception("refresh failed")
            self.beat_t = time.time()
            time.sleep(30)


worker = Worker()


# ---- satellites: which are shown, and when each next passes over home -----------------
SATS_FILE = DATA_DIR / "sats_shown.json"
ISS = 25544


class SatChoice:
    """The satellites drawn on the map: chosen from the right-edge panel (any browser),
    kept in data/sats_shown.json, so the TV and the shack screen show the same set."""

    def __init__(self):
        self.lock = threading.Lock()
        try:
            self.shown = [int(n) for n in json.loads(SATS_FILE.read_text())["shown"]]
        except (OSError, ValueError, KeyError, TypeError):
            self.shown = [int(n) for n in get(cfg, "SATS_SHOWN", [ISS])]
        self.version = 1

    def set(self, norad: int, on: bool) -> list[int]:
        with self.lock:
            s = [n for n in self.shown if n != norad]
            if on:
                s.append(norad)
            self.shown, self.version = s, self.version + 1
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                tmp = SATS_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps({"shown": s}))
                tmp.replace(SATS_FILE)
            except OSError as e:
                log.warning("can't save the satellite choice: %s", e)
            return list(s)


choice = SatChoice()


class Passes:
    """Next pass over home for every satellite (piMap's sat_tracker, every 2 minutes,
    for wherever home is now: it follows the trailer's GPS)."""

    def __init__(self):
        self.at = 0.0
        self.home: tuple[float, float] | None = None

    def run(self) -> None:
        while True:
            h = home.now()
            try:
                sat_tracker._compute_schedule(h["lat"], h["lon"])
                self.at, self.home = time.time(), (h["lat"], h["lon"])
            except Exception as e:
                log.warning("pass schedule failed: %s", e)
            time.sleep(120)

    def get(self) -> dict:
        return sat_tracker.get_schedule()[1]


passes = Passes()


class Qsos:
    """piMap's QRZ logbook loop (piMap.py main loop, same qrz_client): courteous polling
    (STATUS as change detector, only the newest records fetched), session mode (only
    the current run while you're logging, else the last QRZ_MAX_QSOS), an adaptive
    cadence that speeds up mid-session, and the optional N3FJP "just logged" trigger."""

    def __init__(self):
        self.lock = threading.Lock()
        self.all: list[dict] = []          # every recent QSO with a usable grid
        self.shown: list[dict] = []        # what's drawn: the session, or all
        self.session = False
        self.version = 0
        self.enabled = bool(get(cfg, "QRZ_ENABLE", False))

    def _refresh(self, home_ll) -> None:
        records, err = qrz_client.fetch_recent_qsos()
        if err:
            applog.set_status("qrz", "failed", err)
            log.warning("QRZ: %s", err)
            return
        qsos = qrz_client.make_qsos_from_qrz(records, home_ll)
        self._apply(qsos)

    def _apply(self, qsos: list[dict]) -> None:
        shown, session = qrz_client.select_session_qsos(qsos, get(cfg, "QRZ_SESSION_WINDOW_MIN", 0))
        with self.lock:
            changed = [(q["title"], q["ts"]) for q in shown] != [(q["title"], q["ts"]) for q in self.shown] \
                or session != self.session
            self.all, self.shown, self.session = qsos, shown, session
            if changed:
                self.version += 1
        applog.set_status("qrz", "ok", f"{len(shown)} QSOs ({'session' if session else 'recent'})")

    def run(self) -> None:
        if not self.enabled:
            applog.set_status("qrz", "off", "QRZ_ENABLE is off")
            return
        n3fjp_status.start(get(cfg, "QRZ_N3FJP_HOST", "127.0.0.1"), get(cfg, "QRZ_N3FJP_PORT", 1100),
                           get(cfg, "QRZ_N3FJP_TRIGGER_ENABLE", False))
        applog.set_status("qrz", "loading", "")
        window = get(cfg, "QRZ_SESSION_WINDOW_MIN", 0)
        idle_s = float(get(cfg, "QRZ_REFRESH_SECONDS", 150))
        floor_s = float(get(cfg, "QRZ_REFRESH_ACTIVE_MIN_SECONDS", idle_s))
        last, last_home, last_trigger = 0.0, None, n3fjp_status.trigger_count()
        while True:
            h = home.now()
            home_ll = (h["lat"], h["lon"])
            with self.lock:
                qsos = list(self.all)
            interval = qrz_client.poll_interval(qsos, window, idle_s, floor_s)
            ts = n3fjp_status.get().get("last_event_ts", 0.0)        # N3FJP: logging right now
            if window and ts and time.time() - ts < window * 60:
                frac = max(0.0, min(1.0, (time.time() - ts) / (window * 60)))
                interval = min(interval, floor_s + (idle_s - floor_s) * frac)
            trig = n3fjp_status.trigger_count()
            try:
                if time.time() - last >= interval or trig != last_trigger:
                    last_trigger = trig
                    self._refresh(home_ll)
                    last = time.time()
                elif home_ll != last_home and qsos:
                    # the trailer moved: redraw the paths from the new home, no refetch
                    self._apply([{**q, "home": home_ll} for q in qsos])
                else:
                    self._apply(qsos)          # a session ages out on time, not on a fetch
            except Exception:
                log.exception("QRZ loop")
                last = time.time() - idle_s + 30      # piMap: retry in 30 s
            last_home = home_ll
            time.sleep(2)

    def snapshot(self) -> dict:
        with self.lock:
            return {"version": self.version, "session": self.session,
                    "qsos": [{"home": q["home"], "dx": q["dx"], "title": q["title"],
                              "lines": q["lines"], "ts": q["ts"]} for q in self.shown]}


qsos_worker = Qsos()


# ---- target dots: WSPR (and FT8) propagation from home to dots on the map -------------
WSPR_ON = bool(get(cfg, "WSPR_TARGETS_ENABLED", True))
# FT8 streams PSKReporter all day: on the FieldLab kiosk (Starlink) it's off by
# default (PIMAP_FT8=0); at the shack it follows FT8_ENABLED as piMap always has.
FT8_ON = bool(get(cfg, "FT8_ENABLED", True)) and os.environ.get("PIMAP_FT8", "") != "0"
TARGETS_FILE = DATA_DIR / "targets.json"
PREFS_FILE = DATA_DIR / "prefs.json"
SOUNDS = ["Soft Chime", "Marimba", "Bell", "Music Box", "Ascending Triad"]   # piMap's alerts.py


def _read_json(*paths, default):
    for p in paths:
        try:
            return json.loads(Path(p).read_text())
        except (OSError, ValueError):
            continue
    return default


def _write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
    except OSError as e:
        log.warning("can't save %s: %s", path.name, e)


class Prefs:
    """piMap's ui_prefs: spot mode and alert sound, shared by every screen."""

    def __init__(self):
        p = _read_json(PREFS_FILE, Path(ROOT) / "ui_prefs.json", default={})
        modes = wspr_monitor.modes()
        self.spot_mode = p.get("spot_mode") if p.get("spot_mode") in modes else get(cfg, "WSPR_MODE", modes[0])
        self.alert_sound = p.get("alert_sound") if p.get("alert_sound") in SOUNDS else get(cfg, "ALERT_SOUND", SOUNDS[0])
        self.apply()

    def apply(self):
        wspr_monitor.set_mode(self.spot_mode)
        ft8_monitor.set_mode(self.spot_mode)

    def set(self, spot_mode=None, alert_sound=None) -> dict:
        if spot_mode in wspr_monitor.modes():
            self.spot_mode = spot_mode
        if alert_sound in SOUNDS:
            self.alert_sound = alert_sound
        self.apply()
        _write_json(PREFS_FILE, self.get())
        return self.get()

    def get(self) -> dict:
        return {"spot_mode": self.spot_mode, "alert_sound": self.alert_sound}


class Targets:
    """piMap's target dots, kept on the server (data/targets.json; a shack piMap's own
    targets.json is picked up the first time), so the TV and every browser share them."""

    def __init__(self):
        self.lock = threading.Lock()
        raw = _read_json(TARGETS_FILE, Path(ROOT) / "targets.json", default=[])
        self.list = []
        for t in raw if isinstance(raw, list) else []:
            try:
                self.list.append(self._make(float(t["lat"]), float(t["lon"]), t.get("id")))
            except (KeyError, TypeError, ValueError):
                continue
        self.version = 1
        self.openings: list[dict] = []          # recent openings, numbered, for every screen's chime
        self.openings_seq = 0
        self.home_key = None

    @staticmethod
    def _make(lat, lon, tid=None):
        lat, lon = max(-89.99, min(89.99, lat)), ((lon + 180) % 360) - 180
        return {"id": tid or uuid.uuid4().hex[:8], "lat": round(lat, 4), "lon": round(lon, 4),
                "field": latlon_to_field(lat, lon), "label": latlon_to_maidenhead(lat, lon, 4)}

    def _changed(self):
        self.version += 1
        _write_json(TARGETS_FILE, self.list)
        self.configure()

    def configure(self):
        h = home.now()
        with self.lock:
            targets = [dict(t) for t in self.list]
        if WSPR_ON:
            wspr_monitor.set_home(h["lat"], h["lon"])
            wspr_monitor.configure(latlon_to_field(h["lat"], h["lon"]), targets)
        if FT8_ON:
            ft8_monitor.configure(targets)

    def add(self, lat, lon) -> dict:
        t = self._make(lat, lon)
        with self.lock:
            self.list.append(t)
        self._changed()
        return t

    def move(self, tid, lat, lon) -> bool:
        with self.lock:
            i = next((i for i, t in enumerate(self.list) if t["id"] == tid), None)
            if i is None:
                return False
            self.list[i] = self._make(lat, lon, tid)
        self._changed()
        return True

    def remove(self, tid) -> bool:
        with self.lock:
            n = len(self.list)
            self.list = [t for t in self.list if t["id"] != tid]
            gone = len(self.list) != n
        if gone:
            self._changed()
        return gone

    def run(self) -> None:
        """Start the monitors, follow home, and collect openings for the chime."""
        if not WSPR_ON:
            applog.set_status("wspr", "off", "WSPR_TARGETS_ENABLED is off")
            return
        metros.start()                                   # city names for the bubbles
        self.configure()
        while True:
            h = home.now()
            key = (h["lat"], h["lon"])
            if key != self.home_key:
                self.home_key = key
                self.configure()
            for o in wspr_monitor.pop_openings():
                self.openings_seq += 1
                self.openings = (self.openings + [{**o, "seq": self.openings_seq, "t": time.time()}])[-20:]
            time.sleep(2)

    def snapshot(self) -> dict:
        with self.lock:
            targets = [dict(t) for t in self.list]
        fields = sorted({t["field"] for t in targets})
        return {"version": self.version, "targets": targets,
                "wspr": wspr_monitor.get_status() if WSPR_ON else {},
                "ft8": ft8_monitor.get_status(fields) if (FT8_ON and fields) else {},
                "metros": {f: metros.top_metros_in_field(f) for f in fields},
                "wspr_on": WSPR_ON, "ft8_on": FT8_ON,
                "scope": {"wspr": wspr_monitor.home_scope_label(), "ft8": ft8_monitor.home_scope_label()},
                "band_labels": {str(b): wspr_monitor.band_label(b) for b in wspr_monitor._BANDS}}

    def find(self, tid):
        with self.lock:
            return next((dict(t) for t in self.list if t["id"] == tid), None)


prefs = Prefs()
targets = Targets()


def status_tags() -> dict:
    st = applog.get_status()
    out = {}
    for k, label in STATUS_TAGS.items():
        state, detail = st.get(k, ("off" if k in NOT_YET else "loading", ""))
        out[k] = {"label": label, "state": state, "detail": detail}
    return out


def space_wx() -> dict:
    sw = space_weather.get_space_weather()
    return {k: v for k, v in sw.items() if not k.startswith("_")}


def public_config() -> dict:
    lo, hi = zoom_limits()
    try:
        pyramid = json.loads((PYRAMID / "pyramid.json").read_text())
    except (OSError, ValueError):
        pyramid = None
    c = lambda n, d: get(cfg, n, d)
    return {
        "version": VERSION,
        "settings": CONFIG_SOURCE,
        "callsign": c("QRZ_USER_AGENT_CALLSIGN", "piMap"),
        "header": {"font": c("HEADER_FONT", "AnonymousPro"), "fill": c("HEADER_FILL", 0.5),
                   "color": c("HEADER_COLOR", "#512888"),
                   "bar_fraction": c("SPACE_BAR_HEIGHT", 90) / 1080.0,
                   "band_conditions": c("BAND_CONDITIONS_ENABLED", True)},
        "colors": {"qth": c("QTH_COLOR", "#00FF00"), "qso": c("QSO_COLOR", "#FFC800")},
        "qso": {"dot_radius": c("QSO_DOT_RADIUS", 4), "logbook": c("QRZ_LOGBOOK_NAME", ""),
                "enabled": bool(c("QRZ_ENABLE", False))},
        "map": {"brightness": c("MAP_BRIGHTNESS", 1.15), "greyline": c("GREYLINE_ENABLED", True),
                "night_alpha": c("NIGHT_ALPHA", 75), "min_zoom": lo, "max_zoom": hi,
                "zoom_step": c("ZOOM_STEP", 1.15)},
        "iss": {"width": c("ISS_ICON_WIDTH_PX", 62), "halo_px": c("ISS_ICON_HALO_PX", 0.25),
                "halo_alpha": c("ISS_ICON_HALO_ALPHA", 0.1), "halo_color": c("ISS_ICON_HALO_COLOR", "#FFFFFF"),
                "shadow": [c("ISS_ICON_SHADOW_X", -7.5), c("ISS_ICON_SHADOW_Y", 7.5)],
                "shadow_alpha": c("ISS_ICON_SHADOW_ALPHA", 0.1), "shadow_gray": c("ISS_ICON_SHADOW_GRAY", 0.5)},
        "sats": {"dot_radius": c("SAT_DOT_RADIUS", 6), "show": list(c("SATS_SHOWN", [25544])),
                 "footprint": bool(c("SAT_FOOTPRINT", True))},
        "boundaries": {"enabled": bool(c("BOUNDARY_ENABLE", True)), "min_zoom": c("BOUNDARY_MIN_ZOOM", 2.5),
                       "color": c("BOUNDARY_COLOR", "#8FA3B8"), "alpha": c("BOUNDARY_ALPHA", 0.55)},
        "pyramid": pyramid,
        "targets": {"wspr_on": WSPR_ON, "ft8_on": FT8_ON, "modes": wspr_monitor.modes(), "sounds": SOUNDS,
                    "radius": c("TARGET_DOT_RADIUS", 7), "ping_s": c("WSPR_ALERT_HIGHLIGHT_SECONDS", 60),
                    "volume": c("ALERT_VOLUME", 0.8), "sound_on": bool(c("ALERT_SOUND_ENABLED", True))},
        "kiosk": KIOSK,                 # the FieldLab kiosk: browsers get the TV view panel
    }


_boundaries: bytes | None = None


def boundaries_json() -> bytes:
    """Country, state/province and coastline lines, as piMap draws them (geo_boundaries,
    same settings), flattened to [lon, lat, lon, lat, ...] per line. Built once."""
    global _boundaries
    if _boundaries is None:
        lines = []
        if get(cfg, "BOUNDARY_ENABLE", True):
            for pts, _bbox in geo_boundaries.load(
                    state_countries=tuple(get(cfg, "BOUNDARY_STATES_COUNTRIES", ["USA"])),
                    smooth_iters=int(get(cfg, "BOUNDARY_SMOOTH", 0)),
                    coastline=bool(get(cfg, "BOUNDARY_COASTLINE", True))):
                lines.append([round(v, 4) for lat, lon in pts for v in (lon, lat)])
        _boundaries = json.dumps({"lines": lines}, separators=(",", ":")).encode()
    return _boundaries


# ---- the app ------------------------------------------------------------------------
app = FastAPI(title="piMap 2.0", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2048)
NO_STORE = {"Cache-Control": "no-store"}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def api_config():
    return JSONResponse(public_config(), headers=NO_STORE)


@app.get("/api/state")
def api_state():
    return JSONResponse({"home": home.now(),
                         "tv": {"zoom": tv["zoom"], "settings": tv["settings"], "changed": tv["changed"]},
                         "space_weather": space_wx(), "status": status_tags(),
                         "tle_version": worker.tle_version,
                         "sats": {"shown": choice.shown, "version": choice.version},
                         "qso_version": qsos_worker.version,
                         "targets": {"version": targets.version, "openings_seq": targets.openings_seq},
                         "prefs": prefs.get(), "now": time.time()},
                        headers=NO_STORE)


@app.get("/api/tles")
def api_tles():
    tles = sat_tracker.all_tles()
    return JSONResponse({"version": worker.tle_version,
                         "sats": [{"norad": n, "name": t[0], "l1": t[1], "l2": t[2]}
                                  for n, t in sorted(tles.items())]},
                        headers={"Cache-Control": "no-cache"})


@app.get("/api/boundaries")
def api_boundaries():
    return Response(boundaries_json(), media_type="application/json",
                    headers={"Cache-Control": "no-cache"})


@app.get("/api/sats")
def api_sats():
    """Every satellite with its next pass over home, ISS first, then soonest pass."""
    tles = sat_tracker.all_tles()
    nxt = passes.get()
    rows = [{"norad": n, "name": t[0], "next_pass": nxt.get(n)} for n, t in tles.items()]
    rows.sort(key=lambda r: (r["norad"] != ISS, r["next_pass"] is None, r["next_pass"] or 0, r["name"]))
    return JSONResponse({"sats": rows, "shown": choice.shown, "passes_at": passes.at,
                         "min_elevation": float(get(cfg, "PASS_MIN_ELEVATION", 10.0))},
                        headers=NO_STORE)


@app.get("/api/qsos")
def api_qsos():
    """The QSOs piMap draws: home, DX (from the grid), callsign, bubble lines, time."""
    return JSONResponse(qsos_worker.snapshot(), headers=NO_STORE)


@app.get("/api/targets")
def api_targets():
    """Target dots with their WSPR (and FT8) band status, and the cities in each field."""
    return JSONResponse(targets.snapshot(), headers=NO_STORE)


@app.post("/api/targets")
async def edit_targets(request: Request):
    """{op: "add", lat, lon} | {op: "move", id, lat, lon} | {op: "remove", id}"""
    try:
        b = await request.json()
        op = b["op"]
        if op == "add":
            t = targets.add(float(b["lat"]), float(b["lon"]))
            return {"ok": True, "target": t, "version": targets.version}
        if op == "move":
            ok = targets.move(str(b["id"]), float(b["lat"]), float(b["lon"]))
        elif op == "remove":
            ok = targets.remove(str(b["id"]))
        else:
            raise ValueError(op)
    except (ValueError, KeyError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": "expected {op: add|move|remove, ...}"}, status_code=400)
    return {"ok": ok, "version": targets.version}


@app.get("/api/target-detail")
def target_detail(id: str, band: int, source: str = "wspr"):
    """The stations behind one band cell of a dot's bubble (piMap's drill-down).
    WSPR is fetched from wspr.live on first ask (poll until state is "ok"); FT8 is local."""
    t = targets.find(id)
    if t is None:
        return JSONResponse({"state": "error", "error": "no such target"}, status_code=404)
    base = {"label": t["label"], "field": t["field"], "band": band,
            "band_label": wspr_monitor.band_label(band), "source": source.upper()}
    if source == "ft8":
        if not FT8_ON:
            return {**base, "state": "error", "error": "FT8 monitor is off", "hearing": [], "heard_by": []}
        return {**base, "state": "ok", **ft8_monitor.get_detail(t["field"], band)}
    d = wspr_monitor.get_detail()
    if d is None or d.get("key") != (id, band):
        wspr_monitor.request_detail(id, t["field"], band, t["label"])
        d = wspr_monitor.get_detail() or {}
    d = {k: v for k, v in d.items() if k not in ("key", "spots")}
    return JSONResponse({**base, **d, "source": "WSPR"}, headers=NO_STORE)


@app.get("/api/openings")
def api_openings(after: int = 0):
    """Paths that just opened (a dot turning green), for every screen's chime."""
    return JSONResponse({"seq": targets.openings_seq,
                         "openings": [o for o in targets.openings if o["seq"] > after]}, headers=NO_STORE)


@app.post("/api/prefs")
async def set_prefs(request: Request):
    """{spot_mode: "FT8"|"CW"|"SSB"} and/or {alert_sound: name}: shared, like piMap's ui_prefs."""
    try:
        b = await request.json()
    except ValueError:
        return JSONResponse({"ok": False}, status_code=400)
    return {"ok": True, **prefs.set(b.get("spot_mode"), b.get("alert_sound"))}


@app.get("/api/sat/{norad}")
def api_sat(norad: int):
    """What piMap's bubble shows: active transmitters from SatNOGS (piMap's sat_radio,
    cached a day), plus the next pass over home."""
    lines, note = sat_radio.info(norad)
    return JSONResponse({"norad": norad, "lines": lines, "note": note,
                         "next_pass": passes.get().get(norad)}, headers=NO_STORE)


@app.post("/api/sats")
async def set_sat(request: Request):
    """{norad: n, on: true|false}: show or hide a satellite on the map."""
    try:
        body = await request.json()
        norad, on = int(body["norad"]), bool(body["on"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": "expected {norad: n, on: bool}"}, status_code=400)
    return {"ok": True, "shown": choice.set(norad, on)}


@app.get("/api/tv-view")
def get_tv_view():
    lo, hi = zoom_limits()
    return JSONResponse({"zoom": tv["zoom"], "min": lo, "max": hi, "centre": home.now()}, headers=NO_STORE)


@app.post("/api/tv-settings")
async def set_tv_settings(request: Request):
    """{key: "borders", on: false}: what the kiosk TV draws."""
    try:
        body = await request.json()
        key, on = str(body["key"]), bool(body["on"])
        if key not in TV_SETTING_KEYS:
            raise ValueError(key)
    except (ValueError, KeyError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": f"expected {{key: one of {list(TV_SETTING_KEYS)}, on: bool}}"},
                            status_code=400)
    tv["settings"] = {**tv["settings"], key: on}
    tv["changed"] = time.time()
    save_tv_settings(tv["settings"])
    return {"ok": True, "settings": tv["settings"]}


@app.post("/api/tv-view")
async def set_tv_view(request: Request):
    try:
        body = await request.json()
        z = float(body["zoom"])
        if not math.isfinite(z):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": "expected {zoom: number}"}, status_code=400)
    tv["zoom"], tv["changed"] = save_tv_zoom(z), time.time()
    return {"ok": True, "zoom": tv["zoom"]}


_TILE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


@app.get("/tiles/{z}/{name}")
def tile(z: int, name: str):
    p = PYRAMID / str(z) / name
    if not (0 <= z <= 8 and name.endswith(".jpg") and "/" not in name and p.is_file()):
        return Response(status_code=404)
    return FileResponse(p, media_type="image/jpeg", headers=_TILE_HEADERS)


@app.get("/iss.webp")
def iss_icon():
    return FileResponse(Path(ROOT) / get(cfg, "ISS_ICON_FILE", "images/iss.webp"),
                        headers={"Cache-Control": "public, max-age=86400"})


# ---- the FieldLab kiosk's page contract (harmless elsewhere) -------------------------
@app.get("/_fieldlab/status")
def fieldlab_status():
    now = time.time()
    ready = (PYRAMID / "pyramid.json").is_file()
    state, msg, detail = ("ready", "", "") if ready else (
        "error", "Map tiles not built", "Run tools/build_pyramid.py")
    if ready and CONFIG_SOURCE == "example":
        state, msg, detail = "degraded", "No piMap settings", "config.py not found: example settings"
    beat = worker.beat_t
    since = datetime.fromtimestamp(worker.started, timezone.utc).isoformat(timespec="seconds")
    return JSONResponse({
        "id": PAGE_ID, "state": state, "message": msg, "detail": detail,
        "since": since, "in_state_s": round(now - worker.started, 1),
        "heartbeat": None if beat is None else datetime.fromtimestamp(beat, timezone.utc).isoformat(timespec="seconds"),
        "heartbeat_age_s": None if beat is None else round(now - beat, 1),
        "uptime_s": round(now - worker.started, 1), "active": False, "tv_active": False,
        "version": VERSION}, headers=NO_STORE)


@app.get("/_fieldlab/meta")
def fieldlab_meta():
    return {"id": PAGE_ID, "title": PAGE_TITLE, "icon": "globe",
            "order": int(os.environ.get("PIMAP_PAGE_ORDER", "25")), "kind": "page",
            "version": VERSION, "start_grace_s": 60, "heartbeat_timeout_s": 180, "holds": False}


class _Revalidated(StaticFiles):
    """The map's own scripts and styles: the browser must check back every load (cheap,
    by ETag), or it can keep running an old app.js for hours after an update."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = "no-cache"
        return r


app.mount("/web", _Revalidated(directory=WEB), name="web")
app.mount("/fonts", StaticFiles(directory=Path(ROOT) / "fonts"), name="fonts")


@app.on_event("startup")
def _start():
    start_mqtt()
    threading.Thread(target=worker.run, name="refresh", daemon=True).start()
    threading.Thread(target=passes.run, name="passes", daemon=True).start()
    threading.Thread(target=qsos_worker.run, name="qrz", daemon=True).start()
    threading.Thread(target=targets.run, name="targets", daemon=True).start()
    sat_radio.preload_async()             # SatNOGS transmitters for the bubbles


def main() -> None:
    import uvicorn
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8090")),
                log_level="warning")


if __name__ == "__main__":
    main()
