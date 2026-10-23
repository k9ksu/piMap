// Country, state/province and coastline lines (piMap's data/boundaries, same settings),
// shown once zoomed past BOUNDARY_MIN_ZOOM, as piMap does, fading in over the last step.
//
// Tens of thousands of thin anti-aliased segments are slow on a GPU canvas but quick on
// the CPU, so the lines live on their own CPU-backed canvas layer. While the map is
// moving, that layer is slid and scaled with the map by the compositor (a CSS
// transform: almost free) and redrawn sharp at most every 120 ms; when the map stops
// it's redrawn at once. Each line is kept at a few levels of detail and drawn from the
// coarsest with no visible error; lines outside the view are skipped.
"use strict";

const LOD = [0.2, 0.05, 0.01, 0];         // degrees between kept points; 0 = every point
const REDRAW_MOVING_MS = 120;

function thin(flat, tol) {
  if (!tol) return flat;
  const out = [flat[0], flat[1]];
  let px = flat[0], py = flat[1];
  for (let i = 2; i < flat.length - 2; i += 2) {
    const x = flat[i], y = flat[i + 1];
    if (Math.abs(x - px) >= tol || Math.abs(y - py) >= tol) { out.push(x, y); px = x; py = y; }
  }
  out.push(flat[flat.length - 2], flat[flat.length - 1]);  // always keep the end
  return out;
}

export class Borders {
  /** canvas: this layer's own <canvas>, stacked over the map and under the satellites. */
  constructor(conf, canvas) {
    this.conf = conf;                     // {enabled, min_zoom, color, alpha}
    this.lines = [];                      // {w, e, s, n, pts: [Float32Array per LOD]}
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { willReadFrequently: true });   // CPU-backed: fast lines
    this.snap = null;                     // the view the layer was last drawn for
    this.at = -1e9;
    canvas.style.transformOrigin = "0 0";
  }

  load(lines) {
    this.lines = lines.filter((f) => f.length >= 4).map((f) => {
      let w = 180, e = -180, s = 90, n = -90;
      for (let i = 0; i < f.length; i += 2) {
        w = Math.min(w, f[i]); e = Math.max(e, f[i]); s = Math.min(s, f[i + 1]); n = Math.max(n, f[i + 1]);
      }
      return { w, e, s, n, pts: LOD.map((t) => Float32Array.from(thin(f, t))) };
    });
    this.snap = null;
  }

  /** How strongly to draw at this zoom: 0 below the threshold, fading in over one step. */
  strength(zoom) {
    const z0 = this.conf.min_zoom || 2.5;
    return Math.max(0, Math.min(1, (zoom - z0 / 1.15) / (z0 - z0 / 1.15)));
  }

  resize(w, h) {
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w; this.canvas.height = h; this.snap = null;
    }
  }

  /** Keep the layer in step with the view. `moving`: the view is animating or dragged. */
  update(view, dpr, u, now, moving) {
    const on = this.conf.enabled && this.lines.length;
    const k = on ? this.strength(view.zoom) : 0;
    const s = this.snap;
    const same = s && s.lon === view.lon && s.lat === view.lat && s.scale === view.scale && Math.abs(s.k - k) < 0.02;
    if (same) return;
    if (s && moving && now - this.at < REDRAW_MOVING_MS && Math.abs(s.k - k) < 0.2) {
      // slide and scale the last drawing with the map: the compositor does this for free
      const r = view.scale / s.scale;
      const dx = (view.w / 2 - view.lon * view.scale) - (view.w / 2 - s.lon * s.scale) * r;
      const dy = (view.h / 2 + view.lat * view.scale) - (view.h / 2 + s.lat * s.scale) * r;
      this.canvas.style.transform = `translate(${dx}px, ${dy}px) scale(${r})`;
      return;
    }
    this.render(view, dpr, u, k);
    this.snap = { lon: view.lon, lat: view.lat, scale: view.scale, k };
    this.at = now;
  }

  render(view, dpr, u, k) {
    const ctx = this.ctx, W = this.canvas.width, H = this.canvas.height;
    this.canvas.style.transform = "";
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (k <= 0) return;
    const s = view.scale * dpr;                       // device px per degree
    let lod = LOD.length - 1;
    for (let i = 0; i < LOD.length; i++) if (LOD[i] <= 0.5 / s) { lod = i; break; }
    const b = view.bounds();
    const ox = (view.w / 2 - view.lon * view.scale) * dpr, oy = (view.h / 2 + view.lat * view.scale) * dpr;
    ctx.beginPath();
    for (const L of this.lines) {
      if (L.e < b.west || L.w > b.east || L.n < b.south || L.s > b.north) continue;
      const a = L.pts[lod];
      ctx.moveTo(ox + a[0] * s, oy - a[1] * s);
      for (let i = 2; i < a.length; i += 2) ctx.lineTo(ox + a[i] * s, oy - a[i + 1] * s);
    }
    ctx.lineWidth = Math.max(1, u);                   // piMap's 1 px line, at this resolution
    ctx.lineJoin = "round";
    ctx.strokeStyle = this.conf.color || "#8FA3B8";
    ctx.globalAlpha = (this.conf.alpha ?? 0.55) * k;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}
