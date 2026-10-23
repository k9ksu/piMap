// QSO paths, as piMap draws them: a great-circle line from home to each contact's grid
// square (split where it crosses the date line), and a dot at the contact in QSO_COLOR
// with a black rim. The server runs piMap's QRZ loop; this only draws what it serves.
"use strict";

import { splitDateline } from "./sats.js";

const RAD = Math.PI / 180;

/** n+1 points along the great circle from a to b ({lat, lon}), longitudes in -180..180. */
export function greatCircle(a, b, n = 200) {
  const la1 = a.lat * RAD, lo1 = a.lon * RAD, la2 = b.lat * RAD, lo2 = b.lon * RAD;
  const d = 2 * Math.asin(Math.sqrt(Math.sin((la2 - la1) / 2) ** 2
    + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2));
  if (d < 1e-9) return [{ lat: a.lat, lon: a.lon }, { lat: b.lat, lon: b.lon }];
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const f = i / n, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
    const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
    const z = A * Math.sin(la1) + B * Math.sin(la2);
    pts.push({ lat: Math.atan2(z, Math.hypot(x, y)) / RAD, lon: Math.atan2(y, x) / RAD });
  }
  return pts;
}

export class Qsos {
  constructor(conf) {
    this.color = conf.color || "#FFC800";
    this.dotR = conf.dot_radius || 4;
    this.list = [];                   // {title, lines, ts, dx:{lat,lon}, segs}
    this.markers = [];                // last drawn dots, for hover and click
    this.version = -1;
  }

  set(data) {
    this.version = data.version;
    this.list = data.qsos.map((q) => {
      const home = { lat: q.home[0], lon: q.home[1] }, dx = { lat: q.dx[0], lon: q.dx[1] };
      return { title: q.title, lines: q.lines, ts: q.ts, dx, segs: splitDateline(greatCircle(home, dx)) };
    });
  }

  draw(ctx, view, dpr, u) {
    const X = (lon) => view.x(lon) * dpr, Y = (lat) => view.y(lat) * dpr;
    ctx.save();
    ctx.strokeStyle = this.color; ctx.lineWidth = 2 * u; ctx.lineJoin = "round"; ctx.lineCap = "round";
    for (const q of this.list) {
      for (const seg of q.segs) {
        ctx.beginPath();
        seg.forEach((p, i) => (i ? ctx.lineTo(X(p.lon), Y(p.lat)) : ctx.moveTo(X(p.lon), Y(p.lat))));
        ctx.stroke();
      }
    }
    this.markers = [];
    const r = this.dotR * u;
    for (const q of this.list) {
      const x = X(q.dx.lon), y = Y(q.dx.lat);
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = this.color; ctx.fill();
      ctx.lineWidth = 1 * u; ctx.strokeStyle = "#000"; ctx.stroke();
      this.markers.push({ kind: "qso", key: `qso:${q.title}:${q.ts}`, name: q.title, lines: q.lines,
                          x, y, hitR: r + 5 * u, color: this.color });
    }
    ctx.restore();
  }

  hit(x, y) {
    let best = null, bd = Infinity;
    for (const m of this.markers) {
      const d = Math.hypot(m.x - x, m.y - y);
      if (d <= m.hitR && d < bd) { best = m; bd = d; }
    }
    return best;
  }
}
