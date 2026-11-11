"""The shared design tokens meet WCAG AA for every pair the components use (TAX-145)."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_contrast", ROOT / "scripts/check_contrast.py")
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ContrastTests(unittest.TestCase):
    def test_every_listed_pair_meets_wcag_aa(self) -> None:
        rows = check.measure(check.TOKENS.read_text(), json.loads(check.PAIRS.read_text())["pairs"])
        self.assertGreaterEqual(len(rows), 20)
        failed = [f"{r['fg']} on {r['bg']}: {r['ratio']}" for r in rows if not r["ok"]]
        self.assertEqual(failed, [])

    def test_the_ratio_matches_known_wcag_values(self) -> None:
        self.assertAlmostEqual(check.ratio("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(check.ratio("#ffffff", "#ffffff"), 1.0, places=2)
        self.assertAlmostEqual(check.ratio("#777", "#fff"), 4.48, places=2)

    def test_a_low_contrast_pair_is_reported_and_var_references_resolve(self) -> None:
        css = ":root {\n  --a: #777777;\n  --b: var(--c);\n  --c: #ffffff;\n}\n"
        [row] = check.measure(css, [{"fg": "--a", "bg": "--b", "kind": "text", "use": "x"}])
        self.assertFalse(row["ok"])
        self.assertEqual(row["bg_value"], "#ffffff")
        with self.assertRaises(ValueError):
            check.measure(css, [{"fg": "--missing", "bg": "--a", "kind": "text", "use": "x"}])


if __name__ == "__main__":
    unittest.main()
