"""Tests for tools/phosphor-window.py (stdlib unittest; no network, no tty).

Run with:  python3 -m unittest discover -s tests
"""
import importlib.util
import math
import os
import random
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "phosphor-window.py"

# Keep the user's real config / weather cache out of every test.
_TMP = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP.name, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP.name, "cache")

_spec = importlib.util.spec_from_file_location("phosphor_window", TOOL)
pw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pw)

TIMES = ((12, 0), (17, 40), (2, 0))
SIZES = ((160, 50), (75, 45), (40, 20), (20, 8))
SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def at(h, m, day=(2026, 10, 2)):
    """A local wall-clock time (what the pane shows)."""
    return datetime(*day, h, m).astimezone()


def run_cli(*args):
    env = dict(os.environ)
    return subprocess.run([sys.executable, str(TOOL)] + list(args), capture_output=True,
                          text=True, env=env, timeout=60)


def luma(rgb):
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class RenderTest(unittest.TestCase):
    def test_every_weather_time_and_size_renders(self):
        for kind in pw.KINDS:
            for h, m in TIMES:
                for cols, rows in SIZES:
                    with self.subTest(kind=kind, time="%02d:%02d" % (h, m), size=(cols, rows)):
                        frame = pw.render_once(cols, rows, at(h, m), pw.preset_weather(kind), seed=1)
                        text = frame.to_text()
                        self.assertTrue(text.strip())
                        lines = text.split("\n")
                        self.assertEqual(len(lines), rows)
                        self.assertTrue(all(pw.disp_width(line) <= cols for line in lines))

    def test_every_pose_renders(self):
        for pose in pw.POSES:
            for cols, rows in ((160, 50), (75, 45), (40, 20)):
                with self.subTest(pose=pose, size=(cols, rows)):
                    frame = pw.render_once(cols, rows, at(10, 0), pw.preset_weather("clear"),
                                           seed=2, pose=pose)
                    self.assertTrue(frame.to_text().strip())

    def test_tiny_and_degenerate_sizes_do_not_crash(self):
        for cols, rows in ((1, 1), (2, 1), (5, 2), (10, 4), (19, 7), (20, 8), (24, 9),
                           (30, 12), (39, 19), (300, 10), (12, 90)):
            with self.subTest(size=(cols, rows)):
                frame = pw.render_once(cols, rows, at(3, 0), pw.preset_weather("thunder"), seed=1)
                self.assertEqual(frame.rows, rows)
                self.assertEqual(frame.cols, cols)

    def test_tiny_pane_still_shows_the_time(self):
        text = pw.render_once(19, 7, at(9, 41), None).to_text()
        self.assertIn("09:41", text)

    def test_dumps_are_deterministic(self):
        a = pw.render_once(75, 45, at(17, 40), pw.preset_weather("rain"), seed=5, t=1000.0)
        b = pw.render_once(75, 45, at(17, 40), pw.preset_weather("rain"), seed=5, t=1000.0)
        self.assertEqual(a.to_text(), b.to_text())

    def test_only_one_cell_wide_safe_glyphs(self):
        # Cica draws several East Asian Ambiguous symbols (stars, geometric
        # shapes) full-width; the scene must stick to the safe set.
        for kind in pw.KINDS:
            for h, m in TIMES:
                for pose in (None, "sleep", "eat"):
                    frame = pw.render_once(75, 45, at(h, m), pw.preset_weather(kind), seed=1, pose=pose)
                    for row in frame.cells:
                        for ch, _ in row:
                            self.assertTrue(pw.is_safe_glyph(ch), repr(ch))
                            self.assertEqual(pw.char_width(ch), 1, repr(ch))


class ColourTest(unittest.TestCase):
    def test_once_colours_never_exceed_ph(self):
        ph_luma = luma(pw.PH)
        for kind in pw.KINDS:
            for hhmm in ("12:00", "17:40", "02:00"):
                with self.subTest(kind=kind, time=hhmm):
                    res = run_cli("--once", "75x45", "--weather", kind, "--time", hhmm,
                                  "--date", "2026-10-02", "--seed", "1")
                    self.assertEqual(res.returncode, 0, res.stderr)
                    seen = 0
                    for params in SGR_RE.findall(res.stdout):
                        p = params.split(";")
                        if p[:2] == ["38", "2"]:
                            r, g, b = (int(v) for v in p[2:5])
                            seen += 1
                            self.assertLessEqual(r, pw.PH[0])
                            self.assertLessEqual(g, pw.PH[1])
                            self.assertLessEqual(b, pw.PH[2])
                            self.assertLessEqual(luma((r, g, b)), ph_luma + 1e-9)
                        else:
                            # no indexed colours (the Tilix palette is all green)
                            # and never a painted background
                            self.assertIn(params, ("0", "39", ""), params)
                    self.assertGreater(seen, 0)

    def test_shade_is_clamped(self):
        self.assertEqual(pw.shade(99), pw.PH)
        self.assertEqual(pw.shade(-5), pw.PH_FAINT)
        self.assertEqual(pw.shade(float("nan")), pw.PH_FAINT)
        prev = (0, 0, 0)
        for i in range(0, 49):
            c = pw.shade(i / 16.0)
            self.assertTrue(all(c[k] >= prev[k] for k in range(3)), "ramp must be monotonic")
            prev = c

    def test_ph_is_rare(self):
        # PH is for the moon only; the bulk of the picture stays at MID or below
        frame = pw.render_once(75, 45, at(2, 0), pw.preset_weather("clear"), seed=1)
        lit = [fg for row in frame.cells for ch, fg in row if ch != " "]
        above_mid = [fg for fg in lit if luma(fg) > luma(pw.PH_MID) + 1]
        self.assertLess(len(above_mid), 0.05 * len(lit))
        day = pw.render_once(75, 45, at(12, 0), pw.preset_weather("clear"), seed=1)
        self.assertFalse([fg for row in day.cells for ch, fg in row
                          if ch != " " and luma(fg) > luma(pw.PH_MID) + 1])

    def test_lightning_is_never_skipped_at_8fps(self):
        # find every flicker in ten minutes (10 ms scan), then check that an
        # 8 fps loop sees it whatever phase its ticks happen to have
        onsets, prev = [], 0
        for i in range(60000):
            t = 1000.0 + i * 0.01
            f = pw.thunder_flash(t)
            if f and not prev:
                onsets.append(t)
            prev = f
        self.assertGreater(len(onsets), 3)
        for start in onsets:
            for phase in (0.0, 0.031, 0.062, 0.094, 0.124):
                first = math.ceil((start - phase) / 0.125)
                ticks = [k * 0.125 + phase for k in range(first, first + 3)]
                self.assertTrue(any(pw.thunder_flash(x) for x in ticks), (start, phase))

    def test_lightning_only_lights_the_sky(self):
        flash_t = next(t / 10.0 for t in range(0, 40000) if pw.thunder_flash(t / 10.0))
        calm_t = next(t / 10.0 for t in range(int(flash_t * 10) + 20, 40000)
                      if not pw.thunder_flash(t / 10.0))
        w = pw.preset_weather("thunder")
        scene = pw.Scene(75, 45, 1)
        a = scene.render(at(14, 0), flash_t, w, None)
        b = pw.Scene(75, 45, 1).render(at(14, 0), calm_t, w, None)
        self.assertNotEqual(a.to_text(), b.to_text())
        g = scene.lay.glass
        for y in range(scene.rows):
            for x in range(scene.cols):
                inside = g[0] // 2 <= x < (g[2] + 1) // 2 and g[1] // 2 <= y < (g[3] + 1) // 2
                if not inside:
                    self.assertEqual(a.cells[y][x], b.cells[y][x], (x, y))
        lit = [fg for row in a.cells for ch, fg in row if ch != " "]
        self.assertLessEqual(max(luma(fg) for fg in lit), luma(pw.PH_MID) + 1)


class NoPressureTest(unittest.TestCase):
    """The pane must never show anything that reads like a status or a chore."""

    def test_only_clock_date_and_temperature_text(self):
        weekdays = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
        w = pw.preset_weather("clear")
        w["temp"] = 21.4
        for pose in pw.POSES:
            for kind_w in (w, pw.preset_weather("rain"), None):
                with self.subTest(pose=pose):
                    when = at(17, 40)
                    text = pw.render_once(75, 45, when, kind_w, seed=3, pose=pose).to_text()
                    allowed = {str(when.month), str(when.day)}
                    if kind_w and kind_w.get("temp") is not None:
                        allowed.add(str(int(round(kind_w["temp"]))))
                    self.assertLessEqual(set(re.findall(r"\d+", text)), allowed)
                    words = set(re.findall(r"[A-Za-z]{2,}", text))
                    self.assertLessEqual(words, weekdays)

    def test_rabbit_has_no_needs(self):
        lay = pw.make_layout(75, 45)
        r = pw.Rabbit(lay, 7, 0.0)
        for name in vars(r):
            self.assertNotRegex(name, r"hunger|food|mood|health|happy|energy|need|stat")

    def test_rabbit_lives_a_long_unattended_day(self):
        # a day and a half of simulated time: always a valid pose, on the floor
        lay = pw.make_layout(75, 45)
        r = pw.Rabbit(lay, 11, 0.0)
        lo, hi = lay.lane
        seen = set()
        t = 0.0
        for step in range(0, 130000, 1):
            t = step * 1.0
            hour = (t / 3600.0) % 24
            act = "night" if hour < 4 or hour > 21 else ("twilight" if hour < 7 or hour > 16 else "day")
            r.update(t, (act, ("clear", "rain", "snow")[int(t // 20000) % 3]))
            if step % 7 == 0:
                pose, frame, x, lift, facing, twitch, zs = r.view(t)
                self.assertIn(pose, ("sit", "groom", "eat", "look", "loaf", "sleep", "binky",
                                     "flop", "front", "crouch", "air", "land"))
                self.assertGreaterEqual(x, lo - 0.01)
                self.assertLessEqual(x, hi + 0.01)
                self.assertGreaterEqual(lift, 0)
                pw.rabbit_sprite(pose, frame, lay.rabbit_L, facing, twitch)
                seen.add(pose)
        self.assertGreaterEqual(len(seen), 5, seen)


class AstronomyTest(unittest.TestCase):
    @staticmethod
    def dist(age, target):
        d = abs(age - target) % pw.SYNODIC_MONTH
        return min(d, pw.SYNODIC_MONTH - d)

    def test_epoch_is_new_moon(self):
        self.assertAlmostEqual(pw.moon_age(pw.NEW_MOON_EPOCH), 0.0, places=6)

    def test_known_new_and_full_moons(self):
        # Mean-month arithmetic drifts from the true moon by up to ~0.6 day.
        half = pw.SYNODIC_MONTH / 2
        cases = [
            (datetime(2024, 4, 8, 18, 21, tzinfo=timezone.utc), 0.0),     # total solar eclipse
            (datetime(2023, 10, 14, 17, 55, tzinfo=timezone.utc), 0.0),   # annular solar eclipse
            (datetime(2022, 11, 8, 11, 2, tzinfo=timezone.utc), half),    # total lunar eclipse
            (datetime(2025, 3, 14, 6, 55, tzinfo=timezone.utc), half),    # total lunar eclipse
            (datetime(2026, 3, 3, 11, 38, tzinfo=timezone.utc), half),    # total lunar eclipse
        ]
        for when, target in cases:
            with self.subTest(when=when):
                self.assertLess(self.dist(pw.moon_age(when), target), 1.0)

    def test_2026_10_02_is_a_waning_gibbous(self):
        # 2026-10-02 12:00 JST = 03:00 UTC. From the epoch (2000-01-06 18:14 UTC):
        #   2000-01-06 18:14 -> 2026-01-06 18:14 = 26*365 + 7 leap days = 9497 d
        #   2026-01-06 18:14 -> 2026-10-02 03:00 = 269 d - 15h14m      = 268.365 d
        #   total 9765.365 d / 29.530588853 = 330.687 months -> 20.27 d past new.
        # Full moon (14.77 d) was ~5.5 days earlier (the 2026-09-26 full moon) and
        # last quarter (22.1 d) is ~1.8 days ahead: waning gibbous, ~69 % lit.
        age = pw.moon_age(datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc))
        self.assertAlmostEqual(age, 20.27, delta=0.05)
        illum = pw.moon_illumination(age)
        self.assertGreater(illum, 0.6)
        self.assertLess(illum, 0.8)

    def test_lit_side(self):
        # northern hemisphere: waxing lit on the right, waning on the left
        self.assertTrue(pw.moon_lit(0.6, 0.0, 5.0))
        self.assertFalse(pw.moon_lit(-0.6, 0.0, 5.0))
        self.assertTrue(pw.moon_lit(-0.6, 0.0, 20.27))
        self.assertFalse(pw.moon_lit(0.6, 0.0, 20.27))
        self.assertTrue(pw.moon_lit(0.0, 0.0, 14.77))       # full
        self.assertFalse(pw.moon_lit(0.0, 0.0, 0.01))       # new

    def test_rendered_moon_matches_phase(self):
        # draw the moon alone and check the lit fraction of the disc
        for age in (3.0, 7.4, 14.8, 20.27, 26.0):
            c = pw.Canvas(40, 20)
            pw.Scene._draw_moon(c, 40.0, 20.0, 8.0, age, 1.0)
            lit = sum(1 for row in c.px for v in row if v is not None and v > 0.5)
            dark = sum(1 for row in c.px for v in row if v is not None and v <= 0.5)
            frac = lit / float(lit + dark)
            self.assertAlmostEqual(frac, pw.moon_illumination(age), delta=0.06)
            xs = [x for row in c.px for x, v in enumerate(row) if v is not None and v > 0.5]
            if 1.0 < age < 13.0:
                self.assertGreater(sum(xs) / len(xs), 40.0)   # lit right
            if 16.0 < age < 28.5:
                self.assertLess(sum(xs) / len(xs), 40.0)      # lit left

    def test_daylight_ramps(self):
        w = {"sunrise": "2026-10-02T05:37", "sunset": "2026-10-02T17:25"}
        self.assertEqual(pw.Sky(at(1, 0), w).daylight, 0.0)
        self.assertEqual(pw.Sky(at(12, 0), w).daylight, 1.0)
        dusk = pw.Sky(at(17, 30), w).daylight
        self.assertTrue(0.0 < dusk < 1.0)
        self.assertEqual(pw.Sky(at(17, 30), w).activity, "twilight")
        # no data: 06:00 / 18:00
        self.assertEqual((pw.Sky(at(12, 0), None).rise, pw.Sky(at(12, 0), None).sset), (360, 1080))


class WeatherTest(unittest.TestCase):
    def test_wmo_codes(self):
        table = {
            0: "clear", 1: "clear", 2: "clouds", 3: "clouds",
            45: "fog", 48: "fog",
            51: "rain", 53: "rain", 55: "rain", 56: "rain", 57: "rain",
            61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
            71: "snow", 73: "snow", 75: "snow", 77: "snow",
            80: "rain", 81: "rain", 82: "rain", 85: "snow", 86: "snow",
            95: "thunder", 96: "thunder", 99: "thunder",
        }
        for code, kind in table.items():
            self.assertEqual(pw.wmo_kind(code), kind, code)
        self.assertEqual(pw.wmo_kind(None), "clear")
        self.assertEqual(pw.wmo_kind("junk"), "clear")

    SAMPLE = {
        "current": {"time": "2026-10-02T11:45", "interval": 900, "temperature_2m": 22.6,
                    "weather_code": 61, "cloud_cover": 100, "precipitation": 0.4,
                    "rain": 0.4, "snowfall": 0.0, "wind_speed_10m": 9.7, "is_day": 1},
        "daily": {"time": ["2026-10-02"], "sunrise": ["2026-10-02T05:37"],
                  "sunset": ["2026-10-02T17:25"]},
    }

    def test_parse_open_meteo(self):
        w = pw.parse_open_meteo(self.SAMPLE)
        self.assertEqual(w["kind"], "rain")
        self.assertEqual(w["temp"], 22.6)
        self.assertEqual(w["wind"], 9.7)
        sky = pw.Sky(at(12, 0), w)
        self.assertEqual((sky.rise, sky.sset), (5 * 60 + 37, 17 * 60 + 25))

    def test_cache_roundtrip_and_garbage(self):
        w = pw.parse_open_meteo(self.SAMPLE)
        pw.save_cache(w)
        self.assertEqual(pw.load_cache()["kind"], "rain")
        pw.cache_file().write_text("{not json")
        self.assertIsNone(pw.load_cache())
        pw.cache_file().write_text('{"kind": "volcano"}')
        self.assertIsNone(pw.load_cache())
        pw.cache_file().unlink()

    def test_config_parsing(self):
        d = pw.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            (d / "config.json").write_text('{"latitude": 35.681, "longitude": 139.767, '
                                           '"timezone": "Asia/Tokyo"}')
            cfg = pw.load_config()
            self.assertEqual(cfg["timezone"], "Asia/Tokyo")
            self.assertIn("latitude=35.6810", pw.api_url(cfg))
            self.assertIn("timezone=Asia%2FTokyo", pw.api_url(cfg))
            (d / "config.json").write_text('{"latitude": "north"}')
            self.assertIsNone(pw.load_config())
        finally:
            (d / "config.json").unlink()

    def test_failed_fetch_is_silent_and_keeps_last_weather(self):
        got = []
        th = pw.WeatherThread({"latitude": 0.0, "longitude": 0.0, "timezone": "auto"}, got.append)

        def down(*_a, **_k):
            th.stop.set()          # let run() do exactly one round
            raise OSError("network is down")

        with unittest.mock.patch.object(pw.urllib.request, "urlopen", side_effect=down) as m:
            th.run()               # must return quietly
        self.assertEqual(m.call_count, 1)
        self.assertEqual(got, [])
        self.assertFalse(pw.cache_file().exists())

    def test_successful_fetch_is_cached_and_published(self):
        got = []
        th = pw.WeatherThread({"latitude": 35.681, "longitude": 139.767, "timezone": "Asia/Tokyo"},
                              got.append)
        body = __import__("json").dumps(self.SAMPLE).encode()

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, _n=-1):
                th.stop.set()
                return body

        with unittest.mock.patch.object(pw.urllib.request, "urlopen", return_value=Resp()) as m:
            th.run()
        try:
            self.assertEqual(m.call_count, 1)
            self.assertIn("api.open-meteo.com", m.call_args[0][0].full_url)
            self.assertEqual([w["kind"] for w in got], ["rain"])
            self.assertEqual(pw.load_cache()["temp"], 22.6)
        finally:
            pw.cache_file().unlink()

    def test_stale_weather_decays_quietly(self):
        w = pw.parse_open_meteo(self.SAMPLE)
        now = w["fetched"]
        self.assertEqual(pw.freshen(w, now + 600)["temp"], 22.6)
        self.assertIsNone(pw.freshen(w, now + 4 * 3600)["temp"])          # no stale temperature
        self.assertEqual(pw.freshen(w, now + 4 * 3600)["kind"], "rain")
        self.assertEqual(pw.freshen(w, now + 13 * 3600)["kind"], "clear")  # nor a stale sky
        self.assertEqual(pw.freshen(pw.preset_weather("snow"), now + 1e9)["kind"], "snow")

    def test_hostile_numbers_in_files(self):
        self.assertIsNone(pw._num("1e400"))
        self.assertIsNone(pw._num(float("inf")))
        self.assertIsNone(pw._num(int("9" * 400)))
        self.assertEqual(pw._num("12.5"), 12.5)
        bad = dict(pw.preset_weather("rain"), cloud_cover=float("inf"), wind=float("nan"))
        pw.render_once(75, 45, at(12, 0), pw.sane_weather(bad), seed=1)
        d = pw.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            (d / "config.json").write_text('{"latitude": %s, "longitude": 1}' % ("9" * 400))
            self.assertIsNone(pw.load_config())
            (d / "config.json").write_text('[1, 2]')
            self.assertIsNone(pw.load_config())
        finally:
            (d / "config.json").unlink()

    def test_one_shot_frames_never_touch_the_network(self):
        d = pw.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text('{"latitude": 35.681, "longitude": 139.767}')
        try:
            self.assertIsNotNone(pw.load_config())     # a config that *would* allow fetching
            with unittest.mock.patch.object(pw.urllib.request, "urlopen",
                                            side_effect=AssertionError("network used")) as m:
                with open(os.devnull, "w") as sink, unittest.mock.patch("sys.stdout", sink):
                    self.assertEqual(pw.main(["--dump", "40x20"]), 0)
                    self.assertEqual(pw.main(["--once", "40x20"]), 0)
                    self.assertEqual(pw.main(["--dump", "40x20", "--offline"]), 0)
            self.assertEqual(m.call_count, 0)
        finally:
            (d / "config.json").unlink()


class VirtualTerminal:
    """Just enough of a terminal to replay what Frame.to_ansi emits."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.cells = [[pw.EMPTY] * cols for _ in range(rows)]
        self.x = self.y = 0
        self.fg = None

    def feed(self, text):
        i = 0
        while i < len(text):
            if text[i] == "\x1b":
                m = re.match(r"\x1b\[([0-9;?]*)([A-Za-z])", text[i:])
                self._csi(m.group(1), m.group(2))
                i += m.end()
                continue
            ch = text[i]
            w = pw.char_width(ch)
            self.cells[self.y][self.x] = (ch, self.fg)
            if w == 2:
                self.cells[self.y][self.x + 1] = (pw.CONT, self.fg)
            self.x += w
            i += 1

    def _csi(self, params, final):
        if final == "H":
            y, x = (int(v) for v in params.split(";"))
            self.y, self.x = y - 1, x - 1
        elif final == "m":
            p = params.split(";")
            if p[:2] == ["38", "2"]:
                self.fg = tuple(int(v) for v in p[2:5])
            elif p in (["39"], ["0"], [""]):
                self.fg = None
        elif final == "J":
            self.cells = [[pw.EMPTY] * self.cols for _ in range(self.rows)]

    def screen(self):
        # colour only matters where something is drawn
        return [[(ch, fg if ch != " " else None) for ch, fg in row] for row in self.cells]


class OutputTest(unittest.TestCase):
    def test_diffs_replay_to_the_same_screen(self):
        rng = random.Random(4)
        cols, rows = 23, 7
        glyphs = [" ", " ", " ", "x", "\u2588", "\u259a", "\u00b7", "\u6f22"]
        colours = [pw.shade(v / 4.0) for v in range(13)]
        vt = VirtualTerminal(cols, rows)
        prev = None
        frame = pw.Frame(cols, rows)
        for step in range(1500):
            frame = frame.copy()
            for _ in range(rng.randint(0, 12)):
                frame.put(rng.randrange(-1, cols), rng.randrange(rows),
                          "".join(rng.choice(glyphs) for _ in range(rng.randint(1, 4))),
                          rng.choice(colours))
            vt.feed(frame.to_ansi(prev))
            want = [[(ch, fg if ch not in (" ",) else None) for ch, fg in row] for row in frame.cells]
            self.assertEqual(vt.screen(), want, "step %d" % step)
            prev = frame.cells

    def test_unchanged_frame_writes_nothing(self):
        f = pw.render_once(75, 45, at(12, 0), pw.preset_weather("clear"), seed=1)
        self.assertEqual(f.to_ansi(f.copy().cells), "")
        self.assertTrue(f.to_ansi(None))

    def test_put_never_leaves_half_a_wide_char(self):
        f = pw.Frame(6, 1)
        f.put(1, 0, "\u6f22")
        f.put(0, 0, "\u5b57")
        self.assertEqual([c[0] for c in f.cells[0]][:3], ["\u5b57", pw.CONT, " "])
        f.put(1, 0, "a")
        self.assertEqual([c[0] for c in f.cells[0]][:2], [" ", "a"])


class KeysTest(unittest.TestCase):
    def feed(self, keys, *chunks, t=0.0):
        acts = []
        for c in chunks:
            acts += keys.feed(c, t)
        return acts

    def test_plain_keys(self):
        self.assertEqual(self.feed(pw.Keys(), b"q"), ["quit"])
        self.assertEqual(self.feed(pw.Keys(), b"Q"), ["quit"])
        self.assertEqual(self.feed(pw.Keys(), b"\x03"), ["quit"])
        self.assertEqual(self.feed(pw.Keys(), b"  x"), ["poke", "poke"])
        self.assertEqual(self.feed(pw.Keys(), b"abc"), [])

    def test_escape_sequences_are_swallowed(self):
        self.assertEqual(self.feed(pw.Keys(), b"\x1b[A\x1bOB\x1b[1;5q\x1bq"), [])   # arrows, Alt+q
        k = pw.Keys()
        self.assertEqual(self.feed(k, b"\x1b[1;", b"5A"), [])      # CSI split across reads
        self.assertFalse(k.pending)
        self.assertEqual(self.feed(k, b"q"), ["quit"])

    def test_paste_is_ignored_even_when_split(self):
        k = pw.Keys()
        self.assertEqual(self.feed(k, b"\x1b[200~quit q", b"q\x1b[2", b"01~"), [])
        self.assertFalse(k.pending)
        self.assertEqual(self.feed(k, b"q"), ["quit"])
        k = pw.Keys()
        self.assertEqual(self.feed(k, b"\x1b[20", b"0~qqq\x1b[201~", b" "), ["poke"])

    def test_an_unfinished_sequence_cannot_lock_the_keyboard(self):
        k = pw.Keys()
        self.assertEqual(self.feed(k, b"\x1b[200~half a paste", t=10.0), [])
        self.assertTrue(k.pending)
        k.expire(10.2)
        self.assertTrue(k.pending)
        k.expire(11.0)
        self.assertFalse(k.pending)
        self.assertEqual(self.feed(k, b"q", t=11.0), ["quit"])


def _read_until(fd, needle, timeout):
    out, end = b"", time.time() + timeout
    while time.time() < end and needle not in out:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


@unittest.skipUnless(hasattr(os, "fork") and os.path.exists("/dev/ptmx"), "needs a pty")
class PtyTest(unittest.TestCase):
    """The resident mode, in a private pseudo-terminal (never the real one)."""

    def spawn(self, args, shell):
        import fcntl
        import pty
        import struct
        import termios
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ, SHELL=shell, TERM="xterm-256color")
            try:
                os.execve(sys.executable, [sys.executable, str(TOOL)] + args, env)
            finally:
                os._exit(127)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 80, 0, 0))
        os.kill(pid, signal.SIGWINCH)
        self.addCleanup(self._reap, pid, fd)
        return pid, fd

    @staticmethod
    def _reap(pid, fd):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.close(fd)

    def marker_shell(self):
        path = os.path.join(_TMP.name, "marker-shell")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n"
                    "echo \"MARKER pid=$$ $(stty -a | tr ' ' '\\n' | grep -xE -- '-?(icanon|echo)' | tr '\\n' ' ')\"\n"
                    "read line\n")
        os.chmod(path, 0o755)
        return path

    def test_q_restores_the_terminal_and_execs_the_shell_in_place(self):
        pid, fd = self.spawn(["--offline", "--weather", "rain"], self.marker_shell())
        start = _read_until(fd, b"\x1b[?25l", 10)
        self.assertIn(b"\x1b[?1049h", start)
        os.write(fd, b"\x1b[200~q is pasted, not pressed\x1b[2")   # split paste end marker
        time.sleep(0.2)
        os.write(fd, b"01~")
        time.sleep(0.3)
        self.assertEqual(os.waitpid(pid, os.WNOHANG), (0, 0))
        os.write(fd, b"q")
        out = _read_until(fd, b"MARKER", 10)
        self.assertIn(b"\x1b[?1049l", out)
        self.assertIn(b"\x1b[?25h", out)
        self.assertLess(out.index(b"\x1b[?1049l"), out.index(b"MARKER"))
        line = out[out.index(b"MARKER"):].split(b"\r\n")[0].decode()
        self.assertIn("pid=%d" % pid, line)              # exec: same process
        self.assertIn(" icanon", " " + line)              # cooked mode again
        self.assertIn(" echo", " " + line)
        self.assertNotIn("Traceback", out.decode("utf-8", "replace"))

    def test_sigterm_restores_and_exits(self):
        pid, fd = self.spawn(["--offline"], self.marker_shell())
        _read_until(fd, b"\x1b[?25l", 10)
        os.kill(pid, signal.SIGTERM)
        out = _read_until(fd, b"\x1b[?1049l", 10)
        self.assertIn(b"\x1b[?1049l", out)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 0)
        self.assertNotIn(b"MARKER", out)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_started_from_an_interactive_shell_q_returns_to_it(self):
        # run from a prompt, q hands control back to that same shell instead
        # of exec'ing a nested one (if it nested, AFTER would never print)
        import pty
        bash = shutil.which("bash")
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ, PS1="$ ", TERM="xterm-256color")
            try:
                os.execve(bash, [bash, "--norc", "--noprofile", "-i"], env)
            finally:
                os._exit(127)
        self.addCleanup(self._reap, pid, fd)
        _read_until(fd, b"$ ", 10)
        os.write(fd, ("%s %s --offline; echo AFTER=$$\n" % (sys.executable, TOOL)).encode())
        _read_until(fd, b"\x1b[?25l", 10)
        os.write(fd, b"q")
        out = _read_until(fd, b"AFTER=%d" % pid, 10)
        self.assertIn(b"AFTER=%d" % pid, out, out[-300:])


class CliTest(unittest.TestCase):
    def test_dump_cli(self):
        res = run_cli("--dump", "75x45", "--weather", "rain", "--time", "17:30", "--seed", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.endswith("\n"))
        self.assertEqual(len(res.stdout[:-1].split("\n")), 45)
        self.assertNotIn("\x1b", res.stdout)

    def test_rabbit_flag_and_bad_args(self):
        res = run_cli("--dump", "75x45", "--rabbit", "binky", "--offline")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotEqual(run_cli("--dump", "75x45", "--weather", "hail").returncode, 0)
        self.assertNotEqual(run_cli("--dump", "75by45").returncode, 0)
        self.assertNotEqual(run_cli("--dump", "75x45", "--time", "25:00").returncode, 0)

    def test_needs_a_terminal_for_the_resident_mode(self):
        res = subprocess.run([sys.executable, str(TOOL), "--offline"], capture_output=True,
                             text=True, stdin=subprocess.DEVNULL, timeout=30)
        self.assertNotEqual(res.returncode, 0)

    def test_executable(self):
        self.assertTrue(os.access(TOOL, os.X_OK))


if __name__ == "__main__":
    unittest.main()
