#!/usr/bin/env python3
"""Give any image the GreenPhosphor-CRT wallpaper treatment.

Two modes:
  flat-bg   For sticker-style images on a flat background: the background
            color is keyed out and replaced with the theme's green-black
            CRT ground (vignette + scanlines). The artwork keeps its colors.
            Images with real transparency use their alpha channel directly.
  phosphor  Full green-phosphor monochrome conversion with bloom.

Requires numpy and ImageMagick (`magick`).

Usage:
  ./crtify.py INPUT OUTPUT [--mode flat-bg|phosphor]
              [--bg '#RRGGBB']     # flat-bg: background color (default: top-left pixel)
              [--glow-tint 0.35]   # flat-bg: tint semi-transparent halos toward phosphor
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "wallpaper"))
import crtlib
from crtlib import BRIGHT, LUMA_WEIGHTS, col


def parse_hex(s):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", s)
    if not m:
        raise argparse.ArgumentTypeError(f"not a #RRGGBB color: {s}")
    v = int(m.group(1), 16)
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def key_alpha(img, alpha, bg_rgb):
    """Foreground alpha and un-premultiplied foreground color.

    Images with real transparency provide both directly; otherwise recover
    them by difference keying against the flat background color.
    """
    if alpha.min() < 0.999:
        a = alpha[..., None]
        fg = np.clip(img / np.maximum(a, 1e-4), 0.0, 1.0)  # img is over black
        return a, fg
    bg = col(bg_rgb)[None, None, :]
    dist = np.sqrt(((img - bg) ** 2).sum(axis=2))
    a = (np.clip(dist / 0.28, 0.0, 1.0) ** 1.2)[..., None]
    fg = np.clip((img - (1.0 - a) * bg) / np.maximum(a, 1e-4), 0.0, 1.0)
    return a, fg


def mode_flat_bg(img, alpha, bg_rgb, glow_tint):
    h, w = img.shape[:2]
    scale = w / 3840.0
    a, fg = key_alpha(img, alpha, bg_rgb)

    # Optionally pull bright semi-transparent halos toward phosphor green.
    if glow_tint > 0:
        luma = (fg * LUMA_WEIGHTS).sum(axis=2)[..., None]
        tint = luma * col(BRIGHT)[None, None, :] / 0.7152
        gate = np.clip((luma - 0.35) / 0.3, 0.0, 1.0)
        k = glow_tint * (1.0 - a) * a * 4.0 * gate
        fg = fg * (1.0 - k) + tint * k

    ground, r = crtlib.crt_ground(w, h)
    out = fg * a + ground * (1.0 - a)
    out *= crtlib.scanlines(h, scale)[:, None, None]
    out = crtlib.vignette(out, r)
    # Keep the artwork itself out of the vignette/scanline darkening a bit.
    out = out * (1.0 - a * 0.35) + fg * a * 0.35
    out += crtlib.grain((h, w), np.random.default_rng(crtlib.SEED))[..., None]
    return out


def mode_phosphor(img):
    h, w = img.shape[:2]
    scale = w / 3840.0
    luma = (img * LUMA_WEIGHTS).sum(axis=2)
    luma = luma**0.9

    ground, r = crtlib.crt_ground(w, h)
    ramp = col(BRIGHT)[None, None, :] - col(crtlib.GROUND_DARK)[None, None, :]
    out = ground + luma[..., None] * ramp
    # Hot highlights tip toward white.
    out += np.clip(luma - 0.82, 0, 1)[..., None] * np.array([0.5, 0.55, 0.5], np.float32)
    # Phosphor bloom around bright areas.
    glow = crtlib.blur(np.clip(luma - 0.35, 0, 1), 10 * max(scale, 0.25))
    out += glow[..., None] * col(crtlib.PHOSPHOR)[None, None, :] * 0.25
    out *= crtlib.scanlines(h, scale)[:, None, None]
    out = crtlib.vignette(out, r, strength=0.30)
    out += crtlib.grain((h, w), np.random.default_rng(crtlib.SEED))[..., None]
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--mode", choices=("flat-bg", "phosphor"), default="flat-bg")
    ap.add_argument("--bg", type=parse_hex, default=None)
    ap.add_argument("--glow-tint", type=crtlib.unit_float, default=0.35)
    args = ap.parse_args()

    img, alpha = crtlib.read_image(args.input)
    if args.mode == "flat-bg":
        bg = args.bg or tuple(int(round(v * 255)) for v in img[0, 0])
        out = mode_flat_bg(img, alpha, bg, args.glow_tint)
    else:
        out = mode_phosphor(img)
    crtlib.write_png(out, args.output)
    print(args.output)


if __name__ == "__main__":
    sys.exit(main())
