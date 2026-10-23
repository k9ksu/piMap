// piMap's edge flyouts, in HTML: touch the right edge for the satellite list, the left
// edge for settings. They slide out on hover and close when the mouse leaves (after a
// short grace) or sits still for a while, as piMap's do. Touch screens get small tabs
// at the edges instead of hover. Never shown on the kiosk TV.
"use strict";

const EDGE_PX = 8;            // piMap's hot zone
const LEAVE_MS = 800;         // grace after the pointer leaves a panel
const IDLE_MS = 10000;        // close after this long with no pointer movement

const SETTINGS_KEY = "pimap2.settings";
export const DEFAULT_SETTINGS = { night: true, borders: true, tracks: true, footprints: true };

export function loadSettings() {
  try { return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") }; }
  catch (e) { return { ...DEFAULT_SETTINGS }; }
}

function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch (e) { /* private window: just this visit */ }
}

/** piMap's next-pass label: "now", "12m", "1h 05m", "--". */
export function fmtPass(passUnix, nowUnix) {
  if (passUnix == null) return "--";
  const s = passUnix - nowUnix;
  if (s <= 0) return "now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${Math.max(1, m)}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

class Flyout {
  constructor(el, side, tab) {
    this.el = el; this.side = side; this.tab = tab;
    this.open = false; this.leftAt = 0; this.onOpen = () => {};
    el.addEventListener("pointerenter", () => { this.leftAt = 0; });
    el.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") this.leftAt = performance.now(); });
    tab.addEventListener("click", () => this.set(!this.open));
  }
  set(open) {
    if (open === this.open) return;
    this.open = open; this.leftAt = 0;
    this.el.classList.toggle("open", open);
    this.tab.classList.toggle("open", open);
    if (open) this.onOpen();
  }
}

export function setupPanels({ sats, settings, onSettings, serverNow, onToggleSat }) {
  const left = new Flyout(document.getElementById("setpanel"), "left", document.getElementById("settab"));
  const right = new Flyout(document.getElementById("satpanel"), "right", document.getElementById("sattab"));
  let lastMove = performance.now();

  document.addEventListener("pointermove", (e) => {
    lastMove = performance.now();
    if (e.pointerType !== "mouse") return;
    const w = window.innerWidth;
    if (e.clientX <= EDGE_PX) { left.set(true); left.leftAt = 0; }
    if (e.clientX >= w - 1 - EDGE_PX) { right.set(true); right.leftAt = 0; }
  });
  setInterval(() => {
    const now = performance.now();
    for (const f of [left, right]) {
      if (!f.open) continue;
      if ((f.leftAt && now - f.leftAt > LEAVE_MS) || now - lastMove > IDLE_MS) f.set(false);
    }
  }, 200);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { left.set(false); right.set(false); } });

  // ---- settings (left) ----
  const box = document.getElementById("setlist");
  const items = [
    ["night", "Night shading (grey line)"],
    ["borders", "Country / state borders"],
    ["tracks", "Satellite tracks"],
    ["footprints", "Satellite footprints"],
  ];
  box.textContent = "";
  for (const [key, label] of items) {
    const row = document.createElement("label");
    row.className = "row";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = !!settings[key];
    cb.addEventListener("change", () => { settings[key] = cb.checked; saveSettings(settings); onSettings(settings); });
    const t = document.createElement("span"); t.textContent = label;
    row.append(cb, t); box.appendChild(row);
  }

  // ---- satellites (right) ----
  const list = document.getElementById("satlist"), sub = document.getElementById("satsub");
  let rows = [], shown = new Set(), rowEls = new Map();

  async function refresh() {
    try {
      const r = await fetch("api/sats", { cache: "no-store" });
      const d = await r.json();
      rows = d.sats; shown = new Set(d.shown);
      sub.textContent = `next pass above ${d.min_elevation}° over home`;
      build();
    } catch (e) { sub.textContent = "satellite list unavailable"; }
  }

  function build() {
    list.textContent = ""; rowEls.clear();
    if (!rows.length) { list.innerHTML = '<p class="empty">No satellites yet (waiting for TLEs).</p>'; return; }
    for (const s of rows) {
      const row = document.createElement("label");
      row.className = "row sat";
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = shown.has(s.norad);
      cb.addEventListener("change", async () => {
        cb.disabled = true;
        const ok = await onToggleSat(s.norad, cb.checked);
        if (!ok) cb.checked = !cb.checked;
        cb.disabled = false;
      });
      const chip = document.createElement("span");
      chip.className = "chip" + (s.norad === 25544 ? " iss" : "");
      chip.style.background = sats.colorOf(s.norad);
      const name = document.createElement("span"); name.className = "name"; name.textContent = s.name;
      const when = document.createElement("span"); when.className = "when";
      row.append(cb, chip, name, when);
      list.appendChild(row);
      rowEls.set(s.norad, { when, pass: s.next_pass });
    }
    tick();
  }

  function tick() {
    const now = serverNow();
    for (const { when, pass } of rowEls.values()) when.textContent = fmtPass(pass, now);
  }

  right.onOpen = refresh;
  setInterval(() => { if (right.open) tick(); }, 1000);
  setInterval(() => { if (right.open) refresh(); }, 60000);

  return { refresh };          // the list is rebuilt each time the panel opens
}
