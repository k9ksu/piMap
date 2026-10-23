// piMap 2.0: the map, drawn in the browser.
//
//   base canvas    Blue Marble pyramid and night side; redrawn only when the view moves,
//                  a tile arrives, or the grey line advances (every 20 s)
//   lines canvas   borders (borders.js): CPU-drawn, slid with the map while it moves
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
import { Borders } from "./borders.js";
import { Qsos } from "./qsos.js";
import { Targets, STATE_COLORS } from "./targets.js";
import { Chime } from "./chime.js";
import { setupPanels, loadSettings, DEFAULT_SETTINGS, fmtPass } from "./panels.js";

const TV = document.documentElement.classList.contains("tv");
// ?debug=1&off=night,borders,sats,tiles,bright: switch layers off to find what's costly
const QS = new URLSearchParams(location.search);
const OFF = new Set(QS.get("debug") ? (QS.get("off") || "").split(",") : []);
const $ = (id) => document.getElementById(id);
const base = $("base"), over = $("over");
const bctx = base.getContext("2d", { alpha: false }), octx = over.getContext("2d");

let conf = null, view = null, tiles = null, night = null, sats = null, borders = null, qsos = null, targets = null;
const chime = new Chime();
let openingsSeen = -1, targetsAt = 0;
let detail = null;                   // the bubble's drill-down: {id, band, source, data}
let bubbleHover = false, shownKey = null;
// A hovered bubble stays this long after the mouse leaves its dot, so it can be reached.
const HOVER_GRACE_MS = 900;
const LEAVE_GRACE_MS = 250;          // ...but leaving the bubble itself closes it almost at once
const FADE_MS = 180;                 // the fade-out (matches #bubble's CSS transition)
let fadeTimer = 0;
let lastHover = null, graceTimer = 0;
let state = null, home = null, dpr = 1, u = 1;
let baseDirty = true, overAt = 0, tleVersion = -1, tvSeen = null;
let speed = 0, speedAt = 0, changedAt = 0;
let settings = { ...DEFAULT_SETTINGS }, satsVersion = -1, clockSkew = 0;
let pointer = null, pinned = null, panels = null, tvSettingsKey = "";   // pointer over the map (CSS px); the pinned bubble's key
const radio = new Map();             // NORAD -> {lines, note, next_pass, at}          // fastest satellite, device px/s (checked once a second)

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
  qsos = new Qsos({ color: conf.colors.qso, dot_radius: (conf.qso || {}).dot_radius });
  targets = new Targets(conf.targets || {});
  borders = new Borders(conf.boundaries || { enabled: false }, $("lines"));
  if (borders.conf.enabled) {
    getJSON("api/boundaries").then((d) => { borders.load(d.lines); baseDirty = true; }).catch(() => {});
  }
  const iss = new Image(); iss.src = "iss.webp"; iss.onload = () => { overAt = 0; };
  sats = new Sats({ ...conf.sats, iss_width: conf.iss.width, iss_shadow: conf.iss.shadow,
                    iss_shadow_alpha: conf.iss.shadow_alpha, iss_shadow_gray: conf.iss.shadow_gray,
                    footprint: conf.sats.footprint }, iss);

  resize();
  window.addEventListener("resize", resize);
  if (!TV) {
    wireInput();
    wireBubble();
    // the chime: on for the shack screen; on the kiosk only the TV chimes unless a browser opts in
    settings = loadSettings({ chime: !conf.kiosk && (conf.targets ? conf.targets.sound_on : true),
                              volume: conf.targets ? conf.targets.volume : 0.8 });
    applySettings(settings);
    panels = setupPanels({ sats, settings, onSettings: applySettings, onToggleSat: toggleSat,
                  serverNow: () => (Date.now() + clockSkew) / 1000,
                  tv: conf.kiosk ? { get: () => state && state.tv.settings, set: setTvSetting } : null,
                  sound: conf.targets && conf.targets.wspr_on ? {
                    modes: conf.targets.modes, sounds: conf.targets.sounds, get: () => state && state.prefs,
                    set: setPrefs, test: () => { chime.unlock(); chime.play(state.prefs.alert_sound, settings.volume ?? 0.8); },
                  } : null });
  }
  if (!TV && conf.kiosk) wireTvPanel();
  if (QS.get("debug")) window.__pimap = { view, sats, tiles, borders, targets, chime, get settings() { return settings; }, get pinned() { return pinned; }, get lastHover() { return lastHover; }, get pointer() { return pointer; } };   // for measuring and tests
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
  if (borders) borders.resize(base.width, base.height);
  baseDirty = true; overAt = 0;
}

// ---- server state: home, TV view, space weather, status, TLEs -------------------------
async function poll() {
  try { state = await getJSON("api/state"); } catch (e) { return; }
  clockSkew = state.now * 1000 - Date.now();
  if (state.qso_version !== undefined && state.qso_version !== qsos.version) {
    try { qsos.set(await getJSON("api/qsos")); overAt = 0; } catch (e) { /* next poll */ }
  }
  await pollTargets();
  if (state.sats && state.sats.version !== satsVersion) {
    satsVersion = state.sats.version; sats.show = new Set(state.sats.shown); overAt = 0;
  }
  const h = state.home, moved = !home || home.lat !== h.lat || home.lon !== h.lon;
  home = h;
  if (state.tle_version !== tleVersion) {
    try { const t = await getJSON("api/tles"); sats.setTles(t.sats); tleVersion = t.version; overAt = 0; }
    catch (e) { /* next poll */ }
  }
  // the TV draws what the left menu's TV column says; browsers keep their own
  const tvs = JSON.stringify(state.tv.settings || {});
  if (tvs !== tvSettingsKey) {
    tvSettingsKey = tvs;
    if (TV) {
      settings = { ...DEFAULT_SETTINGS, volume: conf.targets ? conf.targets.volume : 0.8, ...state.tv.settings };
      applySettings(settings);
    }
    else if (panels) panels.syncTv(state.tv.settings);
  }
  if (panels && state.prefs) panels.syncPrefs(state.prefs);
  const tvKey = `${state.tv.zoom}|${h.lat}|${h.lon}`;
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
    s.className = v.state; s.title = v.detail || v.state; s.textContent = v.label || k.toUpperCase();
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
  if (viewMoved) changedAt = t;
  if (baseDirty || viewMoved || tiles.busy) drawBase();
  if (!OFF.has("borders")) borders.update(view, dpr, u, t, moving || t - changedAt < 150);
  // satellites: redraw often enough that the fastest moves ~0.2 device px per redraw
  if (t - speedAt > 1000 || viewMoved) { speed = sats.count ? sats.speedPx(view, dpr) : 0; speedAt = t; }
  const every = speed > 0 ? Math.min(1000, Math.max(16, 200 / speed)) : 1000;
  if (viewMoved || moving || targets.pinging || targets.drag || t - overAt >= every) drawOver(t);
  view.changed = false;
  requestAnimationFrame(frame);
}

function drawBase() {
  baseDirty = false;
  bctx.setTransform(1, 0, 0, 1, 0, 0);
  bctx.fillStyle = "#000"; bctx.fillRect(0, 0, base.width, base.height);
  bctx.imageSmoothingEnabled = true; bctx.imageSmoothingQuality = "high";
  if (!OFF.has("tiles")) tiles.draw(bctx, view, dpr);
  if (!OFF.has("bright")) brighten(bctx, conf.map.brightness);
  if (night && settings.night && !OFF.has("night")) night.draw(bctx, view, dpr);
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
  if (settings.qsos !== false && !OFF.has("qsos")) qsos.draw(octx, view, dpr, u);
  else qsos.markers = [];
  if (settings.targets !== false && !OFF.has("targets")) targets.draw(octx, view, dpr, u);
  else targets.markers = [];
  if (!OFF.has("sats")) sats.draw(octx, view, dpr, u);
  octx.restore();
  if (!TV) bubble();
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

// ---- the info bubble (browsers only): piMap's, for the satellite under the mouse -------------
async function radioInfo(norad) {
  const r = radio.get(norad);
  if (r && (r.loading || Date.now() - r.at < 600000)) return;
  radio.set(norad, { ...(r || { lines: [], note: "loading radio data..." }), loading: true, at: Date.now() });
  try {
    const d = await getJSON(`api/sat/${norad}`);
    radio.set(norad, { lines: d.lines, note: d.note, next_pass: d.next_pass, at: Date.now() });
  } catch (e) {
    radio.set(norad, { lines: [], note: "radio data unavailable", at: Date.now() - 540000 });
  }
  overAt = 0;
}

/** The dot under (x, y) device px: a target, QSO or satellite, whichever is nearest. */
function hitAny(x, y) {
  const c = [];
  if (settings.targets !== false) c.push(targets.hit(x, y));
  if (settings.qsos !== false) c.push(qsos.hit(x, y));
  const sm = sats.hit(x, y);
  if (sm) c.push({ ...sm, kind: "sat", key: `sat:${sm.norad}` });
  let best = null, bd = Infinity;
  for (const m of c) { if (!m) continue; const d = Math.hypot(m.x - x, m.y - y); if (d < bd) { best = m; bd = d; } }
  return best;
}

function findMarker(key) {
  if (key.startsWith("sat:")) {
    const m = sats.markers.find((k) => `sat:${k.norad}` === key);
    return m ? { ...m, kind: "sat", key } : null;
  }
  if (key.startsWith("target:")) return targets.markers.find((k) => k.key === key) || null;
  return qsos.markers.find((k) => k.key === key) || null;
}

/** Fade the bubble out (the CSS transition), then hide it. Showing it again cancels this. */
function fadeBubble(el) {
  if (el.hidden || el.classList.contains("fading")) return;
  el.classList.add("fading");
  clearTimeout(fadeTimer);
  fadeTimer = setTimeout(() => {
    el.classList.remove("fading");
    el.hidden = true;
    el.dataset.key = "";                                          // refilled fresh next time
    el.style.minWidth = el.style.minHeight = "";
  }, FADE_MS);
}

function bubble() {
  const el = $("bubble");
  let m = pinned !== null ? findMarker(pinned) : null;
  if (pinned !== null && !m) pinned = null;                   // its dot went away (unchecked, aged out)
  const now = performance.now();
  if (!m && pointer) {
    m = hitAny(pointer.x * dpr, pointer.y * dpr);
    if (m) lastHover = { key: m.key, until: now + HOVER_GRACE_MS };   // keeps it while the mouse crosses to it
  }
  if (!m && bubbleHover && shownKey) m = findMarker(shownKey); // piMap: the cursor is inside the bubble: keep it
  if (!m && lastHover && now < lastHover.until) {
    m = findMarker(lastHover.key);
    clearTimeout(graceTimer);
    graceTimer = setTimeout(() => { overAt = 0; }, lastHover.until - now + 20);   // redraw when the grace ends
  }
  if (!m) {
    shownKey = null; bubbleHover = false; lastHover = null; detail = null;
    fadeBubble(el);
    return;
  }
  if (!detail) el.style.minWidth = el.style.minHeight = "";
  if (detail && (m.kind !== "target" || detail.id !== m.id)) detail = null;
  shownKey = m.key;
  let key, fill;
  if (m.kind === "target") {
    targets.ack(m.id);
    ({ key, fill } = targetBubble(m));
  } else {
    let body, live = null;
    if (m.kind === "sat") {
      radioInfo(m.norad);
      const info = radio.get(m.norad) || { lines: [], note: "loading radio data..." };
      body = info.lines && info.lines.length ? info.lines : [info.note];
      live = `Alt ${Math.round(m.alt)} km   next pass ${fmtPass(info.next_pass, (Date.now() + clockSkew) / 1000)}`;
    } else {
      body = m.lines && m.lines.length ? m.lines : [""];                  // piMap's QSO lines
    }
    key = `${m.key}|${body.join("|")}|${live}`;
    fill = () => {
      const t = document.createElement("div"); t.className = "title"; t.textContent = m.name; el.appendChild(t);
      for (const line of body) { const d = document.createElement("div"); d.textContent = line; el.appendChild(d); }
      if (live) { const d = document.createElement("div"); d.className = "live"; d.textContent = live; el.appendChild(d); }
    };
  }
  key += `|${pinned === m.key}`;
  if (el.dataset.key !== key) {
    el.dataset.key = key;
    el.textContent = "";
    fill();
    el.style.borderColor = m.color === "#000" ? "#d2d2d2" : m.color;      // piMap: black ISS -> light grey
    el.classList.toggle("pinned", pinned === m.key);
    el.classList.toggle("interactive", m.kind === "target");
  }
  if (el.classList.contains("fading")) {                         // back before the fade finished
    clearTimeout(fadeTimer); el.classList.remove("fading");
  }
  el.hidden = false;
  if (bubbleHover) return;                                       // don't move it out from under the mouse
  // piMap's placement: up and to the right of the dot, flipped to stay on screen
  const px = m.x / dpr, py = m.y / dpr, w = el.offsetWidth, h = el.offsetHeight;
  let bx = px + 14, by = py - h - 12;
  if (bx + w > view.w - 4) bx = px - w - 14;
  if (by < 4) by = py + 14;
  if (by + h > view.h - 4) by = Math.max(4, view.h - 4 - h);
  el.style.left = `${Math.max(4, bx)}px`; el.style.top = `${by}px`;
}

const fmtSnr = (v) => (typeof v === "number" ? (v >= 0 ? `+${v}` : `${v}`) : "");

/** piMap's target bubble: grid [field], the field's biggest cities, then FT8 | WSPR per band. */
function targetBubble(m) {
  const t = m.target, d = targets.data, el = $("bubble");
  if (detail) return detailBubble(m);
  const wspr = (d.wspr[t.id] || {}).bands || [], ft8 = d.ft8[t.field] || {};
  const metrosList = d.metros[t.field];
  const bands = [...new Set([...wspr.map((b) => b.band), ...Object.keys(ft8).map(Number)])].sort((a, b) => a - b);
  const byBand = Object.fromEntries(wspr.map((b) => [b.band, b]));
  const rows = bands.map((band) => {
    const w = byBand[band], f = ft8[band];
    const label = (w && w.label) || d.band_labels[String(band)] || `${band}MHz`;
    const fc = f ? [f.state, `${f.stations} sta ${fmtSnr(f.best_snr)}`] : ["none", "--"];
    const wc = w && w.cnt > 0
      ? [w.state, `${w.cnt} sp ${fmtSnr(w.best_snr)}${w.age_min != null ? ` ${Math.round(w.age_min)}m` : ""}`]
      : ["none", "--"];
    return { band, label, fc, wc };
  });
  const key = `${m.key}|${JSON.stringify(rows)}|${JSON.stringify(metrosList)}|${d.ft8_on}`;
  const fill = () => {
    const title = document.createElement("div"); title.className = "title";
    title.textContent = `${t.label}  [${t.field}]`;
    if (!TV) {                                                     // for mice with no middle button, and touch
      const rm = document.createElement("button"); rm.className = "remove"; rm.textContent = "✕ remove";
      rm.dataset.remove = "1"; rm.title = "Remove this target dot";
      title.appendChild(rm);
    }
    el.appendChild(title);
    const mt = metrosList == null ? ["cities loading..."] : metrosList.length ? metrosList.map((c) => `* ${c}`) : ["no major metros"];
    for (const c of mt) { const x = document.createElement("div"); x.className = "metro"; x.textContent = c; el.appendChild(x); }
    const tb = document.createElement("table"); tb.className = "bands";
    const cols = d.ft8_on ? ["ft8", "wspr"] : ["wspr"];
    tb.innerHTML = `<tr><th>band</th>${d.ft8_on ? '<th class="ft8">FT8</th>' : ""}<th class="wspr">WSPR</th></tr>`;
    for (const r of rows) {
      const tr = document.createElement("tr");
      const th = document.createElement("td"); th.className = "band"; th.textContent = r.label; tr.appendChild(th);
      for (const src of cols) {
        const [st, txt] = src === "ft8" ? r.fc : r.wc;
        const td = document.createElement("td"); td.className = "cell";
        td.dataset.band = r.band; td.dataset.source = src;
        const dot = document.createElement("span"); dot.className = "sdot";
        dot.style.background = STATE_COLORS[st === "weak" || st === "none" ? "red" : st] || STATE_COLORS.red;
        td.append(dot, document.createTextNode(txt));
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
    el.appendChild(tb);
    const hint = document.createElement("div"); hint.className = "hint";
    hint.textContent = TV ? "" : "click a cell for stations";
    el.appendChild(hint);
  };
  return { key, fill };
}

/** piMap's drill-down: who is being heard on one band, both directions. */
function detailBubble(m) {
  const el = $("bubble"), d = detail.data || { state: "loading" };
  const key = `${m.key}|detail|${detail.band}|${detail.source}|${d.state}|${(d.hearing || []).length}|${(d.heard_by || []).length}`;
  const fill = () => {
    const head = document.createElement("div"); head.className = "title";
    const back = document.createElement("button"); back.className = "back"; back.textContent = "‹ back";
    back.dataset.back = "1";
    head.append(back, document.createTextNode(`  ${d.label || m.target.label} ${d.band_label || ""} ${detail.source.toUpperCase()}`));
    el.appendChild(head);
    if (d.state !== "ok") {
      const x = document.createElement("div"); x.className = "metro";
      x.textContent = d.state === "error" ? `couldn't fetch: ${d.error || "?"}` : "fetching spots...";
      el.appendChild(x); return;
    }
    const section = (label, list, n) => {
      const h = document.createElement("div"); h.className = "sec";
      h.textContent = `${label} (${list.length} stations, ${n || 0} spots)`; el.appendChild(h);
      if (!list.length) { const x = document.createElement("div"); x.className = "metro"; x.textContent = "nobody"; el.appendChild(x); return; }
      const tb = document.createElement("table"); tb.className = "spots";
      for (const g of list.slice(0, 12)) {
        const tr = document.createElement("tr");
        const extra = g.km ? `${g.km} km` : (g.by ? `by ${g.by}` : "");
        for (const v of [g.sign, g.loc || "", fmtSnr(g.best_snr), extra, `${Math.round(g.age_min || 0)}m`]) {
          const td = document.createElement("td"); td.textContent = v; tr.appendChild(td);
        }
        tb.appendChild(tr);
      }
      el.appendChild(tb);
      if (list.length > 12) { const x = document.createElement("div"); x.className = "hint"; x.textContent = `+${list.length - 12} more`; el.appendChild(x); }
    };
    section("Hearing them", d.hearing || [], d.n_in);
    section("They hear us", d.heard_by || [], d.n_out);
    const f = document.createElement("div"); f.className = "hint";
    f.textContent = `home = ${d.home_scope || ""} round you, last ${d.window_min || ""} min`; el.appendChild(f);
  };
  return { key, fill };
}

async function openDetail(id, band, source) {
  detail = { id, band, source, data: null };
  for (let i = 0; i < 30 && detail && detail.id === id && detail.band === band && detail.source === source; i++) {
    try {
      const d = await getJSON(`api/target-detail?id=${encodeURIComponent(id)}&band=${band}&source=${source}`);
      if (!detail || detail.id !== id || detail.band !== band || detail.source !== source) return;
      detail.data = d; overAt = 0;
      if (d.state !== "loading") return;
    } catch (e) { if (detail) detail.data = { state: "error", error: "server unreachable" }; return; }
    await sleep(1000);
  }
}

function wireBubble() {
  const el = $("bubble");
  el.addEventListener("pointerenter", () => { if (el.classList.contains("interactive")) bubbleHover = true; });
  el.addEventListener("pointerleave", () => {            // out of the bubble: gone almost at once
    bubbleHover = false;
    if (shownKey) lastHover = { key: shownKey, until: performance.now() + LEAVE_GRACE_MS };
    overAt = 0;
  });
  el.addEventListener("click", (e) => {
    const m = shownKey && findMarker(shownKey);
    if (!m || m.kind !== "target") return;
    if (e.target.closest("[data-back]")) { detail = null; el.style.minWidth = el.style.minHeight = ""; overAt = 0; return; }
    if (e.target.closest("[data-remove]")) {
      editTarget({ op: "remove", id: m.id });
      if (pinned === m.key) pinned = null;
      bubbleHover = false; lastHover = null; shownKey = null; fadeBubble(el); overAt = 0;
      return;
    }
    const cell = e.target.closest("td.cell");
    if (cell) {
      // Not pinned: it closes when the mouse leaves. Hold the bubble's size so it doesn't
      // shrink out from under the mouse while the spots load.
      el.style.minWidth = `${el.offsetWidth}px`; el.style.minHeight = `${el.offsetHeight}px`;
      openDetail(m.id, Number(cell.dataset.band), cell.dataset.source); overAt = 0;
    }
  });
}

// ---- input (browsers only) --------------------------------------------------------------
function wireInput() {
  const pts = new Map();               // pointerId -> {x, y}
  let drag = null, pinch = null;
  const at = (e) => { const r = over.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };

  over.addEventListener("contextmenu", (e) => e.preventDefault());
  over.addEventListener("pointerdown", (e) => {
    chime.unlock();                                    // a browser may make sound once it's been clicked
    over.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, at(e));
    if (pts.size === 1) {
      const p = at(e), hit = hitAny(p.x * dpr, p.y * dpr);
      if (e.button === 1) {                            // piMap: middle-click a target dot removes it
        e.preventDefault();
        if (hit && hit.kind === "target") { editTarget({ op: "remove", id: hit.id }); if (pinned === hit.key) pinned = null; }
        return;
      }
      drag = { x: p.x, y: p.y, x0: p.x, y0: p.y, t: performance.now(), vx: 0, vy: 0, button: e.button, moved: false,
               target: e.button === 0 && hit && hit.kind === "target" ? hit : null };
      view.vel = null;
    } else if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) };
      drag = null;
    }
  });
  over.addEventListener("pointermove", (e) => {
    if (e.pointerType === "mouse") { pointer = at(e); overAt = 0; }
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
    if (drag.target) {                                 // piMap: left-drag a target dot moves it
      targets.drag = { id: drag.target.id, lat: Math.max(-89.9, Math.min(89.9, view.latAt(p.y))),
                       lon: Math.max(-180, Math.min(180, view.lonAt(p.x))) };
      drag.x = p.x; drag.y = p.y; drag.t = now; overAt = 0;
      return;
    }
    view.panBy(dx, dy);
    drag.vx = 0.8 * (dx / dt) + 0.2 * drag.vx; drag.vy = 0.8 * (dy / dt) + 0.2 * drag.vy;
    drag.x = p.x; drag.y = p.y; drag.t = now;
  });
  const end = (e) => {
    pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (!drag) return;
    over.classList.remove("dragging");
    if (drag.moved && drag.target && targets.drag) {
      const td = targets.drag;
      editTarget({ op: "move", id: td.id, lat: td.lat, lon: td.lon }).then(() => { targets.drag = null; overAt = 0; });
    } else if (drag.moved) {
      if (performance.now() - drag.t < 80) view.fling(drag.vx, drag.vy);
    } else if (drag.button === 2 && home) {
      view.flyTo(conf.map.min_zoom, home.lon, home.lat);          // piMap: right click = reset view
    } else if (drag.button === 0) {
      // piMap: click a dot to pin/unpin its bubble; click empty map to dismiss
      const p = at(e), m = hitAny(p.x * dpr, p.y * dpr);
      pinned = m ? (pinned === m.key ? null : m.key) : null;
      if (!m) lastHover = null;                                   // a click on empty map closes it at once
      if (e.pointerType !== "mouse") pointer = m ? p : null;      // touch: a tap shows it
      overAt = 0;
    }
    drag = null;
  };
  over.addEventListener("pointerup", end);
  over.addEventListener("dblclick", (e) => {           // piMap: double-click empty map drops a target dot
    if (!conf.targets || !conf.targets.wspr_on) return;
    const p = at(e);
    if (hitAny(p.x * dpr, p.y * dpr)) return;
    const lon = view.lonAt(p.x), lat = view.latAt(p.y);
    if (lon < -180 || lon > 180 || lat < -90 || lat > 90) return;
    editTarget({ op: "add", lat, lon });               // not pinned: its bubble shows on hover, like any dot
  });
  document.addEventListener("keydown", (e) => {        // piMap: Delete/Backspace over a target dot removes it
    if (e.key !== "Delete" && e.key !== "Backspace") return;
    if (e.target && /input|select|textarea/i.test(e.target.tagName)) return;
    const m = pointer && hitAny(pointer.x * dpr, pointer.y * dpr);
    if (m && m.kind === "target") { e.preventDefault(); editTarget({ op: "remove", id: m.id }); if (pinned === m.key) pinned = null; }
  });
  over.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") { pointer = null; overAt = 0; } });
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

// ---- target dots: status, and the chime when a path opens -------------------------------------
async function pollTargets() {
  const ts = state.targets;
  if (!ts || !conf.targets || !conf.targets.wspr_on) return;
  // WSPR re-polls every 5 minutes and FT8 changes all the time: refresh while there are dots
  if (ts.version !== targets.version || (targets.data.targets.length && Date.now() - targetsAt > 15000)) {
    try { targets.set(await getJSON("api/targets")); targetsAt = Date.now(); overAt = 0; $("bubble").dataset.key = ""; }
    catch (e) { /* next poll */ }
  }
  if (openingsSeen < 0) { openingsSeen = ts.openings_seq; return; }     // don't chime for old news
  if (ts.openings_seq > openingsSeen) {
    try {
      const d = await getJSON(`api/openings?after=${openingsSeen}`);
      openingsSeen = d.seq;
      for (const o of d.openings) targets.opened(o.target_id);
      if (d.openings.length && settings.chime) chime.play(state.prefs.alert_sound, settings.volume ?? conf.targets.volume);
      overAt = 0;
    } catch (e) { /* next poll */ }
  }
}

async function editTarget(body) {
  try {
    const r = await getJSON("api/targets", { method: "POST", headers: { "content-type": "application/json" },
                                             body: JSON.stringify(body) });
    if (r.ok) { targets.set(await getJSON("api/targets")); targetsAt = Date.now(); overAt = 0; }
    return r;
  } catch (e) { return { ok: false }; }
}

async function setPrefs(p) {
  try {
    const r = await getJSON("api/prefs", { method: "POST", headers: { "content-type": "application/json" },
                                           body: JSON.stringify(p) });
    if (r.ok) { state.prefs = { spot_mode: r.spot_mode, alert_sound: r.alert_sound }; targetsAt = 0; }
    return r.ok;
  } catch (e) { return false; }
}

// ---- the edge panels' effects ------------------------------------------------------------
function applySettings(st) {
  borders.conf = { ...(conf.boundaries || {}), enabled: !!(conf.boundaries && conf.boundaries.enabled) && st.borders };
  borders.snap = null;                         // redraw (or clear) the borders layer
  sats.tracks = st.tracks;
  sats.footprints = st.footprints && conf.sats.footprint !== false;
  baseDirty = true; overAt = 0;
}

async function setTvSetting(key, on) {
  try {
    const r = await getJSON("api/tv-settings", { method: "POST", headers: { "content-type": "application/json" },
                                                 body: JSON.stringify({ key, on }) });
    if (r.ok) state.tv.settings = r.settings;
    return !!r.ok;
  } catch (e) { return false; }
}

async function toggleSat(norad, on) {
  try {
    const r = await getJSON("api/sats", { method: "POST", headers: { "content-type": "application/json" },
                                          body: JSON.stringify({ norad, on }) });
    if (!r.ok) return false;
    sats.show = new Set(r.shown); overAt = 0;
    return true;
  } catch (e) { return false; }
}

function sleep(ms) { return new Promise((ok) => setTimeout(ok, ms)); }

start();
