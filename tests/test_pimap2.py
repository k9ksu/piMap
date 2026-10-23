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
        self.assertEqual(self.c.post("/api/tv-view", json={"zoom": 3}).json()["zoom"], 3.0)
        self.assertEqual(self.c.post("/api/tv-view", json={"zoom": "nan"}).status_code, 400)
        self.assertEqual(self.c.post("/api/tv-view", json={"x": 1}).status_code, 400)
        self.assertEqual(self.c.get("/api/tv-view").json()["zoom"], 3.0)

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
