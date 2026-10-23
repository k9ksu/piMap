"""piMap 2.0 server: run with  python3 -m unittest discover -s tests"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("PIMAP_MQTT_HOST", None)
os.environ["PIMAP_DATA_DIR"] = tempfile.mkdtemp()
os.environ["PIMAP_CONFIG_DIR"] = tempfile.mkdtemp()          # empty: example settings

from fastapi.testclient import TestClient  # noqa: E402

from pimap2 import server  # noqa: E402


class Fix(unittest.TestCase):
    def test_good(self):
        lat, lon, ts = server.parse_fix(b'{"lat": 38.8262, "lon": -97.7789, "ts": 1800000000}')
        self.assertEqual((lat, lon, ts), (38.8262, -97.7789, 1800000000.0))

    def test_rejects(self):
        for p in (b"", b"null", b"{}", b"junk", b'{"lat": 0, "lon": 0}', b'{"lat": 91, "lon": 0}'):
            self.assertIsNone(server.parse_fix(p), p)

    def test_gps_beats_config_until_stale(self):
        h = server.Home()
        self.assertEqual(h.now()["source"], "config")
        h.set_gps(38.8262, -97.7789)
        self.assertEqual((h.now()["lat"], h.now()["source"]), (38.8262, "gps"))
        h.set_gps(38.8262, -97.7789, ts=time.time() - server.GPS_MAX_AGE_S - 5)
        self.assertEqual(h.now()["source"], "config")


class TvView(unittest.TestCase):
    def test_saved_and_clamped(self):
        lo, hi = server.zoom_limits()
        self.assertEqual(server.save_tv_zoom(2.5), 2.5)
        self.assertEqual(server.load_tv_zoom(), 2.5)
        self.assertEqual(server.save_tv_zoom(999), hi)
        self.assertEqual(server.save_tv_zoom(0.01), lo)

    def test_view_file(self):
        v = server.save_tv_view({"zoom": 3, "lon": 12.5, "lat": 41.9, "follow": False})
        self.assertEqual((v["lon"], v["lat"], v["follow"]), (12.5, 41.9, False))
        self.assertEqual(server.load_tv_view(), v)
        self.assertTrue(server._clean_view({"zoom": 2, "follow": False})["follow"])   # no spot: home
        self.assertEqual(server._clean_view({"zoom": "nan"})["zoom"], server.zoom_limits()[0])
        server.save_tv_view({"zoom": 1})


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(server.app)

    def test_config(self):
        r = self.c.get("/api/config").json()
        self.assertEqual(r["settings"], "example")
        self.assertIn("pyramid", r)
        self.assertGreater(r["map"]["max_zoom"], r["map"]["min_zoom"])

    def test_state(self):
        r = self.c.get("/api/state").json()
        self.assertEqual(set(r["status"]), set(server.STATUS_TAGS))
        self.assertIn("lat", r["home"])

    def test_tv_view_post(self):
        self.c.post("/api/tv-view", json={"follow": True})
        self.assertEqual(self.c.post("/api/tv-view", json={"zoom": 3}).json()["zoom"], 3.0)
        self.assertEqual(self.c.post("/api/tv-view", json={"zoom": "nan"}).status_code, 400)
        self.assertEqual(self.c.post("/api/tv-view", json={"x": 1}).status_code, 400)
        self.assertEqual(self.c.post("/api/tv-view", json=[1]).status_code, 400)
        d = self.c.get("/api/tv-view").json()
        self.assertEqual((d["zoom"], d["follow"]), (3.0, True))
        self.assertEqual(d["centre"], {"lon": server.home.now()["lon"], "lat": server.home.now()["lat"]})

    def test_tv_pan_and_home(self):
        """The remote: a pan stops following GPS; follow puts the TV back on home."""
        d = self.c.post("/api/tv-view", json={"zoom": 4, "lon": 10.0, "lat": 50.0}).json()
        self.assertEqual((d["follow"], d["centre"]), (False, {"lon": 10.0, "lat": 50.0}))
        self.assertFalse(server.load_tv_view()["follow"])                      # saved
        self.assertEqual(self.c.get("/api/state").json()["tv"]["centre"]["lon"], 10.0)
        d = self.c.post("/api/tv-view", json={"follow": True}).json()
        self.assertTrue(d["follow"])
        self.assertEqual(d["centre"]["lat"], server.home.now()["lat"])
        self.assertEqual(d["zoom"], 4.0)
        self.c.post("/api/tv-view", json={"zoom": 1})

    def test_tv_screen(self):
        self.assertTrue(self.c.post("/api/tv-screen", json={"w": 3840, "h": 2160}).json()["ok"])
        self.assertEqual(self.c.get("/api/tv-view").json()["screen"], {"w": 3840, "h": 2160})
        self.assertEqual(self.c.post("/api/tv-screen", json={"w": 5, "h": 2160}).status_code, 400)

    def test_remote_page(self):
        """The remote's port opens on the remote; the normal port on the map."""
        self.assertNotIn('class="remote"', self.c.get("/").text)
        old = server.REMOTE_PORT
        server.REMOTE_PORT = 8001
        try:
            remote = TestClient(server.app, base_url="http://testserver:8001")
            self.assertIn('<html lang="en" class="remote">', remote.get("/").text)
        finally:
            server.REMOTE_PORT = old

    def test_tiles_guarded(self):
        for bad in ("/tiles/0/..%2F..%2Fconfig.py", "/tiles/0/x.png", "/tiles/99/0_0.jpg"):
            self.assertEqual(self.c.get(bad).status_code, 404, bad)
        if (server.PYRAMID / "0" / "0_0.jpg").is_file():
            r = self.c.get("/tiles/0/0_0.jpg")
            self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"))
            self.assertIn("immutable", r.headers["cache-control"])

    def test_boundaries(self):
        r = self.c.get("/api/boundaries", headers={"accept-encoding": "gzip"})
        lines = r.json()["lines"]
        self.assertGreater(len(lines), 1000)                        # countries, states, coasts
        self.assertTrue(all(len(f) % 2 == 0 for f in lines))        # flat lon, lat pairs
        lon, lat = lines[0][0], lines[0][1]
        self.assertTrue(-180 <= lon <= 180 and -90 <= lat <= 90)

    def test_sats_choice(self):
        d = self.c.get("/api/sats").json()
        self.assertIn("shown", d)
        self.assertEqual(self.c.post("/api/sats", json={"norad": 27607, "on": True}).json()["shown"][-1], 27607)
        self.assertNotIn(27607, self.c.post("/api/sats", json={"norad": 27607, "on": False}).json()["shown"])
        self.assertEqual(self.c.post("/api/sats", json={"norad": "x"}).status_code, 400)
        self.assertEqual(self.c.get("/api/state").json()["sats"]["shown"], server.choice.shown)

    def test_sat_bubble(self):
        d = self.c.get("/api/sat/25544").json()
        self.assertEqual(d["norad"], 25544)
        self.assertIsInstance(d["lines"], list)
        self.assertIsInstance(d["note"], str)                       # "" or e.g. "loading radio data..."

    def test_status_labels_as_pimap(self):
        st = self.c.get("/api/state").json()["status"]
        self.assertEqual([v["label"] for v in st.values()], ["TLE", "SWX", "QRZ", "FRQ", "WSP"])
        self.assertNotIn(st["wspr"]["state"], ("",))                 # WSPR runs in piMap 2.0 now

    def test_tv_settings(self):
        st = self.c.get("/api/state").json()["tv"]["settings"]
        self.assertEqual(set(st), set(server.TV_SETTING_KEYS))
        r = self.c.post("/api/tv-settings", json={"key": "borders", "on": False}).json()
        self.assertFalse(r["settings"]["borders"])
        self.assertFalse(server.load_tv_settings()["borders"])           # saved
        self.assertFalse(self.c.get("/api/state").json()["tv"]["settings"]["borders"])
        self.assertEqual(self.c.post("/api/tv-settings", json={"key": "nope", "on": True}).status_code, 400)
        self.c.post("/api/tv-settings", json={"key": "borders", "on": True})

    def test_web_files_revalidate(self):
        r = self.c.get("/web/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-cache")      # updates reach browsers at once

    def test_qsos_session_mode(self):
        """piMap's session rule through the server: a run of recent QSOs hides old ones."""
        import calendar
        now = time.time()
        def rec(call, grid, mins):
            t = time.gmtime(now - mins * 60)
            return {"CALL": call, "GRIDSQUARE": grid, "QSO_DATE": time.strftime("%Y%m%d", t),
                    "TIME_ON": time.strftime("%H%M%S", t), "BAND": "20m", "MODE": "FT8"}
        recs = [rec("JA1XYZ", "PM95ut", 3), rec("G4ABC", "IO91wm", 9), rec("W1OLD", "FN42", 600)]
        import qrz_client
        qsos = qrz_client.make_qsos_from_qrz(recs, (38.92, -97.21))
        q = server.Qsos()
        server.cfg.QRZ_SESSION_WINDOW_MIN = 30
        q._apply(qsos)
        snap = q.snapshot()
        self.assertTrue(snap["session"])
        self.assertEqual([x["title"] for x in snap["qsos"]], ["JA1XYZ", "G4ABC"])
        v = snap["version"]
        q._apply(qsos)
        self.assertEqual(q.snapshot()["version"], v)                # nothing new: same version
        self.assertEqual(self.c.get("/api/qsos").status_code, 200)

    def test_targets_add_move_remove(self):
        r = self.c.post("/api/targets", json={"op": "add", "lat": 51.5, "lon": -0.12}).json()
        t = r["target"]
        self.assertEqual((t["label"], t["field"]), ("IO91", "IO"))           # piMap's grid labels
        self.assertTrue(server.TARGETS_FILE.is_file())                      # saved, shared
        self.assertTrue(self.c.post("/api/targets", json={"op": "move", "id": t["id"], "lat": -33.9, "lon": 151.2}).json()["ok"])
        d = self.c.get("/api/targets").json()
        self.assertEqual([x["label"] for x in d["targets"] if x["id"] == t["id"]], ["QF56"])
        self.assertTrue(self.c.post("/api/targets", json={"op": "remove", "id": t["id"]}).json()["ok"])
        self.assertFalse(self.c.post("/api/targets", json={"op": "remove", "id": t["id"]}).json()["ok"])
        self.assertEqual(self.c.post("/api/targets", json={"op": "zap"}).status_code, 400)

    def test_prefs_shared(self):
        r = self.c.post("/api/prefs", json={"spot_mode": "SSB", "alert_sound": "Bell"}).json()
        self.assertEqual((r["spot_mode"], r["alert_sound"]), ("SSB", "Bell"))
        self.assertEqual(self.c.post("/api/prefs", json={"spot_mode": "RTTY"}).json()["spot_mode"], "SSB")
        self.assertEqual(self.c.get("/api/state").json()["prefs"]["alert_sound"], "Bell")
        self.c.post("/api/prefs", json={"spot_mode": "FT8", "alert_sound": "Soft Chime"})

    def test_openings_feed(self):
        server.targets.openings_seq += 1
        server.targets.openings.append({"seq": server.targets.openings_seq, "target_id": "x", "label": "IO91"})
        d = self.c.get(f"/api/openings?after={server.targets.openings_seq - 1}").json()
        self.assertEqual([o["label"] for o in d["openings"]], ["IO91"])
        self.assertEqual(self.c.get(f"/api/openings?after={d['seq']}").json()["openings"], [])

    def test_kiosk_contract(self):
        st = self.c.get("/_fieldlab/status").json()
        self.assertIn(st["state"], ("ready", "degraded", "error"))
        self.assertEqual(self.c.get("/_fieldlab/meta").json()["id"], "pimap")


class Pyramid(unittest.TestCase):
    def test_manifest(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import build_pyramid as bp
        m = bp.manifest()
        self.assertEqual(m["width"][-1], 21600)                     # full Blue Marble
        self.assertEqual(m["grids"][0], [2, 1])
        for z, (cols, rows) in enumerate(m["grids"]):
            self.assertEqual(cols, 2 * rows)                        # square tiles in degrees


if __name__ == "__main__":
    unittest.main()
