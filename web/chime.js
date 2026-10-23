// piMap's alert chimes (alerts.py), rendered in the browser: the same additive synth,
// note for note (soft attack, exponential decay, peak-normalised), played through Web
// Audio when a target path opens. Browsers only make sound after the page has been
// clicked or tapped once; kiosk screens are started with autoplay allowed.
"use strict";

// (start_s, dur_s, [[freq, amp], ...], attack_s, decay_s) per note, from alerts.py
const SOUNDS = {
  "Soft Chime": [[0.00, 0.55, [[523.25, 1.0], [1046.5, 0.18]], 0.012, 0.32],
                 [0.16, 0.68, [[784.0, 1.0], [1568.0, 0.18]], 0.012, 0.40]],
  "Marimba": [[0.00, 0.30, [[440.0, 1.0], [1760.0, 0.5], [2640.0, 0.12]], 0.002, 0.11],
              [0.11, 0.36, [[587.33, 1.0], [2349.3, 0.5], [3524.0, 0.12]], 0.002, 0.13]],
  "Bell": [[0.00, 1.30, [[466.16, 1.0], [932.3, 0.6], [1119.0, 0.4], [1398.5, 0.25], [2098.0, 0.14]], 0.004, 0.85]],
  "Music Box": [[0.00, 0.34, [[1046.5, 1.0], [2093.0, 0.22]], 0.003, 0.17],
                [0.12, 0.42, [[1318.5, 1.0], [2637.0, 0.22]], 0.003, 0.20]],
  "Ascending Triad": [[0.00, 0.30, [[523.25, 1.0], [1046.5, 0.2]], 0.008, 0.22],
                      [0.12, 0.30, [[659.25, 1.0], [1318.5, 0.2]], 0.008, 0.22],
                      [0.24, 0.48, [[783.99, 1.0], [1568.0, 0.2]], 0.008, 0.32]],
};

export const SOUND_NAMES = Object.keys(SOUNDS);

/** alerts._mix: render notes to samples at `rate`, peak-normalised to 0.75, tail tapered. */
export function render(notes, rate) {
  const total = Math.max(...notes.map(([st, du]) => st + du));
  const n = Math.floor(rate * total) + 1, buf = new Float32Array(n);
  for (const [start, dur, partials, attack, decay] of notes) {
    const s0 = Math.floor(rate * start), len = Math.floor(rate * dur);
    for (let i = 0; i < len && s0 + i < n; i++) {
      const t = i / rate, env = Math.min(1, t / attack) * Math.exp(-t / decay);
      let s = 0;
      for (const [f, a] of partials) s += a * Math.sin(2 * Math.PI * f * t);
      buf[s0 + i] += env * s;
    }
  }
  const fs = Math.max(1, Math.floor(rate * 0.012));
  for (let k = 0; k < fs; k++) buf[n - 1 - k] *= k / fs;
  let peak = 1e-6;
  for (const v of buf) peak = Math.max(peak, Math.abs(v));
  const scale = peak > 0.75 ? 0.75 / peak : 1;
  for (let i = 0; i < n; i++) buf[i] = Math.max(-1, Math.min(1, buf[i] * scale));
  return buf;
}

export class Chime {
  constructor() { this.ctx = null; this.cache = new Map(); }

  _ctx() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      this.ctx = new AC();
    }
    if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
    return this.ctx;
  }

  /** Call from any click/tap: lets the browser make sound from then on. */
  unlock() { this._ctx(); }

  play(name = "Soft Chime", volume = 0.8) {
    const ctx = this._ctx();
    if (!ctx || !SOUNDS[name]) return false;
    let buf = this.cache.get(name);
    if (!buf) {
      const data = render(SOUNDS[name], ctx.sampleRate);
      buf = ctx.createBuffer(1, data.length, ctx.sampleRate);
      buf.copyToChannel(data, 0);
      this.cache.set(name, buf);
    }
    const src = ctx.createBufferSource(), gain = ctx.createGain();
    gain.gain.value = Math.max(0, Math.min(1, volume));
    src.buffer = buf; src.connect(gain).connect(ctx.destination); src.start();
    return ctx.state === "running";
  }
}
