"""Shared palette, math, and image I/O for the GreenPhosphor-CRT wallpaper tools.

Used by src/wallpaper/generate.py and tools/crtify.py so the ground colors,
scanlines, and vignette stay in sync between the shipped wallpapers and
user-converted images.
"""

import subprocess
import sys

import numpy as np

# Theme palette (see README)
GROUND_DARK = (0x05, 0x07, 0x05)
GROUND_CENTER = (0x0B, 0x12, 0x0C)
PHOSPHOR = (0x28, 0xA0, 0x38)
BRIGHT = (0x33, 0xB8, 0x4A)

SEED = 0x28A038

LUMA_WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def col(rgb):
    return np.array(rgb, dtype=np.float32) / 255.0


def positive_int(s):
    import argparse

    v = int(s)
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer: {s}")
    return v


def unit_float(s):
    import argparse

    v = float(s)
    if not 0.0 <= v <= 1.0:
        raise argparse.ArgumentTypeError(f"must be in [0, 1]: {s}")
    return v


def run_magick(args, **kwargs):
    try:
        return subprocess.run(["magick", *args], check=True, **kwargs)
    except FileNotFoundError:
        sys.exit("error: ImageMagick (`magick`) not found")
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        sys.exit(f"error: magick failed{': ' + detail if detail else ''}")


def _parse_ppm(blob):
    if not blob.startswith(b"P6"):
        raise RuntimeError("expected binary PPM from magick")
    # Header: P6 <w> <h> <maxval>, then one whitespace byte before pixel data.
    fields, pos, n = [], 2, len(blob)
    while len(fields) < 3:
        while pos < n and blob[pos : pos + 1].isspace():
            pos += 1
        if pos >= n:
            raise RuntimeError("truncated PPM header")
        if blob[pos : pos + 1] == b"#":
            while pos < n and blob[pos : pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < n and not blob[pos : pos + 1].isspace():
            pos += 1
        fields.append(int(blob[start:pos]))
    pos += 1
    w, h, maxval = fields
    if maxval != 255:
        raise RuntimeError(f"unexpected PPM maxval {maxval} (expected 255)")
    if n - pos < w * h * 3:
        raise RuntimeError("truncated PPM data")
    data = np.frombuffer(blob, dtype=np.uint8, count=w * h * 3, offset=pos)
    return data.reshape(h, w, 3).astype(np.float32) / 255.0


def read_image(path):
    """Read any image as (rgb, alpha) float arrays in [0, 1].

    rgb is premultiplied over black (so rgb = alpha * foreground); alpha is
    all-ones for images without transparency.
    """
    rgb = _parse_ppm(
        run_magick(
            [path, "-background", "black", "-alpha", "remove", "-alpha", "off",
             "-depth", "8", "ppm:-"],
            capture_output=True,
        ).stdout
    )
    alpha = _parse_ppm(
        run_magick(
            [path, "-alpha", "extract", "-depth", "8", "ppm:-"],
            capture_output=True,
        ).stdout
    )[:, :, 0]
    return rgb, alpha


def write_png(img, out_path):
    data = (np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    h, w = data.shape[:2]
    blob = b"P6\n%d %d\n255\n" % (w, h) + data.tobytes()
    run_magick(["ppm:-", "-strip", out_path], input=blob)


def box_blur_axis(img, radius, axis):
    """Single box-blur pass along one axis via cumulative sums."""
    if radius < 1:
        return img
    n = img.shape[axis]
    pad = [(0, 0)] * img.ndim
    pad[axis] = (radius + 1, radius)
    padded = np.pad(img, pad, mode="edge")
    csum = np.cumsum(padded, axis=axis, dtype=np.float32)
    hi = np.take(csum, np.arange(2 * radius + 1, n + 2 * radius + 1), axis=axis)
    lo = np.take(csum, np.arange(0, n), axis=axis)
    return (hi - lo) / (2 * radius + 1)


def blur(img, sigma):
    """Approximate gaussian blur: three box passes per axis."""
    if sigma <= 0:
        return img
    radius = max(1, int(round((np.sqrt(4 * sigma * sigma + 1) - 1) / 2)))
    out = img
    for _ in range(3):
        out = box_blur_axis(out, radius, 0)
        out = box_blur_axis(out, radius, 1)
    return out


def crt_ground(w, h):
    """Dark green-black ground with a CRT-like radial falloff.

    Returns (base image, normalized center distance r) — r feeds vignette().
    """
    nx = (np.arange(w, dtype=np.float32) / w - 0.5) * 2.0
    ny = (np.arange(h, dtype=np.float32) / h - 0.5) * 2.0
    r = np.sqrt(nx[None, :] ** 2 + (ny[:, None] * 1.05) ** 2)
    center_glow = np.clip(1.0 - r, 0.0, 1.0) ** 1.6
    dark = col(GROUND_DARK)
    center = col(GROUND_CENTER)
    base = dark[None, None, :] + (center - dark)[None, None, :] * center_glow[..., None]
    return base, r


def scanlines(h, scale):
    period = max(3.0, 4.0 * scale)
    y = np.arange(h, dtype=np.float32)
    return 1.0 - 0.045 * (0.5 + 0.5 * np.sin(2 * np.pi * y / period))


def vignette(img, r, strength=0.38):
    return img * (1.0 - strength * np.clip(r, 0, 1.35) ** 2.4)[..., None]


def grain(shape, rng):
    return rng.standard_normal(shape).astype(np.float32) * (1.2 / 255.0)
