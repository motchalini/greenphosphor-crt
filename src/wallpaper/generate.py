#!/usr/bin/env python3
"""Generate the GreenPhosphor-CRT wallpaper.

Procedural, deterministic (fixed seed), no external assets.
Requires numpy and ImageMagick (`magick`) for the final PNG encode.

The shipped files are rendered with:
  ./generate.py --variant trace --out wallpapers/greenphosphor-crt-trace.png
  ./generate.py --variant plain --out wallpapers/greenphosphor-crt-plain.png

Usage:
  ./generate.py [--width 3840] [--height 2400] [--variant trace|plain] [--out FILE]
"""

import argparse
import sys

import numpy as np

import crtlib
from crtlib import BRIGHT, PHOSPHOR, col


def add_grid(img, w, h, scale):
    """Faint oscilloscope graticule, brighter major lines."""
    minor = max(2, int(round(64 * scale)))
    major = minor * 5
    xm = np.arange(w) % minor == 0
    ym = np.arange(h) % minor == 0
    xM = np.arange(w) % major == 0
    yM = np.arange(h) % major == 0
    minor_mask = (xm[None, :] | ym[:, None]).astype(np.float32)
    major_mask = (xM[None, :] | yM[:, None]).astype(np.float32)
    grid = minor_mask * 0.022 + major_mask * 0.026
    img += grid[..., None] * col(PHOSPHOR)[None, None, :]
    return img


def trace_ys(w, h, phase):
    """A quiet drifting waveform: two gentle sines plus a slow envelope."""
    x = np.arange(w, dtype=np.float32) / w
    env = 0.55 + 0.45 * np.sin(np.pi * x) ** 2
    y = (
        np.sin(2 * np.pi * (x * 2.0 + phase * 0.07))
        + 0.45 * np.sin(2 * np.pi * (x * 5.0 - phase * 0.11) + 1.3)
    )
    return h * (0.615 + 0.075 * env * y)


def add_trace(img, w, h, scale):
    """Phosphor trace with persistence echoes and bloom."""
    yy = np.arange(h, dtype=np.float32)[:, None]
    core_sigma = 2.2 * scale
    intensity = np.zeros((h, w), dtype=np.float32)
    # Newest trace first; older echoes are dimmer (phosphor persistence).
    for phase, strength in ((0.0, 1.0), (1.0, 0.40), (2.0, 0.18), (3.0, 0.08)):
        yc = trace_ys(w, h, phase)[None, :]
        d2 = (yy - yc) ** 2
        intensity += strength * np.exp(-d2 / (2 * core_sigma**2))

    bloom = crtlib.blur(intensity, 14 * scale)
    wide = crtlib.blur(intensity, 55 * scale)

    phos = col(PHOSPHOR)[None, None, :]
    brt = col(BRIGHT)[None, None, :]
    img += np.clip(intensity, 0, 1)[..., None] * brt * 0.85
    # Hot core tips toward white where the beam lingers.
    img += np.clip(intensity - 0.75, 0, 1)[..., None] * np.array(
        [0.35, 0.45, 0.35], dtype=np.float32
    )
    img += bloom[..., None] * phos * 0.30
    img += wide[..., None] * phos * 0.10
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--width", type=crtlib.positive_int, default=3840)
    ap.add_argument("--height", type=crtlib.positive_int, default=2400)
    ap.add_argument("--variant", choices=("trace", "plain"), default="trace")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    w, h = args.width, args.height
    scale = w / 3840.0
    rng = np.random.default_rng(crtlib.SEED)

    img, r = crtlib.crt_ground(w, h)
    img = add_grid(img, w, h, scale)
    if args.variant == "trace":
        img = add_trace(img, w, h, scale)
    img = img * crtlib.scanlines(h, scale)[:, None, None]
    img = crtlib.vignette(img, r)
    img = img + crtlib.grain((h, w), rng)[..., None]

    out = args.out or f"greenphosphor-crt-{args.variant}-{w}x{h}.png"
    crtlib.write_png(img, out)
    print(out)


if __name__ == "__main__":
    sys.exit(main())
