"""Run the same first-party code-quality checks locally and in CI."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    "scripts",
    "tests/tooling",
    "tests/contracts",
    "backend/vati_tax/vati_tax",
    "ai",
    "integration/api",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fix", action="store_true", help="Format first-party code; review the diff"
    )
    args = parser.parse_args()
    python = ROOT / ".venv-dev" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        raise SystemExit(
            "Create .venv-dev and install requirements-dev.lock; see docs/TOOLCHAIN.md"
        )
    npm = shutil.which("npm")
    if not npm or not (ROOT / "node_modules").exists():
        raise SystemExit("Install Node 22.13+ and run npm ci; see docs/TOOLCHAIN.md")
    if args.fix:
        subprocess.run([str(python), "-m", "ruff", "format", *SOURCES], cwd=ROOT, check=True)
    commands = [
        [str(python), "-m", "ruff", "check", *(["--fix"] if args.fix else []), *SOURCES],
        [str(python), "-m", "ruff", "format", *([] if args.fix else ["--check"]), *SOURCES],
        [str(python), "-m", "mypy"],
        [npm, "run", "format" if args.fix else "check"],
    ]
    if args.fix:
        commands.append([npm, "run", "lint"])
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
    print("First-party quality checks passed")


if __name__ == "__main__":
    main()
