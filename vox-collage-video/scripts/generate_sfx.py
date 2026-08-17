"""
UNUSED as of the current pipeline — superseded by the official
`@remotion/sfx` package (real one-shot library, see SKILL.md step 5).
Left here in case a future video needs a custom sound the official
package doesn't have; don't reach for this by default.

Synthesize a small set of one-shot SFX locally (no sample library, no API).

    python generate_sfx.py public/sfx

Writes 11 short .wav files to the given directory. Keep playback volumes in
Remotion low (0.3-0.55) — these are texture under the narration, not
competing with it.

  whoosh.wav  - fast filtered-noise sweep. Pairs with: rise, zoom-through.
  pop.wav     - short sine burst with a fast pitch drop. Pairs with: punch,
                a chip/tag popping in.
  coin.wav    - two-tone bright chime. Pairs with: a number/stat landing.
  thud.wav    - low sine thump with fast decay. Pairs with: grow, wobble-drop.
  boing.wav   - vibrato'd frequency sweep. Pairs with: unfold, spiral.
  swipe.wav   - high-passed noise burst. Pairs with: flip, peel.
  click.wav   - very short noise tick. Pairs with: a tag/label appearing.
  riser.wav   - rising tone + noise over ~0.6s. Pairs with: building tension
                into a punch-phrase reveal.
  drop.wav    - falling tone, the inverse of riser. Pairs with: a stat/fact
                landing hard.
  shatter.wav - noise burst with a short metallic ring. Pairs with: shatter
                entrance variant.
  paper.wav   - crinkly filtered noise. Pairs with: paper-texture beats,
                a support element sliding in.
"""

import os
import sys

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter

SR = 44100


def _norm(x, peak=0.9):
    m = np.max(np.abs(x)) or 1.0
    return (x / m) * peak


def _fade(x, in_s=0.005, out_s=0.03):
    n_in = max(1, int(in_s * SR))
    n_out = max(1, int(out_s * SR))
    env = np.ones_like(x)
    env[:n_in] *= np.linspace(0, 1, n_in)
    env[-n_out:] *= np.linspace(1, 0, n_out)
    return x * env


def _bandpass(x, low, high, order=4):
    nyq = SR / 2
    b, a = butter(order, [max(low, 1) / nyq, min(high, nyq - 1) / nyq], btype="band")
    return lfilter(b, a, x)


def _highpass(x, cutoff, order=4):
    nyq = SR / 2
    b, a = butter(order, cutoff / nyq, btype="high")
    return lfilter(b, a, x)


def _lowpass(x, cutoff, order=4):
    nyq = SR / 2
    b, a = butter(order, cutoff / nyq, btype="low")
    return lfilter(b, a, x)


def _t(dur):
    return np.linspace(0, dur, int(SR * dur), endpoint=False)


def whoosh():
    dur = 0.45
    t = _t(dur)
    noise = np.random.randn(len(t))
    sweep_center = np.linspace(400, 3500, len(t))
    out = np.zeros_like(t)
    # Sweep a narrow bandpass window across the noise by chunking.
    chunk = 1024
    for i in range(0, len(t), chunk):
        seg = noise[i:i + chunk]
        if len(seg) < 8:
            continue
        c = sweep_center[i]
        out[i:i + chunk] = _bandpass(seg, c * 0.7, c * 1.3)
    return _fade(_norm(out), 0.01, 0.15)


def pop():
    dur = 0.12
    t = _t(dur)
    freq = 900 * np.exp(-t * 18)
    x = np.sin(2 * np.pi * freq * t)
    return _fade(_norm(x), 0.002, 0.08)


def coin():
    dur = 0.25
    t = _t(dur)
    x = np.sin(2 * np.pi * 1568 * t) * np.exp(-t * 9)
    x += 0.6 * np.sin(2 * np.pi * 2093 * t) * np.exp(-(t - 0.05).clip(0) * 9)
    return _fade(_norm(x), 0.002, 0.15)


def thud():
    dur = 0.3
    t = _t(dur)
    freq = 90 * np.exp(-t * 4)
    x = np.sin(2 * np.pi * freq * t) * np.exp(-t * 10)
    x += 0.2 * np.random.randn(len(t)) * np.exp(-t * 30)
    return _fade(_norm(x), 0.002, 0.2)


def boing():
    dur = 0.4
    t = _t(dur)
    vibrato = 1 + 0.15 * np.sin(2 * np.pi * 22 * t)
    freq = 220 * np.exp(-t * 2.2) * vibrato
    phase = 2 * np.pi * np.cumsum(freq) / SR
    x = np.sin(phase) * np.exp(-t * 3)
    return _fade(_norm(x), 0.005, 0.2)


def swipe():
    dur = 0.2
    t = _t(dur)
    noise = np.random.randn(len(t))
    x = _highpass(noise, 2500)
    env = np.exp(-((t - 0.05) ** 2) / (2 * 0.03 ** 2))
    return _fade(_norm(x * env), 0.005, 0.08)


def click():
    dur = 0.04
    t = _t(dur)
    noise = np.random.randn(len(t))
    x = _bandpass(noise, 1500, 6000)
    return _fade(_norm(x), 0.001, 0.03)


def riser():
    dur = 0.6
    t = _t(dur)
    freq = np.linspace(150, 1400, len(t))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    tone = np.sin(phase)
    noise = _highpass(np.random.randn(len(t)), 800)
    env = np.linspace(0.05, 1, len(t))
    x = (0.7 * tone + 0.4 * noise) * env
    return _fade(_norm(x), 0.02, 0.05)


def drop():
    dur = 0.5
    t = _t(dur)
    freq = np.linspace(1200, 80, len(t))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    x = np.sin(phase) * np.exp(-t * 2.5)
    return _fade(_norm(x), 0.005, 0.2)


def shatter():
    dur = 0.5
    t = _t(dur)
    noise = np.random.randn(len(t)) * np.exp(-t * 9)
    burst = _highpass(noise, 3000)
    ring = np.sin(2 * np.pi * 2800 * t) * np.exp(-t * 12) * 0.4
    ring += np.sin(2 * np.pi * 4200 * t) * np.exp(-t * 14) * 0.3
    x = burst + ring
    return _fade(_norm(x), 0.001, 0.25)


def paper():
    dur = 0.35
    t = _t(dur)
    noise = np.random.randn(len(t))
    x = _bandpass(noise, 1000, 5000)
    crinkle = np.random.rand(len(t)) > 0.985
    x = x * (0.5 + 0.5 * np.convolve(crinkle, np.ones(200), mode="same"))
    return _fade(_norm(x), 0.01, 0.15)


GENERATORS = {
    "whoosh": whoosh, "pop": pop, "coin": coin, "thud": thud, "boing": boing,
    "swipe": swipe, "click": click, "riser": riser, "drop": drop,
    "shatter": shatter, "paper": paper,
}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python generate_sfx.py <output-dir>")
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    for name, gen in GENERATORS.items():
        x = gen()
        path = os.path.join(out_dir, f"{name}.wav")
        wavfile.write(path, SR, (x * 32767).astype(np.int16))
        print(f"wrote {path}")
