// Country, state/province and coastline lines (piMap's data/boundaries, same settings),
// shown once zoomed past BOUNDARY_MIN_ZOOM, as piMap does, fading in over the last step.
//
// 156k points is too many to stroke every frame at 4K or on a Pi, so each line is kept
// at a few levels of detail (points closer than 0.2, 0.05, 0.01 degrees dropped) and
// drawn from the coarsest level that still has no visible error (under half a device
// pixel). Lines outside the view are skipped by their bounding box, and what's left is
// stroked as one path in screen pixels.
"use strict";

const LOD = [0.2, 0.05, 0.01, 0];         // degrees between kept points; 0 = every point

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
  constructor(conf) {
    this.conf = conf;                     // {enabled, min_zoom, color, alpha}
    this.lines = [];                      // {w, e, s, n, paths: [Path2D per LOD]}
  }

  load(lines) {
    this.lines = lines.filter((f) => f.length >= 4).map((f) => {
      let w = 180, e = -180, s = 90, n = -90;
      for (let i = 0; i < f.length; i += 2) {
        w = Math.min(w, f[i]); e = Math.max(e, f[i]); s = Math.min(s, f[i + 1]); n = Math.max(n, f[i + 1]);
      }
      return { w, e, s, n, pts: LOD.map((t) => Float32Array.from(thin(f, t))) };
    });
  }

  /** How strongly to draw at this zoom: 0 below the threshold, fading in over one step. */
  strength(zoom) {
    const z0 = this.conf.min_zoom || 2.5;
    return Math.max(0, Math.min(1, (zoom - z0 / 1.15) / (z0 - z0 / 1.15)));
  }

  draw(ctx, view, dpr, u) {
    if (!this.conf.enabled || !this.lines.length) return;
    const k = this.strength(view.zoom);
    if (k <= 0) return;
    const s = view.scale * dpr;                       // device px per degree
    let lod = LOD.length - 1;
    for (let i = 0; i < LOD.length; i++) if (LOD[i] <= 0.5 / s) { lod = i; break; }
    const b = view.bounds();
    // lon/lat to device px here, in JS: one thin path in screen space strokes fast;
    // the same lines stroked through a big degrees-to-pixels transform do not.
    const ox = (view.w / 2 - view.lon * view.scale) * dpr, oy = (view.h / 2 + view.lat * view.scale) * dpr;
    const p = new Path2D();
    for (const L of this.lines) {
      if (L.e < b.west || L.w > b.east || L.n < b.south || L.s > b.north) continue;
      const a = L.pts[lod];
      p.moveTo(ox + a[0] * s, oy - a[1] * s);
      for (let i = 2; i < a.length; i += 2) p.lineTo(ox + a[i] * s, oy - a[i + 1] * s);
    }
    ctx.save();
    ctx.lineWidth = Math.max(1, u);                   // piMap's 1 px line, at this resolution
    ctx.lineJoin = "round";
    ctx.strokeStyle = this.conf.color || "#8FA3B8";
    ctx.globalAlpha = (this.conf.alpha ?? 0.55) * k;
    ctx.stroke(p);
    ctx.restore();
  }
}
