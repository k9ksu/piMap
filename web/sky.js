// The sun and the grey line. The night side is a small soft mask (0.5 degree cells)
// with a twilight ramp from sunset to 6 degrees below the horizon, drawn stretched over
// the world: smooth at any zoom, and only recomputed every 20 seconds (the terminator
// moves a quarter of a degree a minute).
"use strict";

const RAD = Math.PI / 180;

/** Sub-solar point {lat, lon} at `date` (NOAA's low-precision formulas, ~0.01 deg). */
export function subsolar(date) {
  const d = date.getTime() / 86400000 - 10957.5;          // days since J2000.0
  const g = (357.529 + 0.98560028 * d) * RAD;
  const q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * RAD;
  const e = (23.439 - 0.00000036 * d) * RAD;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)) / RAD;
  const dec = Math.asin(Math.sin(e) * Math.sin(L)) / RAD;
  let eqt = (q - ra) / 15;                                // equation of time, hours
  eqt = ((eqt + 12) % 24 + 24) % 24 - 12;
  const utcH = (date.getTime() % 86400000) / 3600000;
  let lon = -15 * (utcH - 12 + eqt);
  lon = ((lon + 180) % 360 + 360) % 360 - 180;
  return { lat: dec, lon };
}

/** Sun elevation in degrees at (lat, lon). */
export function sunElevation(sun, lat, lon) {
  const c = Math.sin(lat * RAD) * Math.sin(sun.lat * RAD)
          + Math.cos(lat * RAD) * Math.cos(sun.lat * RAD) * Math.cos((lon - sun.lon) * RAD);
  return Math.asin(Math.max(-1, Math.min(1, c))) / RAD;
}

export class Night {
  constructor(alpha255 = 75, cellsPerDeg = 2) {
    this.alpha = Math.max(0, Math.min(255, alpha255));
    this.n = cellsPerDeg;
    this.canvas = document.createElement("canvas");
    this.canvas.width = 360 * this.n; this.canvas.height = 180 * this.n;
    this.ctx = this.canvas.getContext("2d");
    this.at = 0;
  }

  /** Recompute if older than 20 s. Returns true when it changed. */
  update(now = Date.now(), force = false) {
    if (!force && now - this.at < 20000) return false;
    this.at = now;
    const sun = subsolar(new Date(now));
    const W = this.canvas.width, H = this.canvas.height;
    const img = this.ctx.createImageData(W, H), px = img.data;
    const sl = Math.sin(sun.lat * RAD), cl = Math.cos(sun.lat * RAD);
    for (let j = 0; j < H; j++) {
      const lat = (90 - (j + 0.5) / this.n) * RAD, a = Math.sin(lat) * sl, b = Math.cos(lat) * cl;
      for (let i = 0; i < W; i++) {
        const lon = -180 + (i + 0.5) / this.n;
        const el = Math.asin(Math.max(-1, Math.min(1, a + b * Math.cos((lon - sun.lon) * RAD)))) / RAD;
        // 0 in daylight, full night 6 degrees below the horizon, smooth in between
        let t = Math.min(1, Math.max(0, (0.5 - el) / 6.5));
        t = t * t * (3 - 2 * t);
        const o = (j * W + i) * 4;
        px[o] = 0; px[o + 1] = 0; px[o + 2] = 0; px[o + 3] = t * this.alpha;     // black, as piMap
      }
    }
    this.ctx.putImageData(img, 0, 0);
    return true;
  }

  draw(ctx, view, dpr) {
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(this.canvas, view.x(-180) * dpr, view.y(90) * dpr, 360 * view.scale * dpr, 180 * view.scale * dpr);
  }
}
