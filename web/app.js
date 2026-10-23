// piMap 2.0: the map, drawn in the browser.
//
//   base canvas    Blue Marble pyramid + night side; redrawn only when the view moves,
//                  a tile arrives, or the grey line advances (every 20 s)
//   overlay canvas QTH, satellites, scale bar; redrawn when the view moves, or as often
//                  as the fastest satellite needs to move a fifth of a pixel (glide)
//   header         plain HTML text, crisp at any resolution
//
// ?mode=tv (the FieldLab kiosk's TV): no pointer, and the view follows the TV view (a
// zoom set from any browser, always centred on home). Browsers each have their own view.
"use strict";

import { View } from "./view.js";
import { Tiles } from "./tiles.js";
import { Night, subsolar, sunElevation } from "./sky.js";
import { Sats } from "./sats.js";

const TV = document.documentElement.classList.contains("tv");
const $ = (id) => document.getElementById(id);
const base = $("base"), over = $("over");
const bctx = base.getContext("2d", { alpha: false }), octx = over.getContext("2d");

let conf = null, view = null, tiles = null, night = null, sats = null;
let state = null, home = null, dpr = 1, u = 1;
let baseDirty = true, overAt = 0, tleVersion = -1, tvSeen = null;
let speed = 0, speedAt = 0;          // fastest satellite, device px/s (checked once a second)

// ---- start --------------------------------------------------------------------------
async function getJSON(url, opts) {
  const r = await fetch(url, { cache: "no-store", ...opts });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

async function start() {
  for (;;) {
    try { conf = await getJSON("api/config"); break; }
    catch (e) { $("note").textContent = "Waiting for the piMap server…"; await sleep(3000); }
  }
  if (!conf.pyramid) { $("note").textContent = "Map tiles not built yet: run tools/build_pyramid.py"; return; }
  document.title = `piMap · ${conf.callsign}`;
  $("call").textContent = conf.callsign;
  const root = document.documentElement.style;
  root.setProperty("--hdr-font", `"${conf.header.font}"`);
  $("call").style.color = conf.header.color;

  view = new View(conf.map.min_zoom, conf.map.max_zoom);
  tiles = new Tiles(conf.pyramid, { maxTiles: TV ? 140 : 180, onLoad: () => { baseDirty = true; } });
  night = conf.map.greyline ? new Night(conf.map.night_alpha) : null;
  const iss = new Image(); iss.src = "iss.webp"; iss.onload = () => { overAt = 0; };
  sats = new Sats({ ...conf.sats, iss_width: conf.iss.width, iss_shadow: conf.iss.shadow,
                    iss_shadow_alpha: conf.iss.shadow_alpha, iss_shadow_gray: conf.iss.shadow_gray,
                    footprint: conf.sats.footprint }, iss);

  resize();
  window.addEventListener("resize", resize);
  if (!TV) wireInput();
  if (!TV && conf.kiosk) wireTvPanel();
  await poll();
  setInterval(poll, 5000);
  setInterval(clock, 250);
  requestAnimationFrame(frame);
}

function resize() {
  dpr = window.devicePixelRatio || 1;
  const H = Math.round(window.innerHeight * (conf ? conf.header.bar_fraction : 0.13));
  document.documentElement.style.setProperty("--H", `${H}px`);
  if (conf) $("call").style.fontSize = `${(H * conf.header.fill) / 0.72}px`;
  const box = $("mapbox").getBoundingClientRect();
  for (const c of [base, over]) { c.width = Math.round(box.width * dpr); c.height = Math.round(box.height * dpr); }
  u = (window.innerHeight * dpr) / 1080;           // piMap's 1080p pixel, in device px
  if (view) view.resize(box.width, box.height);
  baseDirty = true; overAt = 0;
}

// ---- server state: home, TV view, space weather, status, TLEs -------------------------
async function poll() {
  try { state = await getJSON("api/state"); } catch (e) { return; }
  const h = state.home, moved = !home || home.lat !== h.lat || home.lon !== h.lon;
  home = h;
  if (state.tle_version !== tleVersion) {
    try { const t = await getJSON("api/tles"); sats.setTles(t.sats); tleVersion = t.version; overAt = 0; }
    catch (e) { /* next poll */ }
  }
  const tvKey = `${state.tv.changed}|${h.lat}|${h.lon}`;
  if (tvSeen === null) {
    view.set(state.tv.zoom, h.lon, h.lat);          // everyone opens on the TV view
  } else if (TV && (tvKey !== tvSeen || moved)) {
    view.flyTo(state.tv.zoom, h.lon, h.lat);
  }
  tvSeen = tvKey;
  if (!TV && conf.kiosk) showTvZoom(state.tv.zoom);
  header();
}

const BAND_ORDER = ["80m-40m", "30m-20m", "17m-15m", "12m-10m"];

function header() {
  if (!state) return;
  const sw = state.space_weather;
  $("sfi").textContent = sw.SFI || "?"; $("kp").textContent = sw.Kp || "?";
  $("aidx").textContent = sw.A || "?"; $("ssn").textContent = sw.Sunspots || "?";
  $("geomag").textContent = `Geomag: ${sw.Geomag || "Unknown"}`;
  const tags = $("tags"); tags.textContent = "";
  for (const [k, v] of Object.entries(state.status)) {
    const s = document.createElement("span");
    s.className = v.state; s.title = v.detail || v.state; s.textContent = k.toUpperCase();
    tags.appendChild(s);
  }
  if (conf.header.band_conditions && home) {
    const day = sunElevation(subsolar(new Date()), home.lat, home.lon) > -0.833;
    const bands = sw.Bands || {}, el = $("bands");
    el.textContent = `HF ${day ? "Day" : "Night"}:`;
    for (const b of BAND_ORDER) {
      const r = (bands[b] || {})[day ? "day" : "night"] || "--";
      const s = document.createElement("span");
      s.className = r; s.textContent = ` ${b.replace(/m/g, "")} ${r}`;
      el.appendChild(s);
    }
  }
}

function clock() {
  const now = new Date();
  const two = (n) => String(n).padStart(2, "0");
  $("utc").textContent = `${two(now.getUTCHours())}:${two(now.getUTCMinutes())} UTC`;
  let local;
  if (home && home.tz) {
    const f = new Intl.DateTimeFormat("en-US", { timeZone: home.tz, hour: "2-digit", minute: "2-digit",
      second: "2-digit", hourCycle: "h23", timeZoneName: "short" }).formatToParts(now);
    const g = (t) => (f.find((p) => p.type === t) || {}).value || "";
    local = `${g("hour")}:${g("minute")}:${g("second")} ${g("timeZoneName")}`;
  } else {
    const off = home ? Math.round(home.lon / 15) : 0;          // no zone database: by longitude
    const t = new Date(now.getTime() + off * 3600000);
    local = `${two(t.getUTCHours())}:${two(t.getUTCMinutes())}:${two(t.getUTCSeconds())} UTC${off >= 0 ? "+" : "−"}${Math.abs(off)}`;
  }
  $("local").textContent = local;
  if (view) $("zoomlbl").textContent = `Zoom ${view.zoom.toFixed(1)}x`;
}

// ---- drawing --------------------------------------------------------------------------
let last = performance.now();

function frame(t) {
  const dt = Math.min(0.1, (t - last) / 1000); last = t;
  const moving = view.step(dt);
  if (night && night.update()) baseDirty = true;
  const viewMoved = view.changed;
  if (baseDirty || viewMoved || tiles.busy) drawBase();
  // satellites: redraw often enough that the fastest moves ~0.2 device px per redraw
  if (t - speedAt > 1000 || viewMoved) { speed = sats.count ? sats.speedPx(view, dpr) : 0; speedAt = t; }
  const every = speed > 0 ? Math.min(1000, Math.max(16, 200 / speed)) : 1000;
  if (viewMoved || moving || t - overAt >= every) drawOver(t);
  view.changed = false;
  requestAnimationFrame(frame);
}

function drawBase() {
  baseDirty = false;
  bctx.setTransform(1, 0, 0, 1, 0, 0);
  bctx.fillStyle = "#000"; bctx.fillRect(0, 0, base.width, base.height);
  bctx.imageSmoothingEnabled = true; bctx.imageSmoothingQuality = "high";
  tiles.draw(bctx, view, dpr);
  brighten(bctx, conf.map.brightness);
  if (night) night.draw(bctx, view, dpr);
  $("note").hidden = true;
}

// piMap's MAP_BRIGHTNESS (overlays._apply_map_brightness): above 1 it adds a flat grey,
// which lifts the dark oceans so the night side shows against them; below 1 it multiplies.
function brighten(ctx, b) {
  if (!b || Math.abs(b - 1) < 1e-6) return;
  ctx.save();
  if (b > 1) {
    const add = Math.min(120, Math.max(0, Math.round(255 * (b - 1) * 0.8)));
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = `rgb(${add},${add},${add})`;
  } else {
    const m = Math.round(255 * Math.max(0, b));
    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = `rgb(${m},${m},${m})`;
  }
  const L = Math.max(0, view.x(-180) * dpr), T = Math.max(0, view.y(90) * dpr);
  const R = Math.min(base.width, view.x(180) * dpr), B = Math.min(base.height, view.y(-90) * dpr);
  ctx.fillRect(L, T, R - L, B - T);            // the world only: the margins stay black
  ctx.restore();
}

function drawOver(t) {
  overAt = t;
  octx.setTransform(1, 0, 0, 1, 0, 0);
  octx.clearRect(0, 0, over.width, over.height);
  if (home) {
    octx.beginPath();
    octx.arc(view.x(home.lon) * dpr, view.y(home.lat) * dpr, 5 * u, 0, 2 * Math.PI);
    octx.fillStyle = conf.colors.qth; octx.fill();
  }
  octx.save();                                  // keep satellite drawings on the world
  const L = view.x(-180) * dpr, T = view.y(90) * dpr;
  octx.beginPath(); octx.rect(L, T, view.x(180) * dpr - L, view.y(-90) * dpr - T); octx.clip();
  sats.draw(octx, view, dpr, u);
  octx.restore();
  scaleBar();
}

function niceNumber(x) {
  const p = Math.pow(10, Math.floor(Math.log10(x))), f = x / p;
  return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p;
}

function scaleBar() {
  const kmPerPx = 40075 / (360 * view.scale * dpr);           // at the equator, as piMap
  const km = niceNumber(180 * u * kmPerPx), len = km / kmPerPx;
  const pad = 16 * u, ph = 44 * u, pw = len + 2 * 12 * u;
  const x = pad + 20 * u, y = over.height - pad - ph;
  octx.fillStyle = "rgba(120,124,130,.55)";
  octx.beginPath(); octx.roundRect(x, y, pw, ph, 6 * u); octx.fill();
  octx.fillStyle = "#e8e8e8"; octx.font = `${15 * u}px "Courier New", monospace`;
  octx.textAlign = "center"; octx.textBaseline = "top";
  octx.fillText(`${km.toLocaleString("en-US")} km`, x + pw / 2, y + 6 * u);
  const by = y + ph - 10 * u, bx = x + 12 * u;
  octx.strokeStyle = "#e8e8e8"; octx.lineWidth = 2 * u;
  octx.beginPath();
  octx.moveTo(bx, by); octx.lineTo(bx + len, by);
  for (const f of [0, 0.5, 1]) { octx.moveTo(bx + f * len, by - 5 * u); octx.lineTo(bx + f * len, by + 1 * u); }
  octx.stroke();
}

// ---- input (browsers only) --------------------------------------------------------------
function wireInput() {
  const pts = new Map();               // pointerId -> {x, y}
  let drag = null, pinch = null;
  const at = (e) => { const r = over.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };

  over.addEventListener("contextmenu", (e) => e.preventDefault());
  over.addEventListener("pointerdown", (e) => {
    over.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, at(e));
    if (pts.size === 1) {
      const p = at(e);
      drag = { x: p.x, y: p.y, x0: p.x, y0: p.y, t: performance.now(), vx: 0, vy: 0, button: e.button, moved: false };
      view.vel = null;
    } else if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) };
      drag = null;
    }
  });
  over.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    const p = at(e);
    pts.set(e.pointerId, p);
    if (pinch && pts.size === 2) {
      const [a, b] = [...pts.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch.d > 0) {
        view.zoomAt(d / pinch.d, (a.x + b.x) / 2, (a.y + b.y) / 2);
        view.step(1);                  // pinch follows the fingers exactly
      }
      pinch.d = d;
      return;
    }
    if (!drag) return;
    const now = performance.now(), dx = p.x - drag.x, dy = p.y - drag.y, dt = Math.max(1, now - drag.t) / 1000;
    if (!drag.moved && Math.hypot(p.x - drag.x0, p.y - drag.y0) < 6) return;     // piMap's drag threshold
    drag.moved = true; over.classList.add("dragging");
    view.panBy(dx, dy);
    drag.vx = 0.8 * (dx / dt) + 0.2 * drag.vx; drag.vy = 0.8 * (dy / dt) + 0.2 * drag.vy;
    drag.x = p.x; drag.y = p.y; drag.t = now;
  });
  const end = (e) => {
    pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (!drag) return;
    over.classList.remove("dragging");
    if (drag.moved) {
      if (performance.now() - drag.t < 80) view.fling(drag.vx, drag.vy);
    } else if (drag.button === 2 && home) {
      view.flyTo(conf.map.min_zoom, home.lon, home.lat);          // piMap: right click = reset view
    }
    drag = null;
  };
  over.addEventListener("pointerup", end);
  over.addEventListener("pointercancel", end);
  over.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = at(e);
    const px = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
    const step = Math.log(conf.map.zoom_step || 1.15);           // one mouse notch = one piMap step
    view.zoomAt(Math.exp((-px / 100) * step), p.x, p.y);
  }, { passive: false });
}

// ---- the TV view panel (browsers only) --------------------------------------------------
function showTvZoom(z) {
  $("tvzoom").textContent = `${z.toFixed(1)}×`;
  $("tvout").disabled = z <= conf.map.min_zoom + 1e-6;
  $("tvin").disabled = z >= conf.map.max_zoom - 1e-6;
  $("tvview").hidden = false;
}

function wireTvPanel() {
  const send = async (zoom) => {
    try {
      const r = await getJSON("api/tv-view", { method: "POST", headers: { "content-type": "application/json" },
                                               body: JSON.stringify({ zoom }) });
      if (r.ok) { state.tv.zoom = r.zoom; showTvZoom(r.zoom); }
    } catch (e) { /* server restarting */ }
  };
  const step = () => conf.map.zoom_step || 1.15;
  $("tvin").addEventListener("click", () => send(state.tv.zoom * step()));
  $("tvout").addEventListener("click", () => send(state.tv.zoom / step()));
  $("tvmine").addEventListener("click", () => send(view.zoom));
}

function sleep(ms) { return new Promise((ok) => setTimeout(ok, ms)); }

start();
