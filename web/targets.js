// Target dots, as piMap draws them: a dot per user-placed target, coloured by how open
// the path from home is. WSPR (by dot) and FT8 (by grid field) are judged separately
// on the server; the dot takes whichever is more open (piMap's _merge_summary), and
// "quiet" (nothing heard lately) is a faint white dot with a black ring. A path that
// just opened gets piMap's ping ring for WSPR_ALERT_HIGHLIGHT_SECONDS.
"use strict";

export const STATE_COLORS = { green: "rgb(60,200,90)", yellow: "rgb(235,190,40)", red: "rgb(220,70,60)",
                              quiet: "rgb(210,210,214)" };
const RANK = { quiet: 0, red: 1, yellow: 2, green: 3 };

/** One dot colour from WSPR's summary and FT8's per-band states (piMap's _merge_summary). */
export function mergeSummary(wsprSummary, ft8Bands) {
  let best = wsprSummary in RANK ? wsprSummary : "quiet";
  for (const v of Object.values(ft8Bands || {})) {
    const st = v.state === "weak" || v.state === "none" ? "red" : v.state;
    if ((RANK[st] || 0) > RANK[best]) best = st;
  }
  return best;
}

export class Targets {
  constructor(conf) {
    this.r = conf.radius || 7;
    this.pingS = conf.ping_s ?? 60;
    this.data = { targets: [], wspr: {}, ft8: {}, metros: {} };
    this.version = -1;
    this.ping = new Map();             // target id -> ping until (ms)
    this.markers = [];
    this.drag = null;                  // {id, lat, lon} while a dot is being moved
  }

  set(data) { this.data = data; this.version = data.version; }

  summary(t) {
    const w = (this.data.wspr[t.id] || {}).summary || "quiet";
    return mergeSummary(w, this.data.ft8[t.field] || {});
  }

  opened(id) { if (this.pingS > 0) this.ping.set(id, Date.now() + this.pingS * 1000); }
  ack(id) { this.ping.delete(id); }                      // piMap: hovering a pinged dot acknowledges it
  get pinging() { const now = Date.now(); return [...this.ping.values()].some((t) => t > now); }

  draw(ctx, view, dpr, u) {
    const now = Date.now(), r = this.r * u;
    this.markers = [];
    for (const t0 of this.data.targets) {
      const t = this.drag && this.drag.id === t0.id ? { ...t0, lat: this.drag.lat, lon: this.drag.lon } : t0;
      const x = view.x(t.lon) * dpr, y = view.y(t.lat) * dpr, sum = this.summary(t0);
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
      if (sum === "quiet") {
        ctx.fillStyle = "rgba(255,255,255,.24)"; ctx.fill();
        ctx.lineWidth = 2 * u; ctx.strokeStyle = "#000"; ctx.stroke();
      } else {
        ctx.fillStyle = STATE_COLORS[sum]; ctx.fill();
        ctx.lineWidth = 1 * u; ctx.strokeStyle = "#000"; ctx.stroke();
      }
      const until = this.ping.get(t0.id);
      if (until && now < until) {                        // piMap's ping: expanding, fading ring
        const pulse = 0.5 + 0.5 * Math.sin((now / 1000) * 5);
        const a = ((210 * (1 - pulse)) + 30) / 255;
        ctx.beginPath(); ctx.arc(x, y, r + (4 + 9 * pulse) * u, 0, 2 * Math.PI);
        ctx.lineWidth = 2 * u;
        ctx.strokeStyle = sum === "green" ? `rgba(60,200,90,${a})` : `rgba(255,255,255,${a})`;
        ctx.stroke();
      } else if (until) this.ping.delete(t0.id);
      this.markers.push({ kind: "target", key: `target:${t0.id}`, id: t0.id, target: t0, summary: sum,
                          x, y, hitR: r + 6 * u, color: STATE_COLORS[sum] });
    }
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
