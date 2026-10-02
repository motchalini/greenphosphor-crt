#!/usr/bin/env python3
"""phosphor-window -- a quiet window and a resident rabbit, for a terminal pane.

An ambient TUI for an otherwise idle Tilix pane: a window onto a small
landscape that follows the real weather and time of day (sun, the moon in
its actual phase, stars, clouds, rain, snow, fog, thunder), a wall clock,
and a rabbit that lives on the floor in front of the window.

Design rules
------------
* Nothing here asks for attention. The rabbit has no needs, meters or
  counters and nothing decays if you ignore it; it just does rabbit things
  chosen from the time of day, the weather and chance. The only text on
  screen is the clock, the date and the temperature.
* Monochrome green, 24-bit colour only: the Green Phosphor Tilix scheme maps
  all 16 ANSI colours to greens, so indexed colours are meaningless. No
  colour is ever brighter than PH, PH itself is reserved for the moon, and
  the background is never painted (Tilix's transparency shows through).
* Glyphs are limited to ones that occupy exactly one cell in Cica: ASCII,
  the block elements U+2580-U+259F (VTE draws those itself, so they fill the
  cell exactly) and two small marks (middle dot, degree sign) whose Cica
  outlines stay inside a half-width cell. Geometric shapes and stars from
  the East Asian Ambiguous range are avoided because Cica draws several of
  them full-width.
* Pictures are drawn on a pixel canvas of 2x2 pixels per cell, rendered
  with the quadrant block elements. A Cica cell is 1:1.72, so a pixel is
  0.5 x 0.86 cell widths; shapes are laid out in physical units so circles
  stay round. A cell has one foreground colour and no background, so it
  takes the brightest of its four pixels.
* Output is a cell grid; each tick writes only the cells that changed, in a
  single write(). Still scenes tick once a second; rain, hops and the like
  run at 4-8 fps.

Usage
-----
    phosphor-window.py                  resident pane; q drops to a shell
    phosphor-window.py --once           one frame with colour, then exit
    phosphor-window.py --dump 75x45     one frame as plain text, then exit

    --weather clear|clouds|rain|snow|fog|thunder   fix the weather (no network)
    --time HH:MM / --date YYYY-MM-DD               fix the clock
    --offline                                      never touch the network
    --seed N                                       fix the landscape and rabbit
    --rabbit sit|hop|groom|eat|look|loaf|sleep|binky|flop|front

Weather comes from Open-Meteo when ~/.config/phosphor-window/config.json
holds {"latitude": .., "longitude": .., "timezone": ".."}. Without it nothing
is fetched and the sky is simply clear. Failures are silent by design.
"""
import argparse
import functools
import json
import math
import os
import random
import re
import select
import signal
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - non-POSIX
    termios = tty = None

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------
# The same phosphor ramp as mission-control's mc-tui. Levels are floats on a
# 0..3 scale: 0 = FAINT, 1 = DIM, 2 = MID, 3 = PH. Every channel rises
# monotonically along the ramp, so clamping the level clamps the colour:
# nothing can come out brighter than PH or darker than FAINT.
PH = (0x35, 0xE0, 0x65)
PH_MID = (0x2A, 0xA5, 0x52)
PH_DIM = (0x16, 0x63, 0x2F)
PH_FAINT = (0x0D, 0x3F, 0x1E)
RAMP = (PH_FAINT, PH_DIM, PH_MID, PH)


@functools.lru_cache(maxsize=256)
def _shade_q(q):
    v = q / 16.0
    i = min(int(v), 2)
    f = v - i
    a, b = RAMP[i], RAMP[i + 1]
    return tuple(int(round(a[k] + (b[k] - a[k]) * f)) for k in range(3))


def shade(v):
    """Level (0..3) -> RGB, quantised to 1/16 steps so colours stay few."""
    if v != v or v <= 0:  # NaN or below the floor
        return RAMP[0]
    if v >= 3:
        return RAMP[3]
    return _shade_q(int(round(v * 16)))


# Scene brightness levels by day. At night most of them are scaled down by
# Sky.nf (see below); the moon and stars are the night's own lights.
LV_FRAME = 1.05      # window frame
LV_SILL = 1.25
LV_CLOCK = 1.0
LV_INFO = 0.45       # date / temperature line
LV_FLOOR = 0.40
LV_HAY = 0.95
LV_CUSHION = 0.62
LV_RABBIT = 1.55
LV_RABBIT_RIM = 1.95
LV_RABBIT_FAR = 1.05
LV_SUN = 2.0
LV_MOON = 2.45
LV_MOON_DARK = 0.0
LV_FLASH = 2.0       # thunder: clouds light up to about PH_MID, briefly

CELL_ASPECT = 1.72          # cell width : height in Cica (mission-control mcopen.py)
PIX_H = CELL_ASPECT / 2.0   # pixel height in cell widths (two pixel rows per cell)

DEFAULT_SEED = 3
SAFE_EXTRA = "\u00b7\u00b0"  # middle dot, degree sign: half-width in Cica


# --------------------------------------------------------------------------
# display width (EAW) -- same rules as mc-tui
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1024)
def char_width(ch):
    """Terminal cells: W/F take 2, combining marks 0, everything else 1.

    VTE runs with ambiguous-width = narrow here, so East Asian Ambiguous
    characters count as 1 -- but their glyphs may still spill over in Cica,
    which is why the scene itself sticks to ASCII and block elements.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(s):
    return sum(char_width(c) for c in s)


def is_safe_glyph(ch):
    o = ord(ch)
    return 0x20 <= o < 0x7F or 0x2580 <= o <= 0x259F or ch in SAFE_EXTRA


# --------------------------------------------------------------------------
# frame buffer
# --------------------------------------------------------------------------
CONT = None              # right half of a wide character
EMPTY = (" ", None)
_NO_STYLE = object()     # "colour unknown": forces an SGR before the next glyph


def sgr(fg):
    if fg is None:
        return "\x1b[39m"
    return "\x1b[38;2;%d;%d;%dm" % fg


class Frame:
    """Cell grid of (char, fg). The background is never painted."""

    def __init__(self, cols, rows):
        self.cols = cols
        self.rows = rows
        self.cells = [[EMPTY] * cols for _ in range(rows)]

    def copy(self):
        f = Frame.__new__(Frame)
        f.cols, f.rows = self.cols, self.rows
        f.cells = [row[:] for row in self.cells]
        return f

    def set(self, x, y, ch, fg):
        if 0 <= y < self.rows and 0 <= x < self.cols:
            self.cells[y][x] = (ch, fg)

    def is_empty(self, x, y):
        return 0 <= y < self.rows and 0 <= x < self.cols and self.cells[y][x][0] == " "

    def put(self, x, y, text, fg=None):
        """Write text at (x, y), clipped to the frame. Wide chars take two cells."""
        if y < 0 or y >= self.rows:
            return x
        row = self.cells[y]
        for ch in text:
            cw = char_width(ch)
            if cw == 0:
                continue
            if x + cw > self.cols:
                break
            if x >= 0:
                if row[x][0] is CONT and x > 0:
                    row[x - 1] = EMPTY
                row[x] = (ch, fg)
                nxt = x + cw
                if cw == 2:
                    row[x + 1] = (CONT, fg)
                if nxt < self.cols and row[nxt][0] is CONT:
                    row[nxt] = EMPTY      # we overwrote the left half of a wide char
            x += cw
        return x

    # ---- output ----
    def to_text(self):
        """Plain text without escapes (--dump and tests)."""
        return "\n".join("".join(c[0] for c in row if c[0] is not CONT).rstrip()
                         for row in self.cells)

    def to_lines_ansi(self):
        """Whole frame as coloured lines, for printing to a scrolling terminal (--once)."""
        lines = []
        for row in self.cells:
            out, style = [], None
            for ch, fg in row:
                if ch is CONT:
                    continue
                if ch != " " and fg != style:
                    style = fg
                    out.append(sgr(fg))
                out.append(ch)
            lines.append("".join(out).rstrip() + "\x1b[0m")
        return "\n".join(lines)

    def to_ansi(self, prev=None):
        """Cursor-addressed ANSI. With prev (the previous frame's cells) only
        the changed runs of each changed row are emitted."""
        out = []
        style = _NO_STYLE
        for y, row in enumerate(self.cells):
            prow = prev[y] if prev is not None and y < len(prev) else None
            if prow is None or len(prow) != len(row):
                style = self._emit(out, y, 0, len(row), style)
                continue
            if prow == row:
                continue
            n, x = len(row), 0
            while x < n:
                if row[x] == prow[x]:
                    x += 1
                    continue
                start = end = x
                x += 1
                # merge runs separated by a few unchanged cells (one cursor
                # move costs about as much as re-sending four cells)
                while x < n and x - end <= 4:
                    if row[x] != prow[x]:
                        end = x
                    x += 1
                if row[start][0] is CONT and start > 0:
                    start -= 1
                stop = end + 1
                if stop < n and row[stop][0] is CONT:
                    stop += 1
                style = self._emit(out, y, start, stop, style)
                x = stop
        return "".join(out)

    def _emit(self, out, y, x0, x1, style):
        out.append("\x1b[%d;%dH" % (y + 1, x0 + 1))
        row = self.cells[y]
        for x in range(x0, x1):
            ch, fg = row[x]
            if ch is CONT:
                continue
            if ch != " " and fg != style:
                style = fg
                out.append(sgr(fg))
            out.append(ch)
        return style


# --------------------------------------------------------------------------
# quadrant pixel canvas
# --------------------------------------------------------------------------
# A cell is split 2x2 and drawn with the quadrant block elements, so a pixel
# is half a column wide and half a row tall: 0.5 x 0.86 in cell widths.
PIX_W = 0.5
QUAD = (" \u2598\u259d\u2580\u2596\u258c\u259e\u259b"
        "\u2597\u259a\u2590\u259c\u2584\u2599\u259f\u2588")


class Canvas:
    """Pixel (x, y): x in half columns, y in half rows. A pixel holds a
    brightness level or None (transparent). `clip` limits drawing to a pixel
    rectangle (x0, y0, x1, y1), end-exclusive."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.w, self.h = cols * 2, rows * 2
        self.px = [[None] * self.w for _ in range(self.h)]
        self.clip = (0, 0, self.w, self.h)

    def reset_clip(self):
        self.clip = (0, 0, self.w, self.h)

    def set(self, x, y, v):
        x0, y0, x1, y1 = self.clip
        if x0 <= x < x1 and y0 <= y < y1:
            self.px[y][x] = v

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y][x]
        return None

    def hspan(self, y, xa, xb, v):
        x0, y0, x1, y1 = self.clip
        if y0 <= y < y1:
            row = self.px[y]
            for x in range(max(xa, x0), min(xb, x1)):
                row[x] = v

    def rect(self, xa, ya, xb, yb, v):
        for y in range(ya, yb):
            self.hspan(y, xa, xb, v)

    def disc(self, cx, cy, r, v):
        """Filled circle: centre in pixels, radius in cell widths."""
        ry = r / PIX_H
        for y in range(int(math.floor(cy - ry)) - 1, int(math.ceil(cy + ry)) + 1):
            dy = (y + 0.5 - cy) * PIX_H
            if abs(dy) > r:
                continue
            half = math.sqrt(r * r - dy * dy) / PIX_W
            self.hspan(y, int(math.ceil(cx - half - 0.5)),
                       int(math.floor(cx + half - 0.5)) + 1, v)

    def compose(self, frame):
        """Pixels -> quadrant cells. A cell takes its brightest pixel."""
        cache = {}
        for r in range(min(frame.rows, self.rows)):
            top, bot = self.px[2 * r], self.px[2 * r + 1]
            out = frame.cells[r]
            for c in range(min(frame.cols, self.cols)):
                x = 2 * c
                a, b, p, q = top[x], top[x + 1], bot[x], bot[x + 1]
                if a is None and b is None and p is None and q is None:
                    continue
                if (a is not None and a < 0 and b is not None and b < 0
                        and p is not None and p < 0 and q is not None and q < 0):
                    # a negative level marks the inside of a cloud: texture
                    out[c] = ("\u2591", shade(-a))
                    continue
                out[c] = quad_cell(a, b, p, q, cache)


def quad_cell(a, b, c, d, cache=None):
    """Pixel levels TL, TR, BL, BR (None or negative = off) -> (glyph, colour)."""
    bits, hi = 0, -1.0
    if a is not None and a >= 0:
        bits, hi = 1, a
    if b is not None and b >= 0:
        bits |= 2
        hi = b if b > hi else hi
    if c is not None and c >= 0:
        bits |= 4
        hi = c if c > hi else hi
    if d is not None and d >= 0:
        bits |= 8
        hi = d if d > hi else hi
    if not bits:
        return EMPTY
    if cache is None:
        return (QUAD[bits], shade(hi))
    key = (bits, hi)
    cell = cache.get(key)
    if cell is None:
        cell = cache[key] = (QUAD[bits], shade(hi))
    return cell


# --------------------------------------------------------------------------
# clock fonts (pixel bitmaps, '#' = lit)
# --------------------------------------------------------------------------
FONT_4X7 = {
    "0": [".##.", "#..#", "#..#", "#..#", "#..#", "#..#", ".##."],
    "1": [".#..", "##..", ".#..", ".#..", ".#..", ".#..", "###."],
    "2": [".##.", "#..#", "...#", "..#.", ".#..", "#...", "####"],
    "3": [".##.", "#..#", "...#", ".##.", "...#", "#..#", ".##."],
    "4": ["..#.", ".##.", "#.#.", "#.#.", "####", "..#.", "..#."],
    "5": ["####", "#...", "###.", "...#", "...#", "#..#", ".##."],
    "6": [".##.", "#...", "#...", "###.", "#..#", "#..#", ".##."],
    "7": ["####", "...#", "..#.", "..#.", ".#..", ".#..", ".#.."],
    "8": [".##.", "#..#", "#..#", ".##.", "#..#", "#..#", ".##."],
    "9": [".##.", "#..#", "#..#", ".###", "...#", "...#", ".##."],
    ":": [".", ".", "#", ".", "#", ".", "."],
}
FONT_3X5 = {
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", ".##", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", ".#.", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    ":": [".", "#", ".", "#", "."],
}
FONTS = {"4x7": FONT_4X7, "3x5": FONT_3X5}


def text_px_width(text, font, sx):
    w = 0
    for i, ch in enumerate(text):
        w += len(font[ch][0]) * sx
        if i < len(text) - 1:
            w += sx
    return w


def draw_font(canvas, x, y, text, font, sx, sy, v):
    for ch in text:
        glyph = font[ch]
        for r, line in enumerate(glyph):
            for c, bit in enumerate(line):
                if bit == "#":
                    canvas.rect(x + c * sx, y + r * sy, x + (c + 1) * sx, y + (r + 1) * sy, v)
        x += (len(glyph[0]) + 1) * sx


# --------------------------------------------------------------------------
# sun, moon, time of day
# --------------------------------------------------------------------------
SYNODIC_MONTH = 29.530588853
NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)


def moon_age(when):
    """Days since the last new moon (0 .. 29.53), mean synodic month."""
    if when.tzinfo is None:
        when = when.astimezone()
    days = (when - NEW_MOON_EPOCH).total_seconds() / 86400.0
    return days % SYNODIC_MONTH


def moon_illumination(age):
    """Illuminated fraction of the disc (0 new .. 1 full)."""
    return (1.0 - math.cos(2.0 * math.pi * age / SYNODIC_MONTH)) / 2.0


def moon_lit(u, v, age):
    """Is point (u, v) of the unit disc lit? u > 0 is the right-hand side.

    Northern-hemisphere view: a waxing moon is lit from the right, a waning
    one from the left. The terminator is the ellipse u = k * sqrt(1 - v^2)
    with k = cos(phase angle).
    """
    k = math.cos(2.0 * math.pi * age / SYNODIC_MONTH)
    edge = math.sqrt(max(0.0, 1.0 - v * v))
    if age < SYNODIC_MONTH / 2.0:
        return u > k * edge
    return u < -k * edge


def smoothstep(a, b, x):
    if b == a:
        return 1.0 if x >= b else 0.0
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def hhmm_to_min(s, default):
    """'2026-10-02T05:37' or '05:37' -> minutes after midnight."""
    try:
        m = re.search(r"(\d{1,2}):(\d{2})", str(s))
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
    except (TypeError, ValueError):
        pass
    return default


class Sky:
    """Everything the picture needs to know about the time of day."""

    def __init__(self, when, weather):
        w = weather or {}
        tmin = when.hour * 60 + when.minute + when.second / 60.0
        rise = hhmm_to_min(w.get("sunrise"), 360)
        sset = hhmm_to_min(w.get("sunset"), 1080)
        if not 120 <= rise < sset <= 1380:
            rise, sset = 360, 1080
        self.tmin, self.rise, self.sset = tmin, rise, sset
        # 0 = night .. 1 = full day, ramping through twilight
        self.daylight = (smoothstep(rise - 35, rise + 25, tmin)
                         * (1.0 - smoothstep(sset - 25, sset + 35, tmin)))
        # dawn / dusk glow on the horizon, strongest at sunrise / sunset
        g_rise = max(0.0, 1.0 - abs(tmin - (rise - 8)) / 55.0)
        g_set = max(0.0, 1.0 - abs(tmin - (sset + 8)) / 55.0)
        self.glow = max(g_rise, g_set)
        self.glow_side = -1 if g_rise > g_set else 1   # -1 = east (left)
        self.sun_f = (tmin - rise) / float(sset - rise)
        # the moon crosses the meridian ~50 min later each day: transit at
        # noon for a new moon, midnight for a full moon
        self.age = moon_age(when)
        transit = (720.0 + 1440.0 * self.age / SYNODIC_MONTH) % 1440.0
        h = (tmin - transit + 720.0) % 1440.0 - 720.0   # minutes from transit
        self.moon_f = (h + 360.0) / 720.0               # 0 = rising .. 1 = setting
        # global night dimming for the room and the landscape
        self.nf = 0.45 + 0.55 * self.daylight
        near = min(abs(tmin - rise), abs(tmin - sset))
        if near <= 90:
            self.activity = "twilight"
        elif self.daylight < 0.05:
            self.activity = "night"
        else:
            self.activity = "day"

    @property
    def is_night(self):
        return self.daylight < 0.5


# --------------------------------------------------------------------------
# weather
# --------------------------------------------------------------------------
KINDS = ("clear", "clouds", "rain", "snow", "fog", "thunder")


def wmo_kind(code):
    """WMO weather code -> one of KINDS."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "clear"
    if c in (0, 1):
        return "clear"
    if c in (2, 3):
        return "clouds"
    if c in (45, 48):
        return "fog"
    if 51 <= c <= 67 or 80 <= c <= 82:
        return "rain"
    if 71 <= c <= 77 or c in (85, 86):
        return "snow"
    if 95 <= c <= 99:
        return "thunder"
    return "clouds"


PRESETS = {
    "clear": dict(code=0, cloud_cover=10, precipitation=0.0, wind=6.0),
    "clouds": dict(code=3, cloud_cover=70, precipitation=0.0, wind=14.0),
    "rain": dict(code=63, cloud_cover=100, precipitation=2.5, wind=12.0),
    "snow": dict(code=73, cloud_cover=100, precipitation=1.0, wind=6.0),
    "fog": dict(code=45, cloud_cover=100, precipitation=0.0, wind=3.0),
    "thunder": dict(code=95, cloud_cover=100, precipitation=5.0, wind=24.0),
}


def preset_weather(kind):
    w = dict(PRESETS[kind])
    w["kind"] = kind
    w["temp"] = None
    return w


def _num(v, default=None):
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def parse_open_meteo(data):
    """Open-Meteo /v1/forecast JSON -> our weather dict (raises on garbage)."""
    cur = data["current"]
    daily = data.get("daily") or {}
    code = int(cur.get("weather_code", 0))
    w = {
        "kind": wmo_kind(code),
        "code": code,
        "cloud_cover": _num(cur.get("cloud_cover"), 0.0),
        "precipitation": _num(cur.get("precipitation"), 0.0),
        "rain": _num(cur.get("rain"), 0.0),
        "snowfall": _num(cur.get("snowfall"), 0.0),
        "wind": _num(cur.get("wind_speed_10m"), 5.0),
        "temp": _num(cur.get("temperature_2m")),
        "is_day": cur.get("is_day"),
        "time": cur.get("time"),
    }
    sr, ss = daily.get("sunrise") or [], daily.get("sunset") or []
    if sr:
        w["sunrise"] = sr[0]
    if ss:
        w["sunset"] = ss[0]
    w["fetched"] = time.time()
    return w


def sane_weather(w):
    """Validate a cached weather dict; None when unusable."""
    if not isinstance(w, dict) or w.get("kind") not in KINDS:
        return None
    out = dict(w)
    for k, d in (("cloud_cover", 0.0), ("precipitation", 0.0), ("wind", 5.0)):
        out[k] = _num(out.get(k), d)
    out["temp"] = _num(out.get("temp"))
    return out


def freshen(w, now=None):
    """Age a fetched weather dict: an old temperature is dropped (3 h) and a
    very old sky decays to clear (12 h). Fixed presets are left alone."""
    if not w or not isinstance(w.get("fetched"), (int, float)):
        return w
    age = (time.time() if now is None else now) - w["fetched"]
    if age <= 3 * 3600:
        return w
    w = dict(w)
    w["temp"] = None
    if age > 12 * 3600:
        w.update(kind="clear", code=0, cloud_cover=min(30.0, _num(w.get("cloud_cover"), 0.0)),
                 precipitation=0.0)
    return w


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "phosphor-window"


def cache_file():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "phosphor-window" / "weather.json"


def load_config():
    try:
        with open(config_dir() / "config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        lat, lon = _num(cfg["latitude"]), _num(cfg["longitude"])
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        tz = cfg.get("timezone")
        return {"latitude": lat, "longitude": lon,
                "timezone": tz if isinstance(tz, str) and tz else "auto"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def load_cache():
    try:
        with open(cache_file(), encoding="utf-8") as f:
            return sane_weather(json.load(f))
    except (OSError, ValueError):
        return None


def save_cache(w):
    path = cache_file()
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".weather.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(w, f)
        os.replace(tmp, path)
        tmp = None
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def api_url(cfg):
    q = urllib.parse.urlencode({
        "latitude": "%.4f" % cfg["latitude"],
        "longitude": "%.4f" % cfg["longitude"],
        "current": "temperature_2m,weather_code,cloud_cover,precipitation,rain,"
                   "snowfall,wind_speed_10m,is_day",
        "daily": "sunrise,sunset",
        "timezone": cfg["timezone"],
        "forecast_days": "1",
    })
    return "https://api.open-meteo.com/v1/forecast?" + q


def fetch_weather(cfg, timeout=5.0):
    req = urllib.request.Request(api_url(cfg), headers={"User-Agent": "phosphor-window"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_open_meteo(json.loads(resp.read(1 << 20).decode("utf-8")))


class WeatherThread(threading.Thread):
    """Fetches every 20 minutes. Never raises, never reports: on failure the
    last known weather simply stays."""

    INTERVAL = 1200.0

    def __init__(self, cfg, on_update):
        super().__init__(name="weather", daemon=True)
        self.cfg = cfg
        self.on_update = on_update
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                w = fetch_weather(self.cfg)
                save_cache(w)
                self.on_update(w)
            except Exception:  # noqa: BLE001 - silence is the feature
                pass
            self.stop.wait(self.INTERVAL)


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------
class Layout:
    """Where things go for one pane size.

    Canvas coordinates are pixels (x = half columns, y = half rows). The
    rabbit's lane, the hay and the cushion are in columns.
    """

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.tier = "tiny"
        self.clock = None        # (x_px, y_px, font, sx, sy) or ("text", col, row)
        self.info = None         # (col, row, width) of the date/temperature line
        self.win = None          # window frame, outer pixel rect
        self.glass = None        # glass pixel rect
        self.vbars = []          # pixel x of each vertical glazing bar
        self.vbar_w = 2
        self.hbar = None         # pixel y of the horizontal glazing bar
        self.sill = None         # pixel rect
        self.horizon = 0         # pixel row of the horizon
        self.floor_py = 0        # pixel row of the wall/floor line
        self.feet_py = 0         # pixel row the rabbit stands on
        self.lane = (0, 0)       # rabbit x range (columns)
        self.rabbit_L = 0        # rabbit body length in columns (0 = no rabbit)
        self.hay = None          # (x0, x1) columns
        self.cushion = None      # (centre, half width) columns

    def glass_cells(self):
        """Cell rect (cx0, cy0, cx1, cy1) lying wholly on the glass."""
        g = self.glass
        return ((g[0] + 1) // 2, (g[1] + 1) // 2, g[2] // 2, g[3] // 2)


RABBIT_SIZES = (5, 7, 9, 11, 14)


def make_layout(cols, rows):
    lay = Layout(cols, rows)
    if cols < 20 or rows < 8:
        if cols >= 5:
            lay.clock = ("text", (cols - 5) // 2, rows // 2)
        return lay
    full = cols >= 60 and rows >= 28
    lay.tier = "full" if full else "compact"
    side = cols >= 100 and cols >= 2.3 * rows

    floor_rows = max(2, int(round(rows * (0.30 if full else 0.32))))
    floor_y = rows - floor_rows - 1                 # cell row of the floor line
    sill_y = floor_y - (2 if full else 1)           # cell row the sill sits in

    # ---- clock and window, horizontally ----
    if side:
        wx0 = max(2, int(round(cols * 0.05)))
        wx1 = wx0 + int(round(cols * 0.56))
        region = (wx1 + 3, cols - 2)
        rw = region[1] - region[0]
        if rw >= 40 and rows >= 36:
            font, sx, sy = "4x7", 4, 2
        elif rw >= 23:
            font, sx, sy = "4x7", 2, 1
        elif rw >= 18:
            font, sx, sy = "3x5", 2, 1
        else:
            font, sx, sy = None, 0, 0
        wy0 = max(1, int(round(rows * 0.07)))
    else:
        ww = int(round(cols * (0.70 if full else 0.78)))
        wx0 = (cols - ww) // 2
        wx1 = wx0 + ww
        if cols >= 50 and rows >= 30:
            font, sx, sy = "4x7", 2, 1
        elif cols >= 24 and rows >= 15:
            font, sx, sy = "3x5", 2, 1
        else:
            font, sx, sy = None, 0, 0
        region = (0, cols)

    if font:
        f = FONTS[font]
        cw = (text_px_width("00:00", f, sx) + 1) // 2
        ch_rows = (len(f["0"]) * sy + 1) // 2
    else:
        cw, ch_rows = 5, 1

    if side:
        cx = region[0] + (region[1] - region[0] - cw) // 2
        cy = wy0 + max(1, int((sill_y - wy0) * 0.22))
        lay.clock = (cx * 2, cy * 2, font, sx, sy) if font else ("text", cx, cy)
        lay.info = (region[0], cy + ch_rows + 1, region[1] - region[0])
    else:
        cy = 1 if rows >= 24 else 0
        cx = (cols - cw) // 2
        lay.clock = (cx * 2, cy * 2, font, sx, sy) if font else ("text", cx, cy)
        info_y = cy + ch_rows + (1 if font == "4x7" else 0)
        if rows >= 22:
            lay.info = (0, info_y, cols)
            wy0 = info_y + 2
        else:
            wy0 = cy + ch_rows + 1

    # ---- window ----
    if sill_y - wy0 >= 3 and wx1 - wx0 >= 8:
        X0, X1 = wx0 * 2, wx1 * 2
        Y0, Y1 = wy0 * 2, sill_y * 2
        side_t = 3 if (wx1 - wx0) >= 30 else 2
        bar_t = 2 if (sill_y - wy0) >= 14 else 1
        lay.win = (X0, Y0, X1, Y1)
        g = (X0 + side_t, Y0 + bar_t, X1 - side_t, Y1 - bar_t)
        lay.glass = g
        gw, gh = g[2] - g[0], g[3] - g[1]
        lay.vbar_w = 2 if full else 1
        nv = 2 if gw >= 130 else (1 if gw >= 18 else 0)
        lay.vbars = [g[0] + int(round(gw * (i + 1) / (nv + 1))) - lay.vbar_w // 2
                     for i in range(nv)]
        lay.hbar = g[1] + int(round(gh * 0.42)) if gh >= 10 else None
        lay.sill = (X0 - 3, Y1, X1 + 3, Y1 + (2 if full else 1))
        lay.horizon = g[1] + int(round(gh * 0.76))

    # ---- floor and the rabbit's lane ----
    lay.floor_py = floor_y * 2 + 1
    floor_px = (rows - floor_y - 1) * 2
    lay.feet_py = min(lay.floor_py + max(2, int(round(floor_px * 0.62))), rows * 2 - 2)
    # the rabbit stands in front of the wall, so its ears may reach the sill
    if lay.sill:
        top_limit = lay.sill[1] - (4 if full else 2)
    else:
        top_limit = max(0, lay.floor_py - 8)
    avail = (lay.feet_py - top_limit) * PIX_H
    L = 0
    for size in RABBIT_SIZES:
        # sized by the sitting height; standing up it may overlap the sill
        if size * 1.45 <= avail and size * 5.5 <= cols:
            L = size
    lay.rabbit_L = L
    margin = max(2, int(L * 0.8))
    lay.lane = (margin, cols - margin)
    if L and cols >= 34:
        hw = max(3, int(round(L * 0.8)))
        hx0 = max(1, int(cols * 0.09) - hw // 2)
        lay.hay = (hx0, hx0 + hw)
        half = max(3, int(round(L * 0.75)))
        lay.cushion = (min(cols - half - 2, int(cols * 0.80)), half)
        lay.lane = (max(margin, hx0 + hw + int(L * 0.55)), cols - margin)
    return lay


# --------------------------------------------------------------------------
# landscape, stars, clouds (deterministic for a seed and a layout)
# --------------------------------------------------------------------------
# Day levels. The land is drawn like a vector display: the ridges are lit
# contour lines over open ground, and only the small things (trees, roofs)
# are solid. Any filled cell is at least FAINT, so filled hills would turn
# the bottom of the window into a slab.
LV_RIDGE_FAR = 0.36
LV_RIDGE_NEAR = 0.62
LAND = {"tree": (0.34, 0.70), "house": (0.36, 0.36), "roof": (0.46, 0.78)}


def _ridge(rng, width, base, amp, waves=3, wl=1.0):
    comps = []
    for k in range(waves):
        comps.append((rng.uniform(0.6, 1.0) / (k + 1),
                      rng.uniform(0.7, 1.4) * (k + 1) * wl,
                      rng.uniform(0, 2 * math.pi)))
    norm = sum(c[0] for c in comps) or 1.0
    out = []
    for x in range(width):
        s = sum(a * math.sin(2 * math.pi * f * x / max(1, width) + p) for a, f, p in comps)
        out.append(base - amp * (0.5 + 0.5 * s / norm))
    return out


class Scenery:
    """The view outside: ridges, trees, roofs, stars and cloud shapes."""

    def __init__(self, lay, seed):
        self.land = []        # (x, y, level) absolute pixels, day levels
        self.far = set()      # pixels of the far ridge (they fade in fog)
        self.lit = []         # (x, y) house windows that glow at night
        self.stars = []       # (col, row, base, period, phase, glyph)
        self.clouds = []
        self.deck = []
        self.sky_top = {}     # pixel column -> first land pixel row
        if lay.glass is None:
            return
        rng = random.Random(seed * 7919 + 17)
        gx0, gy0, gx1, gy1 = lay.glass
        gw, gh = gx1 - gx0, gy1 - gy0
        hz = lay.horizon
        far = _ridge(rng, gw, hz - gh * 0.03, gh * 0.15, 3, 1.0)
        near = _ridge(rng, gw, hz + gh * 0.09, max(gh * 0.07, 2.6), 3, 1.5)
        near = [min(v, gy1 - 2.0) for v in near]
        yf = [int(round(v)) for v in far]
        yn = [int(round(v)) for v in near]
        short = gh < 26
        if short:
            yf = list(yn)          # a short window shows a single ridge
        contour = {}
        for i in range(gw):
            x = gx0 + i
            # join each column to its neighbour so steep slopes stay unbroken
            for ys, lv in ((yn, LV_RIDGE_NEAR), (yf, LV_RIDGE_FAR)):
                if short and ys is yf:
                    continue
                lo = ys[i] if i == 0 else min(ys[i], ys[i - 1] + (1 if ys[i - 1] < ys[i] else 0))
                for y in range(lo, ys[i] + 1):
                    if y < yn[i] or ys is yn:      # the near hill hides the far ridge
                        if gy0 <= y < gy1:
                            contour.setdefault((x, y), (lv, ys is yf))
        kinds = {}

        def ground(i):
            return int(round(near[max(0, min(gw - 1, i))]))

        def put(i, y, kind):
            if 0 <= i < gw and gy0 <= y < gy1:
                kinds[(gx0 + i, y)] = kind

        scale = max(0.6, min(1.6, gw / 96.0))
        if gw >= 32:
            n_house = 0 if short else (1 if gw < 80 else (2 if gw < 160 else 3))
            slots = sorted(rng.uniform(0.12, 0.88) for _ in range(n_house))
            for s in slots:
                bw = max(6, int(round(rng.uniform(8, 12) * scale)))
                bw += bw % 2
                cx = int(s * gw)
                base = max(ground(cx - bw // 2), ground(cx + bw // 2)) + 1
                bh = max(2, int(round(rng.uniform(2.6, 3.6) * scale)))
                top = base - bh
                for y in range(top, base):
                    for i in range(cx - bw // 2, cx + bw // 2):
                        put(i, y, "house")
                # gable roof overhanging the walls by one pixel
                half, y = bw // 2 + 1, top - 1
                while half > 0:
                    for i in range(cx - half, cx + half):
                        put(i, y, "roof")
                    half -= 2
                    y -= 1
                wy = top + bh // 2
                wx = cx + (2 if rng.random() < 0.5 else -3)
                for i in (wx, wx + 1):
                    if 0 <= i < gw and gy0 <= wy < gy1:
                        kinds.pop((gx0 + i, wy), None)   # a dark window by day
                        self.lit.append((gx0 + i, wy))
            n_tree = max(1 if short else 2, int(gw / (40 if short else 22)))
            for _ in range(n_tree):
                cx = rng.uniform(0.04, 0.96) * gw
                gy = ground(int(cx))
                if rng.random() < 0.65:
                    r = rng.uniform(1.0, 1.8) * scale
                    trunk = rng.randint(1, 2)
                    cy = gy - trunk - r / PIX_H + 0.5
                    ry = r / PIX_H
                    for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
                        dy = (y + 0.5 - cy) * PIX_H
                        if abs(dy) > r:
                            continue
                        half = math.sqrt(r * r - dy * dy) / PIX_W
                        for i in range(int(math.ceil(cx - half - 0.5)), int(math.floor(cx + half - 0.5)) + 1):
                            put(i, y, "tree")
                    for y in range(int(cy), gy + 1):
                        put(int(cx), y, "tree")
                else:
                    h = max(3, int(round(rng.uniform(4, 6) * scale)))
                    for k in range(h):
                        half = (h - k) * 0.85
                        for i in range(int(cx - half), int(cx + half) + 1):
                            put(i, gy - k, "tree")

        for (x, y), (lv, is_far) in contour.items():
            if (x, y) not in kinds:
                self.land.append((x, y, lv))
                if is_far:
                    self.far.add((x, y))
        for (x, y), kind in kinds.items():
            above = kinds.get((x, y - 1))
            fill, rim = LAND[kind]
            self.land.append((x, y, rim if above is None else fill))
        for x, y, _ in self.land:
            if y < self.sky_top.get(x, 1 << 30):
                self.sky_top[x] = y
        for i in range(gw):
            x = gx0 + i
            self.sky_top[x] = min(self.sky_top.get(x, gy1), yf[i], yn[i])

        # stars: cells of open sky
        srng = random.Random(seed * 104729 + 3)
        cx0, cy0, cx1, _ = lay.glass_cells()
        cells = [(c, r) for r in range(cy0, (hz - 2) // 2) for c in range(cx0, cx1)
                 if min(self.sky_top[2 * c], self.sky_top.get(2 * c + 1, gy1)) > 2 * r + 2]
        n = int(len(cells) * 0.035)
        for (c, r) in srng.sample(cells, min(n, len(cells))):
            base = srng.uniform(0.25, 1.0)
            glyph = "+" if base > 0.94 else ("\u00b7" if base > 0.62 else ".")
            self.stars.append((c, r, base, srng.uniform(9, 30), srng.uniform(0, 6.28), glyph))

        # a pool of cumulus shapes; cloud cover decides how many show
        crng = random.Random(seed * 3571 + 5)
        gw_cols = gw * PIX_W
        sky_h = max(4, hz - gy0)
        for k in range(14):
            w = crng.uniform(0.16, 0.32) * gw_cols          # cell widths
            puffs = []
            n_p = crng.randint(3, 5)
            for j in range(n_p):
                fx = (j + 0.5) / n_p
                r = w * crng.uniform(0.2, 0.27) * (1.0 - 0.7 * abs(fx - 0.5))
                puffs.append((fx * w - w / 2, -r * crng.uniform(0.35, 0.75), r))
            self.clouds.append({
                "w": w / PIX_W,                                 # pixels
                "puffs": puffs,
                "base": gy0 + crng.uniform(0.2, 0.62) * sky_h,
                "x0": crng.uniform(0, gw + w / PIX_W),
                "speed": 1.0 if k % 2 else 0.75,                # two layers
            })
        self.deck = [(crng.uniform(0, 1), crng.uniform(0.6, 1.0)) for _ in range(5)]


# --------------------------------------------------------------------------
# the rabbit: shapes per pose (units of body length, x forward, y up)
# --------------------------------------------------------------------------
# ("e", cx, cy, rx, ry, rot_deg, tone) ellipses, drawn front to back.
# tone: "b" body, "f" far side (dimmer), "h" a hay stalk.
def rabbit_shapes(pose, frame=0, twitch=False):
    breath = 0.014 if frame % 2 else 0.0
    if pose == "sit":
        return [
            ("e", 0.37, 0.69, 0.23, 0.19, -15, "b"),        # head
            ("e", 0.55, 0.63, 0.09, 0.085, 0, "b"),         # muzzle
            ("e", 0.22, 1.02, 0.075, 0.26, 26 if twitch else 40, "b"),  # near ear
            ("e", 0.34, 1.05, 0.07, 0.25, 8, "f"),          # far ear
            ("e", 0.14, 0.38, 0.27, 0.30, -10, "b"),        # chest
            ("e", -0.13, 0.30, 0.36, 0.30, 0, "b"),         # haunch
            ("e", -0.50, 0.25, 0.10, 0.10, 0, "b"),         # tail
            ("e", -0.02, 0.045, 0.32, 0.05, 0, "b"),        # hind foot
            ("e", 0.34, 0.045, 0.10, 0.05, 0, "b"),         # fore paw
        ], [(0.43, 0.73)]
    if pose == "loaf":
        return [
            ("e", 0.38, 0.38, 0.21, 0.18, -8, "b"),
            ("e", 0.56, 0.33, 0.085, 0.08, 0, "b"),
            ("e", 0.20, 0.62, 0.07, 0.24, 44 if twitch else 58, "b"),
            ("e", 0.29, 0.66, 0.065, 0.23, 38, "f"),
            ("e", -0.06, 0.24, 0.48, 0.24 + breath, 0, "b"),
            ("e", -0.53, 0.21, 0.085, 0.085, 0, "b"),
        ], [(0.45, 0.42)]
    if pose == "sleep":
        return [
            ("e", 0.39, 0.29, 0.21, 0.17, -10, "b"),
            ("e", 0.56, 0.23, 0.085, 0.075, 0, "b"),
            ("e", 0.18, 0.52, 0.07, 0.25, 52 if twitch else 66, "b"),   # ears laid back
            ("e", 0.26, 0.55, 0.065, 0.24, 54, "f"),
            ("e", -0.06, 0.23, 0.48, 0.23 + breath, 0, "b"),
            ("e", -0.53, 0.20, 0.085, 0.085, 0, "b"),
        ], []
    if pose == "flop":                                       # lying on its side
        return [
            ("e", 0.52, 0.22, 0.20, 0.17, 14, "b"),
            ("e", 0.69, 0.22, 0.085, 0.075, 0, "b"),
            ("e", 0.24, 0.44, 0.07, 0.26, 70 if twitch else 82, "b"),
            ("e", 0.28, 0.40, 0.065, 0.24, 90, "f"),
            ("e", -0.05, 0.21, 0.52, 0.21 + breath, 0, "b"),
            ("e", -0.64, 0.08, 0.20, 0.05, -5, "b"),
            ("e", -0.52, 0.10, 0.16, 0.05, -15, "f"),
            ("e", 0.64, 0.05, 0.14, 0.04, 0, "f"),
            ("e", -0.56, 0.30, 0.08, 0.08, 0, "b"),
        ], [(0.56, 0.24)]
    if pose == "groom":
        dy = (0.0, 0.07, 0.03)[frame % 3]
        rot = (-34, -24, -30)[frame % 3]
        return [
            ("e", 0.38, 0.66 + dy, 0.07, 0.12, -20, "b"),   # fore paws at the face
            ("e", 0.26, 0.80, 0.21, 0.18, rot, "b"),
            ("e", 0.41, 0.71, 0.085, 0.08, 0, "b"),
            ("e", 0.06, 1.08, 0.075, 0.26, 44, "b"),
            ("e", 0.18, 1.11, 0.07, 0.25, 16, "f"),
            ("e", 0.07, 0.44, 0.26, 0.34, -8, "b"),
            ("e", -0.13, 0.30, 0.34, 0.30, 0, "b"),
            ("e", -0.48, 0.24, 0.10, 0.10, 0, "b"),
            ("e", -0.02, 0.045, 0.30, 0.05, 0, "b"),
        ], ([] if frame % 3 == 1 else [(0.31, 0.85)])
    if pose == "eat":
        jaw = 0.025 if frame % 2 else 0.0
        return [
            ("e", 0.72, 0.07 + jaw, 0.12, 0.024, -16, "h"),  # a hay stalk
            ("e", 0.46, 0.20 + jaw, 0.20, 0.16, -34, "b"),
            ("e", 0.62, 0.12 + jaw, 0.085, 0.075, 0, "b"),
            ("e", 0.30, 0.56, 0.075, 0.25, 30, "b"),             # ears stay up
            ("e", 0.40, 0.56, 0.07, 0.24, 12, "f"),
            ("e", 0.14, 0.26, 0.27, 0.22, -12, "b"),
            ("e", -0.15, 0.31, 0.35, 0.31, 0, "b"),
            ("e", -0.52, 0.26, 0.10, 0.10, 0, "b"),
            ("e", -0.04, 0.045, 0.32, 0.05, 0, "b"),
            ("e", 0.40, 0.045, 0.09, 0.05, 0, "b"),
        ], [(0.50, 0.24)]
    if pose == "look":
        return [
            ("e", 0.12, 1.02, 0.21, 0.18, 15, "b"),
            ("e", 0.30, 1.07, 0.085, 0.08, 0, "b"),
            ("e", -0.04, 1.37, 0.075, 0.26, 8 if twitch else 24, "b"),
            ("e", 0.10, 1.40, 0.07, 0.25, 2, "f"),
            ("e", 0.25, 0.74, 0.07, 0.11, -30, "b"),
            ("e", 0.04, 0.60, 0.24, 0.36, 6, "b"),
            ("e", -0.04, 0.25, 0.30, 0.25, 0, "b"),
            ("e", -0.33, 0.18, 0.09, 0.09, 0, "b"),
            ("e", 0.02, 0.045, 0.30, 0.05, 0, "b"),
        ], [(0.20, 1.06)]
    if pose == "crouch":
        return [
            ("e", 0.40, 0.42, 0.21, 0.18, -10, "b"),
            ("e", 0.57, 0.37, 0.085, 0.08, 0, "b"),
            ("e", 0.20, 0.70, 0.075, 0.25, 52, "b"),
            ("e", 0.30, 0.73, 0.07, 0.24, 34, "f"),
            ("e", -0.02, 0.26, 0.47, 0.26, 0, "b"),
            ("e", -0.51, 0.24, 0.09, 0.09, 0, "b"),
            ("e", -0.04, 0.045, 0.32, 0.05, 0, "b"),
            ("e", 0.36, 0.045, 0.09, 0.05, 0, "b"),
        ], [(0.47, 0.46)]
    if pose == "air":
        return [
            ("e", 0.47, 0.52, 0.20, 0.17, 5, "b"),
            ("e", 0.64, 0.50, 0.085, 0.075, 0, "b"),
            ("e", 0.22, 0.74, 0.07, 0.25, 70, "b"),
            ("e", 0.30, 0.78, 0.065, 0.24, 56, "f"),
            ("e", 0.00, 0.39, 0.50, 0.22, 10, "b"),
            ("e", -0.56, 0.20, 0.23, 0.055, 25, "b"),
            ("e", 0.47, 0.24, 0.13, 0.045, -40, "b"),
            ("e", -0.46, 0.48, 0.08, 0.08, 0, "b"),
        ], [(0.54, 0.56)]
    if pose == "land":
        return [
            ("e", 0.45, 0.36, 0.21, 0.17, -20, "b"),
            ("e", 0.62, 0.29, 0.085, 0.075, 0, "b"),
            ("e", 0.22, 0.64, 0.07, 0.25, 64, "b"),
            ("e", 0.30, 0.67, 0.065, 0.24, 48, "f"),
            ("e", 0.02, 0.33, 0.48, 0.23, -12, "b"),
            ("e", 0.43, 0.07, 0.10, 0.06, -60, "b"),
            ("e", -0.50, 0.42, 0.085, 0.085, 0, "b"),
            ("e", -0.40, 0.16, 0.19, 0.05, 30, "b"),
        ], [(0.51, 0.40)]
    if pose == "binky":
        flip = frame % 2
        return [
            ("e", 0.42, 0.44, 0.20, 0.17, -25 if flip else -10, "b"),
            ("e", 0.58, 0.37 if flip else 0.42, 0.085, 0.075, 0, "b"),
            ("e", 0.30, 0.76, 0.07, 0.25, -24 if flip else 6, "b"),
            ("e", 0.16, 0.76, 0.065, 0.24, 40, "f"),
            ("e", 0.00, 0.41, 0.46, 0.23, -14 if flip else -4, "b"),
            ("e", -0.42, 0.68 if flip else 0.58, 0.23, 0.055, 55 if flip else 35, "b"),
            ("e", 0.38, 0.21, 0.12, 0.045, -50, "b"),
            ("e", -0.46, 0.38, 0.08, 0.08, 0, "b"),
        ], [(0.48, 0.48)]
    if pose == "front":
        return [
            ("e", -0.11, 1.13, 0.075, 0.26, 12, "b"),
            ("e", 0.11, 1.13, 0.075, 0.26, -12, "b"),
            ("e", 0.0, 0.77, 0.25, 0.21, 0, "b"),
            ("e", 0.0, 0.34, 0.34, 0.32, 0, "b"),
            ("e", -0.17, 0.045, 0.12, 0.05, 0, "b"),
            ("e", 0.17, 0.045, 0.12, 0.05, 0, "b"),
        ], [(-0.10, 0.79), (0.10, 0.79)]
    raise ValueError("unknown pose: %s" % pose)


POSES = ("sit", "hop", "groom", "eat", "look", "loaf", "sleep", "binky", "flop", "front")
_SS = [((i + 0.5) / 3.0, (j + 0.5) / 3.0) for i in range(3) for j in range(3)]


@functools.lru_cache(maxsize=512)
def rabbit_sprite(pose, frame, L, facing, twitch=False):
    """Rasterise a pose to {(dx, up): tone} in canvas pixels. dx is relative
    to the anchor pixel, `up` counts pixel rows up from the feet row."""
    shapes, eyes = rabbit_shapes(pose, frame, twitch)
    # keep thin parts (ears, paws) at least about one pixel thick
    minr_x = 0.6 * PIX_W / L
    minr_y = 0.6 * PIX_H / L
    prepared = []
    xmin, xmax, ymax = 1e9, -1e9, 0.0
    for _kind, cx, cy, rx, ry, rot, tone in shapes:
        rx, ry = max(rx, minr_x), max(ry, minr_y)
        a = math.radians(rot)
        prepared.append((cx, cy, 1.0 / rx, 1.0 / ry, math.cos(a), math.sin(a), tone))
        ext = max(rx, ry)
        xmin, xmax = min(xmin, cx - ext), max(xmax, cx + ext)
        ymax = max(ymax, cy + ext)

    def tone_at(X, Y):
        for cx, cy, irx, iry, ca, sa, tone in prepared:
            dx, dy = X - cx, Y - cy
            u = (dx * ca + dy * sa) * irx
            v = (dy * ca - dx * sa) * iry
            if u * u + v * v <= 1.0:
                return tone
        return None

    sx_unit, sy_unit = PIX_W / L, PIX_H / L
    pix = {}
    for i in range(int(math.floor(xmin / sx_unit)) - 1, int(math.ceil(xmax / sx_unit)) + 1):
        for j in range(0, int(math.ceil(ymax / sy_unit)) + 1):
            hits, tone = 0, None
            for sx, sy in _SS:
                t = tone_at((i + sx) * sx_unit, (j + sy) * sy_unit)
                if t is not None:
                    hits += 1
                    if tone is None or (t == "b" and tone != "b"):
                        tone = t
            if hits >= 4:
                pix[(i, j)] = tone_at((i + 0.5) * sx_unit, (j + 0.5) * sy_unit) or tone
    if L >= 5:
        for ex, ey in eyes:
            key = (int(math.floor(ex / sx_unit)), int(math.floor(ey / sy_unit)))
            if pix.get(key) == "b":
                pix[key] = "eye"
    out = {}
    for (i, j), tone in pix.items():
        if tone == "eye":
            continue
        if tone == "b" and (i, j + 1) not in pix:
            tone = "rim"          # the top edge catches the window light
        out[(i if facing > 0 else -i - 1, j)] = tone
    return out


TONE_LEVEL = {"b": LV_RABBIT, "rim": LV_RABBIT_RIM, "f": LV_RABBIT_FAR, "h": LV_HAY}


@functools.lru_cache(maxsize=512)
def sprite_cells(pose, frame, L, facing, twitch, xpar, ypar):
    """A sprite grouped into cells for an anchor of the given pixel parity:
    ((cell dx, cell dy, (tone TL, TR, BL, BR)), ...), tone None = see-through."""
    cells = {}
    for (dx, up), tone in rabbit_sprite(pose, frame, L, facing, twitch).items():
        px, py = xpar + dx, ypar - up
        q = cells.setdefault((px >> 1, py >> 1), [None, None, None, None])
        q[(py & 1) * 2 + (px & 1)] = tone
    return tuple((cx, cy, tuple(q)) for (cx, cy), q in cells.items())


# --------------------------------------------------------------------------
# the rabbit: behaviour
# --------------------------------------------------------------------------
# Weights per time of day. Rabbits are crepuscular: busiest at dawn/dusk,
# dozy in the day, mostly asleep in the small hours.
WEIGHTS = {
    "night": dict(sleep=6.0, loaf=2.0, sit=1.0, groom=0.6, eat=1.0, look=0.4,
                  flop=0.6, wander=0.5, binky=0.0),
    "twilight": dict(sleep=0.3, loaf=1.0, sit=1.5, groom=1.2, eat=1.6, look=1.2,
                     flop=0.4, wander=2.4, binky=0.8),
    "day": dict(sleep=1.6, loaf=2.2, sit=1.2, groom=1.0, eat=1.0, look=0.8,
                flop=1.6, wander=0.9, binky=0.15),
}
DURATIONS = {
    "sit": (6, 20), "groom": (6, 14), "eat": (12, 30), "look": (12, 40),
    "loaf": (40, 150), "flop": (60, 240), "sleep": (180, 600), "front": (2.6, 3.4),
}
HOP_T = 0.62      # one hop
HOP_PAUSE = 0.28  # sitting beat between hops
BINKY_T = 1.15


class Rabbit:
    """Pure behaviour: a function of time, time of day, weather and chance.

    There is deliberately no state that accumulates -- no hunger, no mood,
    nothing that drifts if the pane is ignored for a week.
    """

    def __init__(self, lay, seed, t):
        self.rng = random.Random(seed)
        self.lay = lay
        lo, hi = lay.lane
        self.x = float(self._spot("window"))
        if hi > lo:
            self.x = min(hi, max(lo, self.x + self.rng.uniform(-4, 4)))
        self.facing = self.rng.choice((-1, 1))
        self.kind = "sit"
        self.t0 = t
        self.t1 = t + self.rng.uniform(2, 6)
        self.queue = []
        self.hops = []
        self.next_twitch = t + self.rng.uniform(3, 10)
        self.force = None        # --rabbit: keep doing this one thing

    # ---- geometry ----
    def relayout(self, lay):
        self.lay = lay
        lo, hi = lay.lane
        if hi > lo:
            self.x = min(hi, max(lo, self.x))
        if self.kind == "hop":
            self.kind, self.hops = "sit", []

    def _spot(self, name):
        lay = self.lay
        lo, hi = lay.lane
        if hi <= lo:
            return lo
        if name == "hay" and lay.hay:
            return min(hi, max(lo, lay.hay[1] + lay.rabbit_L * 0.75))
        if name == "cushion" and lay.cushion:
            return min(hi, max(lo, lay.cushion[0]))
        if name == "window" and lay.win:
            return min(hi, max(lo, (lay.win[0] + lay.win[2]) / 4.0))   # pixels -> columns
        return self.rng.uniform(lo, hi)

    # ---- planning ----
    def _choose(self, ctx):
        activity, kind = ctx
        w = dict(WEIGHTS.get(activity, WEIGHTS["day"]))
        if kind in ("rain", "snow", "thunder", "fog"):
            w["look"] *= 3.0
        if kind == "thunder":
            w["binky"] = 0.0
            w["loaf"] *= 1.5
        total = sum(w.values())
        r = self.rng.uniform(0, total)
        for k, v in w.items():
            r -= v
            if r <= 0:
                return k
        return "sit"

    def _plan(self, t, ctx):
        k = self._choose(ctx)
        if self.force:
            k = {"hop": "wander"}.get(self.force, self.force)
        target = None
        if k == "eat":
            target = self._spot("hay")
        elif k == "look":
            target = self._spot("window") + self.rng.uniform(-2, 2)
        elif k in ("sleep", "flop") and self.rng.random() < 0.5:
            target = self._spot("cushion")
        elif k == "loaf" and self.rng.random() < 0.25:
            target = self._spot("cushion")
        elif k == "wander":
            target = self._spot("any")
            k = "sit"
        dur = self.rng.uniform(*DURATIONS.get(k, (4, 10)))
        if k == "sleep" and ctx[0] == "night":
            dur = self.rng.uniform(600, 1500)
        if self.force == "hop":
            dur = self.rng.uniform(0.5, 1.5)
        if k == "binky":
            self.queue = [("binky", BINKY_T), ("sit", self.rng.uniform(3, 8))]
        elif target is not None and abs(target - self.x) >= 1.5:
            self.queue = [("hop", target), (k, dur)]
        else:
            self.queue = [(k, dur)]
        if k == "eat" and self.lay.hay:
            self.queue[-1] = ("eat", dur, -1 if self.lay.hay[1] < self.x else 1)

    def _start(self, t, item):
        kind = item[0]
        self.kind, self.t0 = kind, t
        if kind == "hop":
            target = item[1]
            L = max(1, self.lay.rabbit_L)
            step = max(1.0, L * 0.45)
            self.facing = 1 if target > self.x else -1
            hops, x = [], self.x
            while abs(target - x) > 0.5:
                nx = x + self.facing * min(step, abs(target - x))
                hops.append((x, nx))
                x = nx
            self.hops = hops
            self.t1 = t + len(hops) * (HOP_T + HOP_PAUSE)
        else:
            self.t1 = t + item[1]
            if len(item) > 2:
                self.facing = item[2]

    def update(self, t, ctx):
        if self.lay.rabbit_L <= 0:
            return
        guard = 0
        while t >= self.t1 and guard < 8:
            guard += 1
            if self.kind == "hop" and self.hops:
                self.x = self.hops[-1][1]
                self.hops = []
            if not self.queue:
                self._plan(self.t1, ctx)
            self._start(self.t1 if t - self.t1 < 30 else t, self.queue.pop(0))

    def poke(self, t):
        """Space bar: turn to face you with a tiny hop (a dozer only flicks an ear)."""
        if self.kind in ("sleep", "flop"):
            self.next_twitch = t
            return
        if self.kind in ("hop", "binky"):
            return
        self.queue = [("sit", self.rng.uniform(3, 6))]
        self.kind, self.t0, self.t1 = "front", t, t + self.rng.uniform(*DURATIONS["front"])

    # ---- appearance ----
    def view(self, t):
        """-> (pose, frame, x, lift_px, facing, twitch, zs)."""
        k, u = self.kind, max(0.0, t - self.t0)
        L = max(1, self.lay.rabbit_L)
        twitch = False
        if self.next_twitch <= t:
            if t - self.next_twitch < 0.28:
                twitch = True
            else:
                self.next_twitch = t + self.rng.uniform(4, 15)
        if k == "hop" and self.hops:
            period = HOP_T + HOP_PAUSE
            i = max(0, min(int(u // period), len(self.hops) - 1))
            w = u - i * period
            xa, xb = self.hops[i]
            if w < 0.12:
                return ("crouch", 0, xa, 0, self.facing, False, ())
            if w < 0.46:
                s = (w - 0.12) / 0.34
                lift = int(round(math.sin(math.pi * s) * L * 0.30 / PIX_H))
                return ("air", 0, xa + (xb - xa) * s, lift, self.facing, False, ())
            if w < HOP_T:
                return ("land", 0, xb, 0, self.facing, False, ())
            return ("sit", 0, xb, 0, self.facing, False, ())
        if k == "binky":
            if u < 0.15:
                return ("crouch", 0, self.x, 0, self.facing, False, ())
            if u < 0.85:
                s = (u - 0.15) / 0.7
                lift = int(round(math.sin(math.pi * s) * L * 0.62 / PIX_H))
                return ("binky", int(s * 3) % 2, self.x, lift, self.facing, False, ())
            return ("land", 0, self.x, 0, self.facing, False, ())
        if k == "groom":
            cyc = u % 3.4
            frame = 0 if cyc > 2.4 else int(cyc / 0.3) % 3
            return ("groom", frame, self.x, 0, self.facing, False, ())
        if k == "eat":
            cyc = u % 6.0
            if cyc > 4.9:
                return ("sit", 0, self.x, 0, self.facing, twitch, ())
            return ("eat", int(cyc / 0.26) % 2, self.x, 0, self.facing, False, ())
        if k in ("loaf", "flop", "sleep"):
            frame = int(u / 1.7) % 2
            zs = ()
            if k == "sleep":
                zs = self._zs(u)
            return (k, frame, self.x, 0, self.facing, twitch, zs)
        if k == "look":
            return ("look", 0, self.x, 0, self.facing, twitch, ())
        if k == "front":
            lift = 0
            if u < 0.5:
                lift = int(round(math.sin(math.pi * u / 0.5) * L * 0.18 / PIX_H))
            return ("front", 0, self.x, lift, self.facing, False, ())
        return ("sit", 0, self.x, 0, self.facing, twitch, ())

    @staticmethod
    def _zs(u):
        """A small 'z' drifts up from the sleeper every few seconds."""
        out = []
        period, life = 3.6, 4.6
        k = int(u // period)
        for j in (k - 1, k):
            age = u - (j * period + 1.5)
            if 0 <= age < life and j >= 0:
                out.append(age / life)
        return tuple(out)

    def next_change(self, t):
        """When the picture of the rabbit next changes (for the loop's sleep)."""
        k = self.kind
        if k in ("hop", "binky", "front"):
            return t + 0.125
        nxt = self.t1
        if k == "groom":
            nxt = min(nxt, t + 0.3)
        elif k == "eat":
            nxt = min(nxt, t + 0.26)
        elif k == "sleep":
            nxt = min(nxt, t + 0.5)
        elif k in ("loaf", "flop"):
            u = t - self.t0
            nxt = min(nxt, self.t0 + (math.floor(u / 1.7) + 1) * 1.7)
        if k in ("sit", "look", "loaf", "eat", "sleep", "flop"):
            nt = self.next_twitch
            nxt = min(nxt, nt if nt > t else nt + 0.3)
        return max(nxt, t + 0.02)


def dump_rabbit(lay, seed, sky, kind, pose=None):
    """A deterministic rabbit snapshot for --dump / --once."""
    rng = random.Random(seed * 31 + 7)
    r = Rabbit(lay, seed, 0.0)
    if pose is None:
        k = r._choose((sky.activity, kind))
        pose = {"wander": "sit"}.get(k, k)
    lo, hi = lay.lane
    x = r.x
    facing = rng.choice((-1, 1))
    if pose == "eat":
        x, facing = r._spot("hay"), (-1 if lay.hay else facing)
    elif pose == "look":
        x = r._spot("window")
    elif pose in ("sleep", "flop") and lay.cushion:
        x = r._spot("cushion")
    frame, lift, twitch, zs = 0, 0, False, ()
    L = max(1, lay.rabbit_L)
    if pose == "hop":
        pose, lift = "air", int(round(L * 0.30 / PIX_H))
    elif pose == "binky":
        lift = int(round(L * 0.62 / PIX_H))
    elif pose == "sleep":
        zs = (0.15, 0.6)
    if hi > lo:
        x = min(hi, max(lo, x))
    return (pose, frame, x, lift, facing, twitch, zs)


# --------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------
BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def thunder_flash(t):
    """1 during a brief double flicker roughly every half minute, else 0.
    A pure function of t, so every frame agrees on it."""
    k = int(t // 37.0)
    rng = random.Random((k * 2654435761) % 4294967296)
    if rng.random() > 0.6:
        return 0
    d = t - (k * 37.0 + rng.uniform(4.0, 30.0))
    # each pulse outlasts two 8 fps ticks, so the loop can never step over it
    return 1 if (0 <= d < 0.26 or 0.42 <= d < 0.62) else 0


class Scene:
    """Draws one pane size. The slow parts (sky, land, window, room, clock)
    are cached as a base frame and rebuilt only when they change; stars,
    rain/snow and the rabbit go on top every tick."""

    def __init__(self, cols, rows, seed):
        self.cols, self.rows = cols, rows
        self.seed = seed
        self.lay = make_layout(cols, rows)
        self.scenery = Scenery(self.lay, seed)
        self.base_key = None
        self.base = None
        self.base_canvas = None
        self._skycells = None
        self._drop_key = None
        self._drop_list = []
        self.builds = 0

    # ---- what the base layer depends on ----
    @staticmethod
    def _kind(weather):
        return (weather or {}).get("kind", "clear")

    def _cloud_count(self, weather):
        if not weather or not self.lay.glass:
            return 0
        kind = self._kind(weather)
        if kind == "fog":
            return 0
        if self._deck(weather):
            return 0                       # the overcast deck draws its own lobes
        cover = _num(weather.get("cloud_cover"), 0.0)
        gw = self.lay.glass[2] - self.lay.glass[0]
        per = max(1, gw // 28)
        n = int(round(per * cover / 100.0 * 1.4))
        return min(n, len(self.scenery.clouds))

    @staticmethod
    def _deck(weather):
        if not weather or weather.get("kind") == "fog":
            return False
        return (weather.get("kind") in ("rain", "snow", "thunder")
                or _num(weather.get("cloud_cover"), 0.0) >= 85)

    @staticmethod
    def _cloud_speed(weather):
        """Pixels per minute: a breeze drifts, a gale hurries."""
        wind = _num((weather or {}).get("wind"), 5.0)
        return 2.0 * (0.5 + 0.22 * max(0.0, min(wind, 60.0)))

    def _cloud_offsets(self, weather, t):
        v = self._cloud_speed(weather) * t / 60.0
        return int(v * 0.75), int(v), int(v * 0.6)

    def base_key_for(self, when, sky, weather, t):
        w = weather or {}
        kind = self._kind(weather)
        moving = self._cloud_count(weather) or self._deck(weather)
        temp = w.get("temp")
        return (when.strftime("%Y%m%d%H%M"), kind,
                int(_num(w.get("cloud_cover"), 0.0) // 5),
                None if temp is None else int(round(temp)),
                self._cloud_offsets(weather, t) if moving else None,
                thunder_flash(t) if kind == "thunder" else 0,
                int(t // 9) if kind == "fog" else 0)

    # ---- base layer ----
    def build_base(self, when, sky, weather, t):
        self.builds += 1
        lay = self.lay
        frame = Frame(self.cols, self.rows)
        canvas = Canvas(self.cols, self.rows)
        if lay.tier == "tiny":
            if lay.clock:
                _, x, y = lay.clock
                frame.put(x, y, when.strftime("%H:%M"), shade(LV_CLOCK * sky.nf))
            return frame, canvas
        kind = self._kind(weather)
        flash = thunder_flash(t) if kind == "thunder" else 0
        fog = kind == "fog"
        if lay.glass:
            canvas.clip = lay.glass
            self._draw_sun_moon(canvas, sky, weather, fog)
            self._draw_clouds(canvas, sky, weather, t, flash)
            self._draw_land(canvas, sky, fog)
            canvas.reset_clip()
            self._draw_window(canvas, sky)
        self._draw_room(canvas, sky)
        self._draw_clock(canvas, sky, when)
        canvas.compose(frame)
        if lay.glass:
            self._sky_texture(frame, sky, weather, fog, flash, t)
        self._room_glyphs(frame, sky, weather)
        self._info(frame, sky, when, weather)
        return frame, canvas

    def _sky_geom(self):
        lay = self.lay
        g = lay.glass
        top = g[1] + 2
        span = max(2.0, lay.horizon - top)
        r = max(1.6, min((g[2] - g[0]) * PIX_W * 0.05, span * PIX_H * 0.17, 5.0))
        return g, span, r

    def _arc(self, f, g, span):
        """Position along a sky arc for f in 0..1 (rising left, setting
        right). At f = 0 / 1 the disc sits on the hills; beyond, it sinks."""
        lay = self.lay
        x = g[0] + (g[2] - g[0]) * (0.08 + 0.84 * min(1.04, max(-0.04, f)))
        fc = min(1.0, max(0.0, f))
        y = lay.horizon - span * (0.22 + 0.66 * math.sin(math.pi * fc))
        y += max(0.0, -f, f - 1.0) * span * 6.0
        return x, y

    def _draw_sun_moon(self, canvas, sky, weather, fog):
        g, span, r = self._sky_geom()
        cover = _num((weather or {}).get("cloud_cover"), 0.0)
        heavy = self._kind(weather) in ("rain", "snow", "thunder") or cover >= 92
        f = sky.sun_f
        if -0.04 < f < 1.04 and sky.daylight > 0.02 and not heavy:
            x, y = self._arc(f, g, span)
            lv = 0.7 if fog else LV_SUN * (0.6 + 0.4 * sky.daylight)
            canvas.disc(x, y, r * 1.05, lv)
        m = sky.moon_f
        if 0.0 < m < 1.0 and sky.daylight < 0.55 and not heavy:
            x, y = self._arc(m, g, span)
            strength = (1.0 - sky.daylight / 0.55) * (0.5 if fog else 1.0)
            self._draw_moon(canvas, x, y, r * 0.95, sky.age, strength)

    @staticmethod
    def _draw_moon(canvas, cx, cy, r, age, strength):
        lit_lv = LV_MOON * (0.45 + 0.55 * strength)
        rx, ry = r / PIX_W, r / PIX_H
        for y in range(int(math.floor(cy - ry)) - 1, int(math.ceil(cy + ry)) + 1):
            for x in range(int(math.floor(cx - rx)) - 1, int(math.ceil(cx + rx)) + 1):
                lit = dark = 0
                for sx, sy in _SS:
                    u = (x + sx - cx) * PIX_W / r
                    v = (y + sy - cy) * PIX_H / r
                    if u * u + v * v > 1.0:
                        continue
                    if moon_lit(u, v, age):
                        lit += 1
                    else:
                        dark += 1
                if lit + dark >= 4:
                    canvas.set(x, y, lit_lv if lit >= 3 else LV_MOON_DARK)

    def _draw_clouds(self, canvas, sky, weather, t, flash):
        """Clouds like the hills: a lit outline around a textured inside."""
        lay, sc = self.lay, self.scenery
        n = self._cloud_count(weather)
        deck = self._deck(weather)
        if not n and not deck:
            return
        g = lay.glass
        gw = g[2] - g[0]
        far_off, near_off, deck_off = self._cloud_offsets(weather, t)
        kind = self._kind(weather)
        edge_lv = {"rain": 0.62, "snow": 0.85, "thunder": 0.5}.get(kind, 0.95) * sky.nf
        if deck and kind not in ("rain", "snow", "thunder"):
            edge_lv = 0.8 * sky.nf
        top_lv = edge_lv + 0.45 * sky.daylight          # sunlit tops
        fill_lv = 0.18 + 0.12 * sky.daylight
        if flash:
            edge_lv = top_lv = LV_FLASH
            fill_lv = 1.15
        pixels = set()
        for idx in range(n):
            c = sc.clouds[idx]
            span = gw + c["w"] * 2
            off = near_off if c["speed"] >= 1.0 else far_off
            cx = g[2] + c["w"] - ((c["x0"] + off) % span)  # the wind blows leftwards
            self._cloud(pixels, cx, c["base"], c["puffs"])
        if deck:
            # an overcast layer with a row of round puffs along its underside,
            # and a few larger, nearer puffs drifting a little faster
            sky_h = max(4, lay.horizon - g[1])
            depth = sky_h * (0.30 if kind == "thunder" else 0.24)
            y_base = g[1] + depth
            for x in range(g[0], g[2]):
                for y in range(g[1] - 1, int(y_base) + 1):
                    pixels.add((x, y))
            for layer, off, size, drop in ((0, deck_off, 0.9, 0.0), (1, near_off, 1.5, 0.45)):
                spacing = max(8.0, gw / (8.0 if layer == 0 else 4.5))
                count = int(gw / spacing) + 3
                for k in range(count):
                    p, a = sc.deck[(k * 3 + layer) % len(sc.deck)]
                    if layer == 1 and p < 0.45:
                        continue
                    cx = g[0] - spacing + ((k * spacing + off + p * spacing) % (count * spacing))
                    r = size * spacing * PIX_W * (0.35 + 0.25 * a)
                    cy = y_base + depth * drop * a
                    ry = r / PIX_H
                    for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
                        dy = (y + 0.5 - cy) * PIX_H
                        if abs(dy) > r:
                            continue
                        half = math.sqrt(r * r - dy * dy) / PIX_W
                        for x in range(int(math.ceil(cx - half - 0.5)), int(math.floor(cx + half - 0.5)) + 1):
                            pixels.add((x, y))

        x0, y0, x1, y1 = canvas.clip
        for (x, y) in pixels:
            if not (x0 <= x < x1 and y0 <= y < y1):
                continue
            if (x, y - 1) not in pixels:
                canvas.px[y][x] = top_lv
            elif ((x, y + 1) not in pixels or (x - 1, y) not in pixels and x > x0
                    or (x + 1, y) not in pixels and x < x1 - 1):
                canvas.px[y][x] = edge_lv
            else:
                canvas.px[y][x] = -fill_lv

    @staticmethod
    def _cloud(pixels, cx, base, puffs):
        for dx, dy, r in puffs:
            px, py = cx + dx / PIX_W, base + dy / PIX_H
            ry = r / PIX_H
            for y in range(int(py - ry) - 1, int(base) + 1):
                ddy = (y + 0.5 - py) * PIX_H
                if abs(ddy) > r:
                    continue
                half = math.sqrt(r * r - ddy * ddy) / PIX_W
                for x in range(int(math.ceil(px - half - 0.5)), int(math.floor(px + half - 0.5)) + 1):
                    pixels.add((x, y))

    def _draw_land(self, canvas, sky, fog):
        sc = self.scenery
        nf = sky.nf
        px = canvas.px
        x0, y0, x1, y1 = canvas.clip
        # the hills hide whatever of the sun, moon and clouds is behind them
        for x, top in sc.sky_top.items():
            for y in range(max(top, y0), y1):
                px[y][x] = None
        far = sc.far
        for x, y, v in sc.land:
            if fog:
                if (x, y) in far:
                    continue
                v *= 0.5
            if x0 <= x < x1 and y0 <= y < y1:
                px[y][x] = v * nf
        if sky.daylight < 0.35 and not fog:
            for x, y in sc.lit:
                canvas.set(x, y, 1.25)

    def _draw_window(self, canvas, sky):
        lay = self.lay
        nf = sky.nf
        x0, y0, x1, y1 = lay.win
        g = lay.glass
        lv = LV_FRAME * nf
        canvas.rect(x0, y0, x1, g[1], lv)
        canvas.rect(x0, g[3], x1, y1, lv)
        canvas.rect(x0, y0, g[0], y1, lv)
        canvas.rect(g[2], y0, x1, y1, lv)
        for vx in lay.vbars:
            canvas.rect(vx, g[1], vx + lay.vbar_w, g[3], lv * 0.92)
        if lay.hbar is not None:
            canvas.rect(g[0], lay.hbar, g[2], lay.hbar + 1, lv * 0.92)
        sx0, sy0, sx1, sy1 = lay.sill
        canvas.rect(sx0, sy0, sx1, sy0 + 1, LV_SILL * nf)
        if sy1 > sy0 + 1:
            canvas.rect(sx0 + 2, sy0 + 1, sx1 - 2, sy1, LV_FRAME * 0.75 * nf)

    def _hay_height(self):
        return max(2, int(round(self.lay.rabbit_L * 0.40 / PIX_H)))

    def _draw_room(self, canvas, sky):
        lay = self.lay
        nf = sky.nf
        canvas.rect(0, lay.floor_py, canvas.w, lay.floor_py + 1, LV_FLOOR * nf)
        feet = lay.feet_py
        if lay.hay:
            rng = random.Random(self.seed * 13 + 1)
            x0, x1 = lay.hay[0] * 2, lay.hay[1] * 2
            h = self._hay_height()
            mid, half = (x0 + x1) / 2.0, (x1 - x0) / 2.0
            for x in range(x0, x1):
                u = (x + 0.5 - mid) / half
                hh = int(round(h * max(0.0, 1.0 - u * u) ** 0.5)) + rng.choice((0, 0, 1))
                for y in range(feet - hh, feet + 1):
                    canvas.set(x, y, (LV_HAY + 0.3 if y == feet - hh else LV_HAY) * nf)
        if lay.cushion:
            c, half = lay.cushion
            x0, x1 = (c - half) * 2, (c + half) * 2
            h = max(2, int(round(lay.rabbit_L * 0.20 / PIX_H)))
            for x in range(x0, x1):
                edge = min(x - x0, x1 - 1 - x)
                top = feet - h + (2 if edge < 1 else 1 if edge < 3 else 0)
                if abs(x - (x0 + x1) / 2.0) < 1.0:
                    top += 1
                for y in range(top, feet + 1):
                    canvas.set(x, y, (LV_CUSHION + 0.3 if y == top else LV_CUSHION) * nf)

    def _draw_clock(self, canvas, sky, when):
        lay = self.lay
        if not lay.clock or lay.clock[0] == "text":
            return
        x, y, font, sx, sy = lay.clock
        draw_font(canvas, x, y, when.strftime("%H:%M"), FONTS[font], sx, sy,
                  LV_CLOCK * (0.55 + 0.45 * sky.daylight))

    # ---- glyph details in the base layer ----
    def _sky_cells(self):
        """Cells of open sky (on the glass, above the land) -- cached."""
        if self._skycells is not None:
            return self._skycells
        lay, top = self.lay, self.scenery.sky_top
        cx0, cy0, cx1, cy1 = lay.glass_cells()
        g3 = lay.glass[3]
        self._skycells = [(c, r) for r in range(cy0, cy1) for c in range(cx0, cx1)
                          if 2 * r + 1 < min(top.get(2 * c, g3), top.get(2 * c + 1, g3))]
        return self._skycells

    def _sky_texture(self, frame, sky, weather, fog, flash, t):
        """Dither on the open sky: twilight glow, fog, or a lightning flash."""
        lay = self.lay
        cells = self._sky_cells()
        if not cells:
            return
        cx0, cy0, cx1, _ = lay.glass_cells()
        hz_row = lay.horizon / 2.0
        sky_rows = max(1.0, hz_row - cy0)
        rows = frame.cells
        if flash:
            col = shade(0.8)
            for x, y in cells:
                if rows[y][x] is EMPTY and BAYER4[y % 4][x % 4] < 3:
                    rows[y][x] = ("\u2591", col)
            return
        if fog:
            # mist: every open cell of the glass gets a light-shade texture
            # whose brightness drifts in slow soft bands, thicker low down
            drift = int(t // 9) * 0.35
            cy1 = lay.glass_cells()[3]
            day = 0.6 + 0.4 * sky.daylight
            for y in range(cy0, cy1):
                low = (y - cy0 + 1.0) / max(1, cy1 - cy0)
                for x in range(cx0, cx1):
                    if rows[y][x] is not EMPTY:
                        continue
                    band = 0.5 + 0.5 * math.sin(x * 0.21 - drift + y * 0.9) * math.sin(x * 0.07 + y * 0.4 + drift * 0.5)
                    lv = (0.10 + 0.55 * low) * (0.55 + 0.45 * band) * day
                    if lv >= 0.12:
                        rows[y][x] = ("\u2591", shade(lv))
            return
        if sky.glow > 0.05:
            # twilight: a soft band hugging the hills, fading upwards by colour
            heavy = self._kind(weather) in ("rain", "snow", "thunder")
            strength = sky.glow * (0.35 if heavy else 1.0)
            span = max(1, cx1 - cx0)
            top = self.scenery.sky_top
            for x in range(cx0, cx1):
                side = (x - cx0) / float(span)
                if sky.glow_side < 0:
                    side = 1.0 - side
                ridge = min(top.get(2 * x, 0), top.get(2 * x + 1, 0)) // 2
                for k in range(4):
                    y = ridge - 1 - k
                    if y < cy0 or rows[y][x] is not EMPTY:
                        continue
                    lv = strength * (0.35 + 0.65 * side) * (1.0, 0.62, 0.36, 0.16)[k] * 1.1
                    if lv >= 0.12:
                        rows[y][x] = ("\u2591", shade(lv))

    def _room_glyphs(self, frame, sky, weather):
        lay = self.lay
        if lay.hay:
            # loose stalks along the top of the pile and a few poking out
            col = shade((LV_HAY + 0.25) * sky.nf)
            rng = random.Random(self.seed * 29 + 3)
            for x in range(lay.hay[0], lay.hay[1]):
                top = next((y for y in range(frame.rows) if frame.cells[y][x] is not EMPTY
                            and y >= lay.floor_py // 2 + 1), None)
                if top is None:
                    continue
                frame.cells[top][x] = (rng.choice("\\|//|\\"), col)
                if top > 0 and rng.random() < 0.35 and frame.cells[top - 1][x] is EMPTY:
                    frame.cells[top - 1][x] = (rng.choice("'`,"), col)
        self._light_patch(frame, sky, weather)

    def _light_patch(self, frame, sky, weather):
        """Sunlight -- or a bright moon -- lying on the floor under the window."""
        lay = self.lay
        if not lay.glass or lay.tier != "full":
            return
        kind = self._kind(weather)
        cover = _num((weather or {}).get("cloud_cover"), 0.0)
        if kind not in ("clear", "clouds") or cover > 80:
            return
        strength, lean = 0.0, 0.0
        if sky.daylight > 0.4 and 0.0 < sky.sun_f < 1.0:
            strength = sky.daylight * (1.0 - cover / 110.0)
            lean = (0.5 - sky.sun_f) * 2.0         # sun in the east -> patch leans west
        elif (sky.daylight < 0.2 and 0.25 < sky.moon_f < 0.75
              and moon_illumination(sky.age) > 0.8):
            strength = 0.35 * (1.0 - cover / 100.0)
            lean = (0.5 - sky.moon_f) * 2.0
        if strength < 0.25:
            return
        # the window's shape thrown on the floor: a short trapezoid of light
        # split by the shadows of the glazing bars
        cx0, _, cx1, _ = lay.glass_cells()
        y0 = lay.floor_py // 2 + 1
        depth = max(1, min(lay.rows - y0, int(round((lay.rows - y0) * 0.45))))
        bars = [(v / 2.0, (v + lay.vbar_w) / 2.0) for v in lay.vbars]
        for k in range(depth):
            y = y0 + k
            d = (k + 1.0) / depth
            shift = lean * (k + 1) * 1.6
            col = shade((0.05 + 0.32 * strength) * (1.0 - 0.45 * d))
            xa = int(round(cx0 + 1 + shift - d * 2))
            xb = int(round(cx1 - 1 + shift + d * 2))
            for x in range(max(0, xa), min(lay.cols, xb)):
                gx = x + 0.5 - shift
                if any(a - 0.6 <= gx < b + 0.6 for a, b in bars):
                    continue
                if frame.cells[y][x] is EMPTY:
                    frame.cells[y][x] = ("\u2591", col)

    def _info(self, frame, sky, when, weather):
        lay = self.lay
        if lay.clock and lay.clock[0] == "text":
            _, x, y = lay.clock
            frame.put(x, y, when.strftime("%H:%M"), shade(LV_CLOCK * (0.55 + 0.45 * sky.daylight)))
        if not lay.info:
            return
        x0, y, width = lay.info
        wd = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[when.weekday()]
        text = "%s %d/%d" % (wd, when.month, when.day)
        temp = (weather or {}).get("temp")
        if temp is not None:
            text += "   %d\u00b0" % int(round(temp))
        if disp_width(text) > width:
            text = "%d/%d" % (when.month, when.day)
        x = x0 + (width - disp_width(text)) // 2
        frame.put(x, y, text, shade(LV_INFO * (0.6 + 0.4 * sky.daylight)))

    # ---- per tick ----
    def render(self, when, t, weather, rabbit_view):
        sky = Sky(when, weather)
        key = self.base_key_for(when, sky, weather, t)
        if key != self.base_key or self.base is None:
            self.base, self.base_canvas = self.build_base(when, sky, weather, t)
            self.base_key = key
        frame = self.base.copy()
        if self.lay.tier == "tiny":
            return frame
        if self.lay.glass:
            self._stars(frame, sky, weather, t)
            self._precip(frame, weather, sky, t)
        if rabbit_view is not None and self.lay.rabbit_L:
            self._rabbit(frame, rabbit_view, sky)
        return frame

    def _stars(self, frame, sky, weather, t):
        if sky.daylight > 0.45:
            return
        kind = self._kind(weather)
        if kind not in ("clear", "clouds") or _num((weather or {}).get("cloud_cover"), 0.0) > 75:
            return
        vis = min(1.0, (0.45 - sky.daylight) / 0.35)
        rows = frame.cells
        for x, y, base, period, phase, glyph in self.scenery.stars:
            if rows[y][x] is not EMPTY:
                continue
            tw = 0.62 + 0.38 * math.sin(2 * math.pi * t / period + phase)
            lv = base * tw * vis * 1.25
            if lv < 0.12:
                continue
            rows[y][x] = (glyph if lv > 0.5 else ".", shade(round(lv * 6) / 6.0))

    def _drops(self, kind, drizzle, n):
        """Per-drop constants, drawn once per weather kind (not per tick)."""
        key = (kind, drizzle, n)
        if self._drop_key != key:
            rng = random.Random(self.seed * 9176 + (1 if kind == "snow" else 2))
            if kind == "snow":
                self._drop_list = [(rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(1.0, 2.2),
                                    rng.uniform(0.4, 1.4), rng.uniform(0.4, 0.9),
                                    rng.uniform(0, 6.3), rng.uniform(0.8, 1.25)) for _ in range(n)]
            else:
                lo, hi = (4.5, 6.5) if drizzle else (9.0, 13.0)
                self._drop_list = [(rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(lo, hi),
                                    rng.uniform(0.6, 1.15)) for _ in range(n)]
            self._drop_key = key
        return self._drop_list

    def _precip(self, frame, weather, sky, t):
        kind = self._kind(weather)
        if kind not in ("rain", "snow", "thunder"):
            return
        lay = self.lay
        cx0, cy0, cx1, cy1 = lay.glass_cells()
        gw, nrows = max(1, cx1 - cx0), max(1, cy1 - cy0)
        rows = frame.cells
        wind = _num(weather.get("wind"), 5.0)
        precip = _num(weather.get("precipitation"), 1.0)
        dim = 0.55 + 0.45 * sky.nf
        colours = {}
        if kind == "snow":
            n = int(gw * nrows * (0.025 + 0.03 * min(precip, 3.0) / 3.0))
            drift = min(0.8, wind / 40.0)
            for i, (fx, fy, v, amp, om, ph, lv) in enumerate(self._drops("snow", False, n)):
                yy = (fy * nrows + v * t) % nrows
                x = cx0 + int((fx * gw - drift * yy + amp * math.sin(om * t + ph)) % gw)
                y = cy0 + int(yy)
                if rows[y][x] is EMPTY:
                    big = i % 9 == 0
                    q = round((1.45 if big else lv) * dim * 16)
                    col = colours.get(q) or colours.setdefault(q, shade(q / 16.0))
                    rows[y][x] = ("*" if big else ("\u00b7" if i % 3 else "."), col)
            return
        code = int(_num(weather.get("code"), 61.0))
        drizzle = 51 <= code <= 57
        dens = 0.018 if drizzle else 0.02 + 0.05 * min(precip, 4.0) / 4.0
        n = int(gw * nrows * dens)
        slant = min(1.0, wind / 28.0)
        glyph = "|" if slant < 0.25 else "/"
        for i, (fx, fy, v, lv) in enumerate(self._drops(kind, drizzle, n)):
            yy = (fy * nrows + v * t) % nrows
            x = cx0 + int((fx * gw - slant * yy) % gw)
            y = cy0 + int(yy)
            if rows[y][x] is EMPTY:
                q = round(lv * dim * 16)
                col = colours.get(q) or colours.setdefault(q, shade(q / 16.0))
                rows[y][x] = ((("." if i % 2 else "'") if drizzle else glyph), col)

    def _rabbit(self, frame, view, sky):
        pose, fr, x, lift, facing, twitch, zs = view
        lay = self.lay
        L = lay.rabbit_L
        ax = int(round(x * 2))
        ay = lay.feet_py - lift
        nf = 0.5 + 0.5 * sky.nf
        levels = {tone: lv * nf for tone, lv in TONE_LEVEL.items()}
        levels[None] = None
        bx, by = ax >> 1, ay >> 1
        px = self.base_canvas.px
        rows = frame.cells
        cols, nrows = lay.cols, lay.rows
        body = set()
        for cx, cy, q in sprite_cells(pose, fr, L, facing, twitch, ax & 1, ay & 1):
            X, Y = bx + cx, by + cy
            body.add((X, Y))
            if 0 <= X < cols and 0 <= Y < nrows:
                top, bot = px[2 * Y], px[2 * Y + 1]
                rows[Y][X] = quad_cell(
                    levels[q[0]] if q[0] else top[2 * X],
                    levels[q[1]] if q[1] else top[2 * X + 1],
                    levels[q[2]] if q[2] else bot[2 * X],
                    levels[q[3]] if q[3] else bot[2 * X + 1])
        if zs:
            hx = x + (L * 0.30 if facing > 0 else -L * 0.30)
            head_row = (ay - int(L * 0.62 / PIX_H)) // 2 - 1     # just above the ears
            for age in zs:
                y = head_row - int(age * 4.2)
                xz = int(round(hx + age * 3.0 * (1 if facing > 0 else -1)))
                lv = 0.75 * (1.0 - age) * nf
                # a z floats in front of the room, but never over the rabbit
                if lv > 0.08 and 0 <= y < lay.rows and 0 <= xz < lay.cols and (xz, y) not in body:
                    rows[y][xz] = ("z", shade(lv))

    def tick_interval(self, weather):
        """Seconds between ticks needed by the sky alone."""
        kind = self._kind(weather)
        if kind in ("rain", "thunder"):
            return 0.125
        if kind == "snow":
            return 0.25
        return 1.0


# --------------------------------------------------------------------------
# one-shot rendering (tests, --dump, --once)
# --------------------------------------------------------------------------
def render_once(cols, rows, when, weather, seed=DEFAULT_SEED, pose=None, t=None):
    scene = Scene(cols, rows, seed)
    sky = Sky(when, weather)
    view = None
    if scene.lay.rabbit_L:
        view = dump_rabbit(scene.lay, seed, sky, (weather or {}).get("kind", "clear"), pose)
    if t is None:
        t = when.timestamp()
    return scene.render(when, t, weather, view)


# --------------------------------------------------------------------------
# terminal app
# --------------------------------------------------------------------------
ENTER = "\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?2004h\x1b[0m\x1b[2J"
LEAVE = "\x1b[?2004l\x1b[?7h\x1b[0m\x1b[?25h\x1b[?1049l"
PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"
INPUT_STALE = 0.5   # an unfinished escape sequence or paste older than this is dropped
SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "mksh", "tcsh", "csh", "ash",
          "yash", "elvish", "xonsh", "nu"}


def write_all(fd, data):
    """Write a whole frame with one write() (looping only on a short write)."""
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except InterruptedError:
            continue
        except BlockingIOError:
            select.select([], [fd], [], 1.0)
            continue
        view = view[n:]


def _held(buf, marker):
    """Length of the longest tail of buf that could be the start of marker."""
    for k in range(min(len(marker) - 1, len(buf)), 0, -1):
        if marker.startswith(buf[-k:]):
            return k
    return 0


class Keys:
    """Raw key bytes -> actions ("quit", "poke").

    Pasted text (bracketed paste) and escape sequences are swallowed whole,
    so a paste containing a 'q' cannot drop the pane to the shell. A sequence
    split across two reads is carried over; one left unfinished for longer
    than INPUT_STALE is forgotten, so the keyboard can never get stuck.
    """

    def __init__(self):
        self.carry = b""
        self.paste = False
        self.last = 0.0

    @property
    def pending(self):
        return bool(self.carry) or self.paste

    def expire(self, now):
        if self.pending and now - self.last > INPUT_STALE:
            self.carry, self.paste = b"", False

    def feed(self, data, now):
        self.last = now
        buf, self.carry = self.carry + data, b""
        acts = []
        i, n = 0, len(buf)
        while i < n:
            if self.paste:
                end = buf.find(PASTE_END, i)
                if end < 0:
                    k = _held(buf[i:], PASTE_END)
                    self.carry = buf[n - k:] if k else b""
                    return acts
                i, self.paste = end + len(PASTE_END), False
                continue
            b = buf[i]
            if b == 0x1B:
                rest = buf[i:]
                if rest.startswith(PASTE_START):
                    self.paste = True
                    i += len(PASTE_START)
                    continue
                if len(rest) < len(PASTE_START) and PASTE_START.startswith(rest):
                    self.carry = rest                 # perhaps a paste starting
                    return acts
                j = i + 1
                if buf[j] in (0x5B, 0x4F):           # CSI / SS3: skip to the final byte
                    j += 1
                    while j < n and not (0x40 <= buf[j] <= 0x7E):
                        j += 1
                    if j >= n:
                        if n - i <= 32:
                            self.carry = rest         # finish it on the next read
                        return acts
                i = j + 1                             # (Alt+key: ESC and the key)
                continue
            if b in (0x71, 0x51, 0x03):               # q Q ^C
                acts.append("quit")
                return acts
            if b == 0x20:
                acts.append("poke")
            i += 1
        return acts


def parent_is_interactive_shell():
    """True when an interactive, job-controlling shell started us.

    Then q simply hands control back to that shell instead of stacking a new
    shell on top of it each round trip. Started as a pane's own command we
    lead the session, and behind a `sh -c` style wrapper there is no job
    control -- in both cases this is False and q execs $SHELL, because
    exiting would close the pane.
    """
    try:
        if os.getsid(0) == os.getpid():
            return False
        ppid = os.getppid()
        if ppid <= 1 or os.getpgid(ppid) == os.getpgrp():
            return False
        if os.tcgetpgrp(sys.stdin.fileno()) != os.getpgrp():
            return False
        with open("/proc/%d/comm" % ppid, encoding="utf-8", errors="replace") as f:
            if f.read().strip().lstrip("-") not in SHELLS:
                return False
        with open("/proc/%d/cmdline" % ppid, "rb") as f:
            argv = f.read().split(b"\0")[1:]
        for a in argv:
            if a == b"--" or not a.startswith(b"-"):
                break
            if not a.startswith(b"--") and b"c" in a:
                return False      # `bash -c ...` would exit right after us
        return True
    except (OSError, ValueError):
        return False


class App:
    def __init__(self, args):
        self.args = args
        self.seed = args.seed if args.seed is not None else DEFAULT_SEED
        rabbit_seed = args.seed if args.seed is not None else int(time.time() * 1000) & 0xFFFFFFF
        self.rabbit_seed = rabbit_seed
        self.tz = None
        self.cfg = None
        self.weather = None
        self.running = True
        self.to_shell = False
        self.resized = True
        self.cols, self.rows = 80, 24
        self.scene = None
        self.rabbit = None
        self.prev = None
        self.thread = None
        self.keys = Keys()

    # ---- data ----
    def setup_weather(self):
        a = self.args
        if a.weather:
            self.weather = preset_weather(a.weather)
            cached = load_cache()
            if cached:
                for k in ("sunrise", "sunset"):
                    if cached.get(k):
                        self.weather[k] = cached[k]
        else:
            self.weather = load_cache()
            self.cfg = load_config()
        cfg = self.cfg or load_config()
        if cfg and ZoneInfo is not None and cfg.get("timezone") not in (None, "auto"):
            try:
                self.tz = ZoneInfo(cfg["timezone"])
            except Exception:  # noqa: BLE001 - unknown zone: fall back to local
                self.tz = None

    def on_weather(self, w):
        self.weather = w   # one reference swap; the loop picks it up next tick

    def now(self):
        return fixed_now(self.args, self.tz)

    # ---- loop ----
    def refresh_size(self):
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            cols, rows = size.columns, size.lines
        except OSError:
            cols, rows = 0, 0
        if cols <= 0 or rows <= 0:
            cols, rows = 80, 24
        self.cols, self.rows = cols, rows
        self.scene = Scene(cols, rows, self.seed)
        if self.rabbit is None:
            # the rabbit runs on the monotonic clock: wall time can jump
            self.rabbit = Rabbit(self.scene.lay, self.rabbit_seed, time.monotonic())
            if self.args.rabbit:
                self.rabbit.force = self.args.rabbit
                self.rabbit.t1 = time.monotonic()
        else:
            self.rabbit.relayout(self.scene.lay)

    def current_view(self, t, sky):
        self.rabbit.update(t, (sky.activity, (self.weather or {}).get("kind", "clear")))
        return self.rabbit.view(t)

    def run(self):
        if termios is None:
            print("phosphor-window: needs a POSIX terminal", file=sys.stderr)
            return 1
        fd = sys.stdin.fileno()
        try:
            saved = termios.tcgetattr(fd)
            if not sys.stdout.isatty():
                raise termios.error("stdout is not a terminal")
        except (termios.error, ValueError):
            print("phosphor-window: needs a terminal (use --once or --dump COLSxROWS "
                  "to print a single frame).", file=sys.stderr)
            return 1
        try:
            self.setup_weather()
        except Exception:  # noqa: BLE001 - the weather must never keep the window shut
            self.weather = self.cfg = None
        if self.cfg and not self.args.offline and not self.args.weather:
            self.thread = WeatherThread(self.cfg, self.on_weather)
            self.thread.start()
        back_to_parent = parent_is_interactive_shell()

        wake_r, wake_w = os.pipe()
        os.set_blocking(wake_r, False)
        os.set_blocking(wake_w, False)
        old_wakeup = signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)

        def on_winch(_s, _f):
            self.resized = True

        def on_term(_s, _f):
            self.running = False

        def on_int(_s, _f):
            self.running, self.to_shell = False, True

        signal.signal(signal.SIGWINCH, on_winch)
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGHUP, on_term)
        signal.signal(signal.SIGINT, on_int)

        out = sys.stdout.fileno()
        try:
            tty.setraw(fd)
            write_all(out, ENTER.encode())
            while self.running:
                try:
                    if not self.tick(fd, out, wake_r):
                        break
                except OSError:
                    break                    # the terminal went away
                except Exception:  # noqa: BLE001
                    # a bug outside the drawing code: hand the pane to the
                    # shell rather than letting the pane close under the user
                    self.to_shell = True
                    break
        finally:
            # termios first: if writing fails below, the tty is still sane
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            except termios.error:
                pass
            try:
                write_all(out, LEAVE.encode())
            except OSError:
                pass
            if self.to_shell:
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)   # leftovers stay out of the shell
                except termios.error:
                    pass
            signal.set_wakeup_fd(old_wakeup)
            for f in (wake_r, wake_w):
                try:
                    os.close(f)
                except OSError:
                    pass
            if self.thread:
                self.thread.stop.set()
        if self.to_shell and not back_to_parent:
            exec_shell()
        return 0

    def tick(self, fd, out, wake_r):
        """Draw one frame, then sleep until something will change or a key
        arrives. False when the terminal is gone."""
        prefix = ""
        if self.resized:
            self.resized = False
            self.refresh_size()
            self.prev = None
            prefix = "\x1b[2J"           # in the same write as the redraw
        t = time.time()          # sky motion: a pure function of wall time
        mono = time.monotonic()  # the rabbit
        when = self.now()
        weather = freshen(self.weather, t)
        sky = Sky(when, weather)
        try:
            view = self.current_view(mono, sky) if self.scene.lay.rabbit_L else None
            frame = self.scene.render(when, t, weather, view)
        except Exception:  # noqa: BLE001 - never fold the pane over a drawing bug
            frame = Frame(self.cols, self.rows)
            self.scene.base_key = None
        painted = prefix + frame.to_ansi(self.prev)
        if painted:
            write_all(out, painted.encode("utf-8"))
        self.prev = frame.cells

        delay = min(math.floor(t) + 1.0005 - t, self.scene.tick_interval(weather))
        if self.rabbit is not None and self.scene.lay.rabbit_L:
            delay = min(delay, self.rabbit.next_change(mono) - mono)
        keys = self.keys
        keys.expire(mono)
        if keys.pending:
            delay = min(delay, INPUT_STALE)
        timeout = max(0.0, delay - (time.monotonic() - mono))
        try:
            ready, _, _ = select.select([fd, wake_r], [], [], timeout)
        except InterruptedError:
            ready = []
        if wake_r in ready:
            try:
                while os.read(wake_r, 512):
                    pass
            except OSError:
                pass
        if fd in ready:
            data = os.read(fd, 1024)
            if not data:
                return False
            for act in keys.feed(data, time.monotonic()):
                if act == "quit":
                    self.running, self.to_shell = False, True
                elif act == "poke" and self.rabbit is not None:
                    self.rabbit.poke(time.monotonic())
        return True


def exec_shell():
    """Replace this process with the user's shell (same pid, same pane)."""
    # CPython ignores SIGPIPE and SIGXFSZ, and ignored dispositions survive
    # exec: without this, the shell and everything it runs would inherit them
    # (`yes | head` would never end).
    for name in ("SIGPIPE", "SIGXFSZ"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)
    for shell in (os.environ.get("SHELL"), "/bin/bash", "/bin/sh"):
        if shell and os.access(shell, os.X_OK):
            try:
                os.execv(shell, [shell])
            except OSError:
                continue


# --------------------------------------------------------------------------
# entry
# --------------------------------------------------------------------------
def parse_size(text):
    m = re.match(r"^(\d+)x(\d+)$", text.strip())
    if not m:
        raise argparse.ArgumentTypeError("expected COLSxROWS, e.g. 75x45")
    return max(1, int(m.group(1))), max(1, int(m.group(2)))


def parse_hhmm(text):
    m = re.match(r"^(\d{1,2}):(\d{2})$", text.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise argparse.ArgumentTypeError("expected HH:MM, e.g. 17:30")
    return int(m.group(1)), int(m.group(2))


def parse_date(text):
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD")


def fixed_now(args, tz):
    now = datetime.now(tz) if tz else datetime.now().astimezone()
    if args.date:
        now = now.replace(year=args.date.year, month=args.date.month, day=args.date.day)
    if args.time:
        now = now.replace(hour=args.time[0], minute=args.time[1], second=0, microsecond=0)
    return now


def build_parser():
    p = argparse.ArgumentParser(
        description="A window onto the weather, a clock, and a rabbit. q drops to a shell.")
    p.add_argument("--dump", type=parse_size, metavar="COLSxROWS",
                   help="print one frame as plain text and exit")
    p.add_argument("--once", nargs="?", const=True, type=parse_size, metavar="COLSxROWS",
                   help="print one frame with colour and exit (default: terminal size)")
    p.add_argument("--weather", choices=KINDS, help="fix the weather (no network)")
    p.add_argument("--time", type=parse_hhmm, metavar="HH:MM", help="fix the time of day")
    p.add_argument("--date", type=parse_date, metavar="YYYY-MM-DD", help="fix the date")
    p.add_argument("--offline", action="store_true", help="never use the network")
    p.add_argument("--seed", type=int, help="fix the landscape and the rabbit's choices")
    p.add_argument("--rabbit", choices=POSES, help="hold the rabbit in one pose")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.dump or args.once:
        if args.weather:
            weather = preset_weather(args.weather)
        else:
            weather = freshen(load_cache())   # never fetch for a one-shot frame
        cfg = load_config()
        tz = None
        if cfg and ZoneInfo is not None and cfg.get("timezone") not in (None, "auto"):
            try:
                tz = ZoneInfo(cfg["timezone"])
            except Exception:  # noqa: BLE001
                tz = None
        when = fixed_now(args, tz)
        if args.dump:
            cols, rows = args.dump
        elif args.once is not True:
            cols, rows = args.once
        else:
            try:
                size = os.get_terminal_size(sys.stdout.fileno())
                cols, rows = size.columns, size.lines - 1
            except OSError:
                cols, rows = 80, 24
            if cols <= 0 or rows <= 0:
                cols, rows = 80, 24
        seed = args.seed if args.seed is not None else DEFAULT_SEED
        frame = render_once(cols, rows, when, weather, seed=seed, pose=args.rabbit)
        if args.dump:
            sys.stdout.write(frame.to_text() + "\n")
        else:
            sys.stdout.write(frame.to_lines_ansi() + "\n")
        return 0
    return App(args).run() or 0


if __name__ == "__main__":
    sys.exit(main())
