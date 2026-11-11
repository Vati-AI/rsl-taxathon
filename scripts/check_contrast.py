"""Measure WCAG 2.1 contrast for the shared design tokens (TAX-145, DESIGN_STANDARD.md v2).

Reads the :root custom properties in apps/shared/design/tokens.css (resolving var()
references), takes every pair listed in apps/shared/design/contrast-pairs.json and
computes the WCAG relative-luminance contrast ratio. Text needs 4.5:1, large text and
UI indicators (focus ring) 3:1. Exit status 1 if any pair is below its minimum.

    python3 scripts/check_contrast.py              # print the table
    python3 scripts/check_contrast.py --markdown   # the table as Markdown (evidence)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "apps/shared/design/tokens.css"
PAIRS = ROOT / "apps/shared/design/contrast-pairs.json"
MINIMUM = {"text": 4.5, "large": 3.0, "ui": 3.0}
_DECL = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
_VAR = re.compile(r"^var\((--[a-z0-9-]+)\)$")
_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def root_tokens(css: str) -> dict[str, str]:
    """The custom properties declared in the first :root block."""
    start = css.index(":root")
    body = css[css.index("{", start) + 1 : css.index("\n}", start)]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return {name: value.strip() for name, value in _DECL.findall(body)}


def resolve(tokens: dict[str, str], name: str, seen: frozenset[str] = frozenset()) -> str:
    if name in seen:
        raise ValueError(f"{name}: circular var() reference")
    if name not in tokens:
        raise ValueError(f"{name}: not declared in tokens.css")
    value = tokens[name]
    ref = _VAR.match(value)
    return resolve(tokens, ref.group(1), seen | {name}) if ref else value


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    match = _HEX.match(hex_colour)
    if not match:
        raise ValueError(f"{hex_colour}: only #rgb or #rrggbb colours are measured")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def ratio(fg: str, bg: str) -> float:
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def measure(css: str, pairs: list[dict[str, str]]) -> list[dict[str, object]]:
    tokens = root_tokens(css)
    rows = []
    for pair in pairs:
        fg, bg = resolve(tokens, pair["fg"]), resolve(tokens, pair["bg"])
        value = round(ratio(fg, bg), 2)
        minimum = MINIMUM[pair["kind"]]
        rows.append({**pair, "fg_value": fg, "bg_value": bg, "ratio": value,
                     "minimum": minimum, "ok": value >= minimum})  # fmt: skip
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    rows = measure(TOKENS.read_text(), json.loads(PAIRS.read_text())["pairs"])
    if args.markdown:
        print("| Use | Foreground | Background | Ratio | Minimum | Result |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            result = "pass" if r["ok"] else "**fail**"
            print(f"| {r['use']} | `{r['fg']}` {r['fg_value']} | `{r['bg']}` {r['bg_value']} "
                  f"| {r['ratio']}:1 | {r['minimum']}:1 | {result} |")  # fmt: skip
    else:
        for r in rows:
            mark = "ok  " if r["ok"] else "FAIL"
            print(
                f"{mark} {r['ratio']:>5}:1 (min {r['minimum']}) {r['fg']} on {r['bg']}  {r['use']}"
            )
    failed = [r for r in rows if not r["ok"]]
    if failed:
        print(f"{len(failed)} pair(s) below WCAG AA", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
