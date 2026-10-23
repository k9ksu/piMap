// The map's view: where it's centred and how far in, with piMap's rules (the world fits
// the map area at zoom 1; you can never pan past its edges) and smooth motion: zoom and
// fly-to ease toward a target a little each frame instead of jumping.
"use strict";

export class View {
  constructor(minZoom = 1, maxZoom = 8) {
    this.w = 1; this.h = 1;                // map area, CSS px
    this.minZoom = minZoom; this.maxZoom = maxZoom;
    this.zoom = minZoom; this.lon = 0; this.lat = 0;
    this.target = null;                    // {zoom, anchor|centre}
    this.vel = null;                       // pan inertia, CSS px/s
    this.changed = true;
  }

  resize(w, h) { this.w = w; this.h = h; this.clamp(); this.changed = true; }

  /** CSS px per degree at the current zoom. */
  get scale() { return this.baseScale * this.zoom; }
  get baseScale() { return Math.min(this.w / 360, this.h / 180); }

  x(lon) { return this.w / 2 + (lon - this.lon) * this.scale; }
  y(lat) { return this.h / 2 - (lat - this.lat) * this.scale; }
  lonAt(x) { return this.lon + (x - this.w / 2) / this.scale; }
  latAt(y) { return this.lat - (y - this.h / 2) / this.scale; }

  /** Visible lon/lat box, clipped to the world. */
  bounds() {
    const hw = this.w / 2 / this.scale, hh = this.h / 2 / this.scale;
    return { west: Math.max(-180, this.lon - hw), east: Math.min(180, this.lon + hw),
             north: Math.min(90, this.lat + hh), south: Math.max(-90, this.lat - hh) };
  }

  clamp() {
    this.zoom = Math.min(Math.max(this.zoom, this.minZoom), this.maxZoom);
    const s = this.scale, hw = this.w / 2 / s, hh = this.h / 2 / s;
    this.lon = hw >= 180 ? 0 : Math.min(Math.max(this.lon, -180 + hw), 180 - hw);
    this.lat = hh >= 90 ? 0 : Math.min(Math.max(this.lat, -90 + hh), 90 - hh);
  }

  set(zoom, lon, lat) {
    this.zoom = zoom; this.lon = lon; this.lat = lat;
    this.target = null; this.vel = null; this.clamp(); this.changed = true;
  }

  /** Zoom by `factor`, keeping the point under (sx, sy) where it is: wheel, pinch. */
  zoomAt(factor, sx, sy) {
    const base = this.target && this.target.anchor ? this.target.zoom : this.zoom;
    const z = Math.min(Math.max(base * factor, this.minZoom), this.maxZoom);
    this.target = { zoom: z, anchor: { sx, sy, lon: this.lonAt(sx), lat: this.latAt(sy) } };
    this.vel = null;
  }

  /** Glide to a zoom centred on (lon, lat): the TV view, reset. */
  flyTo(zoom, lon, lat) {
    this.target = { zoom: Math.min(Math.max(zoom, this.minZoom), this.maxZoom), centre: { lon, lat } };
    this.vel = null;
  }

  panBy(dx, dy) {
    const s = this.scale;
    this.lon -= dx / s; this.lat += dy / s;
    this.target = this.target && this.target.anchor ? null : this.target;
    this.clamp(); this.changed = true;
  }

  fling(vx, vy) { this.vel = Math.hypot(vx, vy) > 60 ? { x: vx, y: vy } : null; }

  /** Advance the motion by dt seconds. Returns true while anything is still moving. */
  step(dt) {
    let moving = false;
    const k = 1 - Math.exp(-dt * 12);           // ~90% of the way in 0.2 s
    const t = this.target;
    if (t) {
      const lz = Math.log(t.zoom / this.zoom);
      const done = Math.abs(lz) < 1e-4;
      this.zoom = done ? t.zoom : this.zoom * Math.exp(lz * k);
      if (t.anchor) {
        const a = t.anchor, s = this.scale;
        this.lon = a.lon - (a.sx - this.w / 2) / s;
        this.lat = a.lat + (a.sy - this.h / 2) / s;
        this.clamp();
        if (done) this.target = null;
      } else {
        const c = t.centre;
        const dlon = c.lon - this.lon, dlat = c.lat - this.lat;
        this.lon += dlon * k; this.lat += dlat * k;
        this.clamp();
        const near = Math.abs(this.x(c.lon) - this.w / 2) < 0.3 && Math.abs(this.y(c.lat) - this.h / 2) < 0.3;
        if (done && (near || this._stuck(c))) { this.set(t.zoom, c.lon, c.lat); }
      }
      moving = true; this.changed = true;
    }
    if (this.vel) {
      this.panBy(this.vel.x * dt, this.vel.y * dt);
      const f = Math.exp(-dt * 4);
      this.vel.x *= f; this.vel.y *= f;
      if (Math.hypot(this.vel.x, this.vel.y) < 8) this.vel = null;
      moving = true;
    }
    return moving;
  }

  // The edge of the world can stop a fly-to short of centring (near a pole): call it done.
  _stuck(c) {
    const lon = this.lon, lat = this.lat;
    this.lon = c.lon; this.lat = c.lat; this.clamp();
    const same = Math.abs(this.lon - lon) < 1e-6 && Math.abs(this.lat - lat) < 1e-6;
    this.lon = lon; this.lat = lat;
    return same;
  }
}
