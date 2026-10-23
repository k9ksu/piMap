// Satellites, propagated in the browser (SGP4, satellite.js) from the TLEs the server
// serves. Positions are worked out for the moment each frame is drawn, at sub-pixel
// precision, so markers glide instead of stepping. Styles follow piMap: the ISS as its
// icon with a black one-orbit track, others as dots in golden-ratio hues.
"use strict";

const ISS = 25544;
const RE = 6378.137;                         // Earth radius, km

function hsv(h, s, v) {
  const i = Math.floor(h * 6), f = h * 6 - i, p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  const [r, g, b] = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][i % 6];
  return `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`;
}

/** piMap's colour for catalog index i (overlays._sat_color). */
export const satColor = (i) => hsv((i * 0.61803398875) % 1, 0.65, 1);

/** Where a satellite is at `date`: {lat, lon, alt} in degrees/km, or null. */
export function subpoint(satrec, date) {
  const pv = satellite.propagate(satrec, date);
  if (!pv || !pv.position) return null;
  const g = satellite.eciToGeodetic(pv.position, satellite.gstime(date));
  let lon = satellite.degreesLong(g.longitude);
  lon = ((lon + 180) % 360 + 360) % 360 - 180;
  return { lat: satellite.degreesLat(g.latitude), lon, alt: g.height };
}

/** Split a lat/lon path where it crosses the date line (piMap's split_path_at_dateline). */
export function splitDateline(pts) {
  const segs = [];
  let cur = [];
  for (let i = 0; i < pts.length; i++) {
    if (cur.length && Math.abs(pts[i].lon - cur[cur.length - 1].lon) > 180) { segs.push(cur); cur = []; }
    cur.push(pts[i]);
  }
  if (cur.length) segs.push(cur);
  return segs;
}

/** Visibility circle around a sub-point: ring of {lat, lon}, longitudes unwrapped. */
export function footprint(p, steps = 120) {
  const rho = Math.acos(RE / (RE + Math.max(p.alt, 1)));
  const la = p.lat * Math.PI / 180, lo = p.lon * Math.PI / 180;
  const ring = [];
  let prev = null;
  for (let k = 0; k <= steps; k++) {
    const b = (k / steps) * 2 * Math.PI;
    const lat = Math.asin(Math.sin(la) * Math.cos(rho) + Math.cos(la) * Math.sin(rho) * Math.cos(b));
    let lon = lo + Math.atan2(Math.sin(b) * Math.sin(rho) * Math.cos(la), Math.cos(rho) - Math.sin(la) * Math.sin(lat));
    lon = lon * 180 / Math.PI;
    if (prev !== null) { while (lon - prev > 180) lon -= 360; while (lon - prev < -180) lon += 360; }
    prev = lon;
    ring.push({ lat: lat * 180 / Math.PI, lon });
  }
  // A circle around a pole comes back 360 degrees round: its fill closes over the pole
  // (ring.pole), but only the circle itself is outlined.
  const net = ring[ring.length - 1].lon - ring[0].lon;
  ring.pole = Math.abs(net) > 180 ? (p.lat >= 0 ? 90 : -90) : null;
  return ring;
}

export class Sats {
  constructor(conf, issImg) {
    this.conf = conf;                        // config.sats + config.iss
    this.iss = issImg;
    this.recs = new Map();                   // norad -> {satrec, name, color, track, trackAt}
    this.show = new Set(conf.show || [ISS]);
    this.footprints = conf.footprint !== false;
    this.tracks = true;
    this.markers = [];                       // last drawn: {norad, name, lat, lon, alt, x, y, hitR, color}
  }

  /** The marker under (x, y) device px, nearest first, or null. */
  hit(x, y) {
    let best = null, bd = Infinity;
    for (const m of this.markers) {
      const d = Math.hypot(m.x - x, m.y - y);
      if (d <= m.hitR && d < bd) { best = m; bd = d; }
    }
    return best;
  }

  /** The colour a satellite is drawn in (piMap's), for the satellite panel's chips. */
  colorOf(norad) {
    const r = this.recs.get(norad);
    return r ? r.color : "#777";
  }

  setTles(list) {
    // piMap colours by position in the name-sorted catalog
    const sorted = [...list].sort((a, b) => a.name.localeCompare(b.name));
    const recs = new Map();
    sorted.forEach((t, i) => {
      try {
        const satrec = satellite.twoline2satrec(t.l1, t.l2);
        recs.set(t.norad, { satrec, name: t.name, color: t.norad === ISS ? "#000" : satColor(i), track: null, trackAt: 0 });
      } catch (e) { /* a malformed TLE: skip it */ }
    });
    this.recs = recs;
  }

  get count() { return this.recs.size; }

  _track(r, now) {
    if (r.track && now - r.trackAt < 10000) return r.track;
    const period = r.satrec.no > 0 ? (2 * Math.PI / r.satrec.no) * 60 : 95 * 60;     // s
    const pts = [];
    for (let t = 0; t <= period; t += 10) {
      const p = subpoint(r.satrec, new Date(now + t * 1000));
      if (p) pts.push(p);
    }
    r.track = splitDateline(pts); r.trackAt = now;
    return r.track;
  }

  /** Draw tracks, footprints and markers. ctx in device px, u = device px per 1080p px. */
  draw(ctx, view, dpr, u, now = Date.now()) {
    const date = new Date(now);
    const X = (lon) => view.x(lon) * dpr, Y = (lat) => view.y(lat) * dpr;
    const shown = [...this.show].map((n) => [n, this.recs.get(n)]).filter(([, r]) => r);
    const out = [];
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    for (const [n, r] of shown) {
      ctx.strokeStyle = r.color; ctx.lineWidth = 3 * u;
      for (const seg of this.tracks ? this._track(r, now) : []) {
        ctx.beginPath();
        seg.forEach((p, i) => (i ? ctx.lineTo(X(p.lon), Y(p.lat)) : ctx.moveTo(X(p.lon), Y(p.lat))));
        ctx.stroke();
      }
      const p = subpoint(r.satrec, date);
      if (!p) continue;
      out.push({ norad: n, name: r.name, ...p });
      if (this.footprints) {
        const ring = footprint(p);
        ctx.strokeStyle = n === ISS ? "rgba(255,255,255,.55)" : r.color;
        ctx.fillStyle = "rgba(255,255,255,.05)";
        ctx.lineWidth = 1.5 * u;
        ctx.setLineDash([6 * u, 5 * u]);
        for (const off of [-360, 0, 360]) {       // a ring crossing the date line shows on both sides
          ctx.beginPath();
          ring.forEach((q, i) => (i ? ctx.lineTo(X(q.lon + off), Y(q.lat)) : ctx.moveTo(X(q.lon + off), Y(q.lat))));
          if (ring.pole === null) { ctx.closePath(); ctx.fill(); ctx.stroke(); continue; }
          ctx.stroke();                          // the circle...
          const a = ring[0], z = ring[ring.length - 1];
          ctx.lineTo(X(z.lon + off), Y(ring.pole)); ctx.lineTo(X(a.lon + off), Y(ring.pole));
          ctx.closePath(); ctx.fill();           // ...and the cap over the pole, filled only
        }
        ctx.setLineDash([]);
      }
    }
    // markers on top of every track; remember where, for hover and click
    this.markers = [];
    for (const s of out) {
      const x = X(s.lon), y = Y(s.lat), r = this.recs.get(s.norad);
      const hitR = s.norad === ISS ? ((this.conf.iss_width || 62) * u) / 2 : ((this.conf.dot_radius || 6) + 5) * u;
      this.markers.push({ ...s, x, y, hitR, color: r.color });
      if (s.norad === ISS && this.iss && this.iss.complete && this.iss.naturalWidth) {
        const w = (this.conf.iss_width || 62) * u, h = w * this.iss.naturalHeight / this.iss.naturalWidth;
        const sh = this.conf.iss_shadow || [-7.5, 7.5];
        const g = Math.round(255 * (this.conf.iss_shadow_gray ?? 0.5));
        ctx.save();
        ctx.shadowColor = `rgba(${g},${g},${g},${this.conf.iss_shadow_alpha ?? 0.1})`;
        ctx.shadowOffsetX = sh[0] * u; ctx.shadowOffsetY = sh[1] * u; ctx.shadowBlur = 2 * u;
        ctx.drawImage(this.iss, x - w / 2, y - h / 2, w, h);
        ctx.restore();
      } else {
        const rad = (this.conf.dot_radius || 6) * u;
        ctx.beginPath(); ctx.arc(x, y, rad, 0, 2 * Math.PI);
        ctx.fillStyle = r.color; ctx.fill();
        ctx.lineWidth = 1 * u; ctx.strokeStyle = "#000"; ctx.stroke();
      }
    }
    return out;
  }

  /** Fastest-moving shown marker, in device px per second at this view (for redraw pacing). */
  speedPx(view, dpr) {
    let best = 0;
    const now = Date.now();
    for (const n of this.show) {
      const r = this.recs.get(n);
      if (!r) continue;
      const a = subpoint(r.satrec, new Date(now)), b = subpoint(r.satrec, new Date(now + 1000));
      if (!a || !b) continue;
      let dlon = b.lon - a.lon;
      if (dlon > 180) dlon -= 360; if (dlon < -180) dlon += 360;
      best = Math.max(best, Math.hypot(dlon, b.lat - a.lat) * view.scale * dpr);
    }
    return best;
  }
}
