"""Audio alert for WSPR band openings.

Synthesizes a small bank of pleasant chimes at startup (no sound files, no
numpy) and plays the selected one when a target band opens. Entirely
best-effort: if the display session has no working audio device, init() logs
once and play() becomes a no-op.
"""

import array
import math

import pygame

import applog

_log = applog.get_logger("wspr")
_sounds = {}         # name -> pygame.Sound
_current = None      # selected sound name
_ready = False
_volume = 1.0


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------
def _mix(rate, notes, tail_fade=0.012):
    """Render additive-synth notes to a mono 16-bit array.

    notes: list of (start_s, dur_s, [(freq, amp), ...], attack_s, decay_s).
    Each note has a soft attack and an exponential decay for a natural,
    un-clicky sound. The final buffer is peak-normalized."""
    total = max(st + du for (st, du, _p, _a, _d) in notes)
    n_total = int(rate * total) + 1
    buf = [0.0] * n_total
    for (start, dur, partials, attack, decay) in notes:
        s0 = int(rate * start)
        n = int(rate * dur)
        for i in range(n):
            t = i / rate
            env = min(1.0, t / attack) * math.exp(-t / decay)
            s = 0.0
            for f, a in partials:
                s += a * math.sin(2.0 * math.pi * f * t)
            j = s0 + i
            if j < n_total:
                buf[j] += env * s
    # taper the very end so it never clicks
    fs = max(1, int(rate * tail_fade))
    for k in range(fs):
        buf[n_total - 1 - k] *= k / fs
    peak = max(1e-6, max(abs(v) for v in buf))
    scale = 0.75 / peak if peak > 0.75 else 1.0
    out = array.array("h")
    for v in buf:
        out.append(int(max(-1.0, min(1.0, v * scale)) * 32767))
    return out


def _soft_chime(rate):
    return _mix(rate, [
        (0.00, 0.55, [(523.25, 1.0), (1046.5, 0.18)], 0.012, 0.32),
        (0.16, 0.68, [(784.0, 1.0), (1568.0, 0.18)], 0.012, 0.40),
    ])


def _marimba(rate):
    return _mix(rate, [
        (0.00, 0.30, [(440.0, 1.0), (1760.0, 0.5), (2640.0, 0.12)], 0.002, 0.11),
        (0.11, 0.36, [(587.33, 1.0), (2349.3, 0.5), (3524.0, 0.12)], 0.002, 0.13),
    ])


def _bell(rate):
    return _mix(rate, [
        (0.00, 1.30, [(466.16, 1.0), (932.3, 0.6), (1119.0, 0.4),
                      (1398.5, 0.25), (2098.0, 0.14)], 0.004, 0.85),
    ])


def _music_box(rate):
    return _mix(rate, [
        (0.00, 0.34, [(1046.5, 1.0), (2093.0, 0.22)], 0.003, 0.17),
        (0.12, 0.42, [(1318.5, 1.0), (2637.0, 0.22)], 0.003, 0.20),
    ])


def _triad(rate):
    return _mix(rate, [
        (0.00, 0.30, [(523.25, 1.0), (1046.5, 0.2)], 0.008, 0.22),
        (0.12, 0.30, [(659.25, 1.0), (1318.5, 0.2)], 0.008, 0.22),
        (0.24, 0.48, [(783.99, 1.0), (1568.0, 0.2)], 0.008, 0.32),
    ])


# Ordered so the menu shows them consistently.
_BUILDERS = [
    ("Soft Chime", _soft_chime),
    ("Marimba", _marimba),
    ("Bell", _bell),
    ("Music Box", _music_box),
    ("Ascending Triad", _triad),
]
_DEFAULT = _BUILDERS[0][0]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def sound_names():
    return [n for n, _ in _BUILDERS]


def current_sound():
    return _current


def set_sound(name):
    global _current
    if name in _sounds:
        _current = name


def init(enabled=True, devicename=None, volume=1.0, sound=None):
    """Prepare the chime bank. devicename routes output to a specific device;
    volume is 0..1; sound selects the initial chime by name."""
    global _sounds, _current, _ready, _volume
    _ready = False
    _sounds = {}
    _volume = max(0.0, min(1.0, float(volume)))
    _current = sound if sound in dict(_BUILDERS) else _DEFAULT
    if not enabled:
        return
    try:
        if not pygame.mixer.get_init():
            if devicename:
                try:
                    pygame.mixer.init(frequency=44100, size=-16, channels=1,
                                      devicename=devicename)
                except Exception as e:
                    _log.warning("Audio device '%s' failed (%s) — using default",
                                 devicename, e)
                    pygame.mixer.init(frequency=44100, size=-16, channels=1)
            else:
                pygame.mixer.init(frequency=44100, size=-16, channels=1)
        rate = pygame.mixer.get_init()[0]
        for name, builder in _BUILDERS:
            snd = pygame.mixer.Sound(buffer=builder(rate).tobytes())
            snd.set_volume(_volume)
            _sounds[name] = snd
        _ready = True
        _log.info("Audio alert ready (%d chimes, '%s', vol %.0f%%)",
                  len(_sounds), _current, _volume * 100)
    except Exception as e:
        _log.warning("Audio alert unavailable (%s) — openings will be silent", e)


def set_volume(v):
    """Set the chime volume (0..1) for all sounds."""
    global _volume
    _volume = max(0.0, min(1.0, float(v)))
    for snd in _sounds.values():
        try:
            snd.set_volume(_volume)
        except Exception:
            pass


def get_volume():
    return _volume


def play(name=None):
    """Play the selected chime (or a specific one by name, for previews)."""
    snd = _sounds.get(name or _current)
    if _ready and snd is not None:
        try:
            snd.play()
        except Exception:
            pass
