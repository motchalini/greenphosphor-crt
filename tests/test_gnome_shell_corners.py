"""Tests for src/overrides/gnome-shell-corners.py (stdlib unittest).

Run with:  python3 -m unittest discover -s tests
"""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "src" / "overrides" / "gnome-shell-corners.py"
SHELL_CSS = ROOT / "themes" / "GreenPhosphor-CRT" / "gnome-shell" / "gnome-shell.css"
BUILD = (ROOT / "build.sh").read_text(encoding="utf-8")
MARKER = re.search(r"^BEGIN='(.*)'$", BUILD, re.M).group(1)

_spec = importlib.util.spec_from_file_location("gnome_shell_corners", GEN)
corners = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corners)


class CapTest(unittest.TestCase):
    def test_caps_each_length(self):
        self.assertEqual(corners.cap("14px"), "3px")
        self.assertEqual(corners.cap("9999px"), "3px")
        self.assertEqual(corners.cap("0 0 6px 6px !important"), "0 0 3px 3px !important")
        self.assertEqual(corners.cap("18px 0px 0px 18px"), "3px 0px 0px 3px")

    def test_leaves_small_values(self):
        self.assertEqual(corners.cap("0"), "0")
        self.assertEqual(corners.cap("2px"), "2px")
        self.assertEqual(corners.cap("2.5px"), "2.5px")
        self.assertEqual(corners.cap("7.5px"), "3px")


class GenerateTest(unittest.TestCase):
    CSS = """
    /* a comment { border-radius: 40px } */
    .a, .b .c { color: red; border-radius: 12px; }
    .d { border-radius: 0 !important; -arrow-border-radius: 9px; }
    .e { color: blue; }
    .user-icon, .f { border-radius: 9999px; }
    """ + MARKER + """
    .old { border-radius: 99px; }
    """

    def setUp(self):
        self.out = corners.generate(self.CSS, MARKER)

    def test_caps_and_reemits_every_radius_rule(self):
        self.assertIn(".a,\n.b .c {\n  border-radius: 3px;\n}", self.out)
        # unchanged values are re-emitted too, so the base cascade is mirrored
        self.assertIn(".d {\n  border-radius: 0 !important;\n  -arrow-border-radius: 3px;\n}",
                      self.out)

    def test_keep_selectors_keep_their_value(self):
        self.assertIn(".f {\n  border-radius: 3px;\n}", self.out)
        self.assertIn(".user-icon {\n  border-radius: 9999px;\n}", self.out)

    def test_ignores_comments_rules_without_radius_and_old_block(self):
        self.assertNotIn("40px", self.out)
        self.assertNotIn(".e", self.out)
        self.assertNotIn(".old", self.out)


class ShippedThemeTest(unittest.TestCase):
    """The built gnome-shell.css carries an up-to-date generated block."""

    def test_built_css_contains_current_output(self):
        css = SHELL_CSS.read_text(encoding="utf-8")
        self.assertIn(corners.generate(css, MARKER), css.split(MARKER, 1)[1])

    def test_no_uncapped_radius_survives_in_generated_block(self):
        css = SHELL_CSS.read_text(encoding="utf-8")
        for selectors, decls in corners.rules(corners.generate(css, MARKER)):
            if all(corners.KEEP.search(s) for s in selectors):
                continue
            for _, value in decls:
                for px in re.findall(r"(\d+(?:\.\d+)?)px", value):
                    self.assertLessEqual(float(px), corners.MAX_PX, (selectors, value))


if __name__ == "__main__":
    unittest.main()
