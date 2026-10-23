// piMap 2.0 browser maths: run with  node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { subsolar, sunElevation } from "../web/sky.js";
import { footprint, splitDateline } from "../web/sats.js";
import { View } from "../web/view.js";
import { fmtPass } from "../web/panels.js";

test("sun: declination through the year", () => {
  assert.ok(Math.abs(subsolar(new Date("2026-03-20T14:46:00Z")).lat) < 0.3);         // equinox
  assert.ok(Math.abs(subsolar(new Date("2026-06-21T08:24:00Z")).lat - 23.44) < 0.1);  // solstice
});

test("sun: subsolar longitude follows the clock", () => {
  const s = subsolar(new Date("2026-10-23T12:00:00Z"));
  assert.ok(Math.abs(s.lon - (-3.9)) < 0.5, `noon UTC: ${s.lon}`);                   // eq. of time ~+16 min
  assert.ok(sunElevation(s, s.lat, s.lon) > 89.9);
  assert.ok(sunElevation(s, -s.lat, s.lon + 180) < -89.9);
});

test("footprint: ISS ring, about 2200 km radius", () => {
  const ring = footprint({ lat: 0, lon: 0, alt: 420 });
  assert.equal(ring.pole, null);
  const north = Math.max(...ring.map((p) => p.lat));
  assert.ok(Math.abs(north * 111.2 - 2250) < 120, `${north * 111.2} km`);
});

test("footprint: a ring round the pole is closed over it", () => {
  const ring = footprint({ lat: 80, lon: 10, alt: 800 });
  assert.equal(ring.pole, 90);
});

test("date line: split where the path jumps", () => {
  const segs = splitDateline([{ lat: 0, lon: 170 }, { lat: 1, lon: 179 }, { lat: 2, lon: -178 }, { lat: 3, lon: -170 }]);
  assert.equal(segs.length, 2);
});

test("view: zoom 1 shows the whole world, centred", () => {
  const v = new View(1, 11);
  v.resize(1920, 940);
  v.set(1, -97.7, 38.8);
  assert.equal(v.lon, 0); assert.equal(v.lat, 0);              // can't pan past the edges
  assert.ok(Math.abs(v.x(-180) - 20) < 1e-9);                  // piMap's 20 px margins at 1080p
});

test("view: fly-to ends centred on home", () => {
  const v = new View(1, 11);
  v.resize(1920, 940);
  v.flyTo(2.5, -97.7789, 38.8262);
  for (let i = 0; i < 200; i++) v.step(1 / 60);
  assert.equal(v.zoom, 2.5);
  assert.ok(Math.abs(v.x(-97.7789) - 960) < 0.5 && Math.abs(v.y(38.8262) - 470) < 0.5);
});

test("view: wheel zoom keeps the point under the mouse", () => {
  const v = new View(1, 11);
  v.resize(1920, 940);
  v.set(3, -97.7, 38.8);
  const lon = v.lonAt(400), lat = v.latAt(300);
  v.zoomAt(1.15, 400, 300);
  for (let i = 0; i < 120; i++) v.step(1 / 60);
  assert.ok(Math.abs(v.x(lon) - 400) < 0.5 && Math.abs(v.y(lat) - 300) < 0.5);
});

test("panel: piMap's next-pass labels", () => {
  assert.equal(fmtPass(null, 0), "--");
  assert.equal(fmtPass(100, 200), "now");
  assert.equal(fmtPass(1000 + 30, 1000), "1m");
  assert.equal(fmtPass(1000 + 23 * 60 + 5, 1000), "23m");
  assert.equal(fmtPass(1000 + 76 * 60, 1000), "1h 16m");
});
