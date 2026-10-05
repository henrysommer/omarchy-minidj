"""Synthesized one-shot DJ sound effects (no sample files needed)."""

import numpy as np
from scipy.signal import butter, sosfilt

from engine import SR


def _t(seconds):
    return np.arange(int(seconds * SR)) / SR


def _saw(phase):
    return 2.0 * (phase % 1.0) - 1.0


def _phase(freq):
    """Integrate an instantaneous frequency curve (Hz per sample) into a phase in cycles."""
    return np.cumsum(freq) / SR


def _env(n, attack=0.005, release=0.05):
    env = np.ones(n)
    a, r = int(attack * SR), int(release * SR)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def _lowpass(x, cutoff):
    return sosfilt(butter(2, cutoff, fs=SR, output="sos"), x)


def _echo(x, delay=0.3, feedback=0.45, repeats=6):
    d = int(delay * SR)
    out = np.concatenate([x, np.zeros(d * repeats)])
    for k in range(1, repeats + 1):
        out[k * d: k * d + len(x)] += x * feedback ** k
    return out


def _stereo(x, peak=0.8):
    x = x / (np.abs(x).max() + 1e-9) * peak
    return np.stack([x, x], axis=1).astype(np.float32)


def air_horn():
    """Reggae air horn: three blasts, the last one long ("doo-doo-dooooo")."""
    parts = []
    for dur in (0.13, 0.13, 0.7):
        t = _t(dur)
        bend = 1.0 - 0.08 * np.exp(-t / 0.03)  # pitch scoops up into the note
        tone = sum(amp * _saw(_phase(f * bend)) for f, amp in ((415, 1.0), (418, 0.8), (622, 0.5), (830, 0.3)))
        parts += [_lowpass(tone, 3200) * _env(len(t), 0.008, 0.04), np.zeros(int(0.05 * SR))]
    return _stereo(np.concatenate(parts))


def siren():
    """Dub siren: fast up/down wobble that slides down, with a dub echo."""
    t = _t(1.6)
    lfo = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 7 * t))  # square-wave pitch jumps
    freq = (650 + 450 * lfo) * np.linspace(1.0, 0.55, len(t))
    tone = np.sin(2 * np.pi * _phase(freq)) + 0.3 * np.sign(np.sin(2 * np.pi * _phase(freq)))
    return _stereo(_echo(_lowpass(tone, 4000) * _env(len(t), 0.01, 0.15), 0.32, 0.5))


def laser():
    """Three "pew" zaps: exponential pitch dives."""
    parts = []
    for _ in range(3):
        t = _t(0.22)
        freq = 2600 * np.exp(-t * 14) + 120
        parts += [np.sin(2 * np.pi * _phase(freq)) * _env(len(t), 0.002, 0.03), np.zeros(int(0.06 * SR))]
    return _stereo(_echo(np.concatenate(parts), 0.18, 0.3, 3))


def scratch():
    """Vinyl scratch ("wikka-wikka"): a buzzy sample pushed back and forth."""
    rng = np.random.default_rng(7)
    src_t = _t(0.6)
    src = _saw(_phase(np.full(len(src_t), 140.0))) + 0.6 * rng.standard_normal(len(src_t))
    src = _lowpass(src, 2500) * np.hanning(len(src_t))
    t = _t(1.1)
    pos = (0.5 - 0.5 * np.cos(2 * np.pi * 3.5 * t)) * (len(src) - 2)  # hand movement
    speed = np.abs(np.gradient(pos))
    out = np.interp(pos, np.arange(len(src)), src) * np.clip(speed / speed.max() * 3, 0, 1)
    return _stereo(out * _env(len(t), 0.005, 0.05))


# name, key, generator
EFFECTS = [
    ("AIR HORN", "1", air_horn),
    ("SIREN", "2", siren),
    ("LASER", "3", laser),
    ("SCRATCH", "4", scratch),
]


def build():
    return {name: gen() for name, _, gen in EFFECTS}
