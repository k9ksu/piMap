// The Blue Marble pyramid (tools/build_pyramid.py): picks the level whose tiles are about
// the size they appear on screen, draws what's loaded, stands in the nearest coarser tile
// for anything still loading, and fades sharp tiles in over it. Level 0 (the whole world,
// two tiles) is fetched first and kept, so there's never a blank square.
"use strict";

const FADE_MS = 220;

export class Tiles {
  constructor(pyramid, { maxTiles = 160, parallel = 6, onLoad = () => {} } = {}) {
    this.p = pyramid;                // {tile, levels, grids: [[cols, rows]], width: [px]}
    this.maxTiles = maxTiles;
    this.parallel = parallel;
    this.onLoad = onLoad;
    this.cache = new Map();          // "z/x/y" -> {img, ok, t, used}
    this.queue = [];
    this.loading = 0;
    this.fading = false;
    const [c0, r0] = this.p.grids[0];
    for (let y = 0; y < r0; y++) for (let x = 0; x < c0; x++) this._want(0, x, y, true);
  }

  /** The level to draw for a world `worldPx` device pixels wide. */
  level(worldPx) {
    const w = this.p.width;
    for (let z = 0; z < w.length; z++) if (w[z] >= worldPx * 0.75) return z;
    return w.length - 1;
  }

  _key(z, x, y) { return `${z}/${x}/${y}`; }

  _want(z, x, y, keep = false) {
    const k = this._key(z, x, y);
    let e = this.cache.get(k);
    if (!e) {
      e = { img: null, ok: false, t: 0, used: 0, keep, z, x, y, queued: true };
      this.cache.set(k, e);
      this.queue.push(e);
    }
    e.used = performance.now();
    return e;
  }

  _pump() {
    // newest wants first: what's on screen now matters more than what was a second ago
    this.queue.sort((a, b) => b.used - a.used);
    while (this.loading < this.parallel && this.queue.length) {
      const e = this.queue.shift();
      if (!this.cache.has(this._key(e.z, e.x, e.y))) continue;     // evicted before it started
      this.loading++;
      const img = new Image();
      img.decoding = "async";
      img.onload = () => {
        const done = () => { e.img = img; e.ok = true; e.t = performance.now(); this.loading--; this.onLoad(); this._pump(); };
        img.decode ? img.decode().then(done, done) : done();
      };
      img.onerror = () => { this.loading--; this.cache.delete(this._key(e.z, e.x, e.y)); this._pump(); };
      img.src = `tiles/${e.z}/${e.x}_${e.y}.jpg`;
    }
  }

  _evict() {
    if (this.cache.size <= this.maxTiles) return;
    const old = [...this.cache.entries()].filter(([, e]) => !e.keep && e.ok)
      .sort((a, b) => a[1].used - b[1].used);
    for (const [k] of old.slice(0, this.cache.size - this.maxTiles)) this.cache.delete(k);
  }

  /** The nearest loaded coarser tile covering (z, x, y): [entry, sx, sy, size] in its pixels. */
  _stand_in(z, x, y) {
    const T = this.p.tile;
    for (let k = 1; k <= z; k++) {
      const e = this.cache.get(this._key(z - k, x >> k, y >> k));
      if (e && e.ok) {
        const n = 1 << k, size = T / n;
        return [e, (x - ((x >> k) << k)) * size, (y - ((y >> k) << k)) * size, size];
      }
    }
    return null;
  }

  /** Draw the visible part of the world. ctx is in device px; view in CSS px. */
  draw(ctx, view, dpr) {
    const s = view.scale * dpr;
    const z = this.level(360 * s);
    const [cols, rows] = this.p.grids[z];
    const dLon = 360 / cols, dLat = 180 / rows;
    const b = view.bounds();
    const x0 = Math.max(0, Math.floor((b.west + 180) / dLon)), x1 = Math.min(cols - 1, Math.floor((b.east + 180 - 1e-9) / dLon));
    const y0 = Math.max(0, Math.floor((90 - b.north) / dLat)), y1 = Math.min(rows - 1, Math.floor((90 - b.south - 1e-9) / dLat));
    const now = performance.now();
    this.fading = false;
    const T = this.p.tile;
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        // round each edge to a device pixel so neighbouring tiles meet with no seam
        const X0 = Math.round(view.x(-180 + x * dLon) * dpr), X1 = Math.round(view.x(-180 + (x + 1) * dLon) * dpr);
        const Y0 = Math.round(view.y(90 - y * dLat) * dpr), Y1 = Math.round(view.y(90 - (y + 1) * dLat) * dpr);
        const e = this._want(z, x, y);
        const a = e.ok ? Math.min(1, (now - e.t) / FADE_MS) : 0;
        if (a < 1) {
          const si = this._stand_in(z, x, y);
          if (si) ctx.drawImage(si[0].img, si[1], si[2], si[3], si[3], X0, Y0, X1 - X0, Y1 - Y0);
          else { ctx.fillStyle = "#06101c"; ctx.fillRect(X0, Y0, X1 - X0, Y1 - Y0); }
        }
        if (a > 0) {
          ctx.globalAlpha = a;
          ctx.drawImage(e.img, 0, 0, T, T, X0, Y0, X1 - X0, Y1 - Y0);
          ctx.globalAlpha = 1;
          if (a < 1) this.fading = true;
        }
      }
    }
    this._pump();
    this._evict();
    return z;
  }

  get busy() { return this.fading || this.loading > 0 || this.queue.length > 0; }
}
