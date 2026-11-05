"""Generate or check the FtM schema subset used by the tax entity mapping (TAX-036).

The mapping in integration/followthemoney/tax_mapping.py must stay dependency-free so
`scripts/dev.py check` runs it with a plain interpreter. It validates entities against
integration/followthemoney/ftm_schema_subset.v1.json, which this script derives from the
locked FollowTheMoney source (integration/upstreams.lock.json, fetched with
`python3 scripts/upstreams.py fetch followthemoney`). Only the schemata and properties the
mapping emits are kept; inheritance (`extends`) is resolved from the YAML files.

    python3 scripts/ftm_schema_subset.py --check   # fails if the subset drifted from source
    python3 scripts/ftm_schema_subset.py --write   # regenerate after a reviewed lock bump

Needs PyYAML (not part of requirements-dev.lock), so CI does not run it; the generated
file records the source commit and a SHA-256 of every YAML file it read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "external" / "followthemoney"
SCHEMA_DIR = SOURCE / "followthemoney" / "schema"
OUTPUT = ROOT / "integration" / "followthemoney" / "ftm_schema_subset.v1.json"
LOCK = ROOT / "integration" / "upstreams.lock.json"

# Schema -> properties the mapping may emit (own or inherited).
USED: dict[str, list[str]] = {
    "LegalEntity": ["name", "taxNumber", "registrationNumber", "idNumber", "country", "notes"],
    "Company": [
        "name",
        "taxNumber",
        "registrationNumber",
        "idNumber",
        "country",
        "incorporationDate",
        "notes",
    ],
    "Person": [
        "name",
        "firstName",
        "lastName",
        "taxNumber",
        "idNumber",
        "nationality",
        "birthDate",
        "country",
        "notes",
    ],
    "Ownership": [
        "owner",
        "asset",
        "percentage",
        "ownershipType",
        "startDate",
        "endDate",
        "recordId",
        "proof",
        "description",
    ],
    "Contract": ["title", "name", "authority", "contractDate", "description", "proof"],
    "ContractAward": ["contract", "supplier", "recordId", "proof", "role"],
    "UnknownLink": ["subject", "object", "role", "date", "recordId", "proof", "description"],
    "Payment": [
        "payer",
        "beneficiary",
        "amount",
        "currency",
        "date",
        "recordId",
        "purpose",
        "contract",
        "proof",
    ],
    "Document": ["fileName", "title", "mimeType", "fileSize", "date"],
    # TAX-074 scheme / directorship / adviser graph (integration/followthemoney/scheme_graph.py).
    "Directorship": [
        "director",
        "organization",
        "role",
        "startDate",
        "endDate",
        "recordId",
        "proof",
        "description",
    ],
    "Representation": [
        "agent",
        "client",
        "role",
        "startDate",
        "endDate",
        "recordId",
        "proof",
        "description",
    ],
    "Project": ["name", "projectId", "description", "startDate", "endDate", "proof"],
    "ProjectParticipant": [
        "project",
        "participant",
        "role",
        "startDate",
        "endDate",
        "recordId",
        "proof",
    ],
}


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        raise SystemExit("PyYAML is needed: install it in a scratch environment, not .venv-dev")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or len(data) != 1:
        raise SystemExit(f"Unexpected schema file layout: {path.name}")
    return data


def build() -> dict[str, Any]:
    if not SCHEMA_DIR.is_dir():
        raise SystemExit(
            "Fetch the locked source first: python3 scripts/upstreams.py fetch followthemoney"
        )
    commit = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    locked = json.loads(LOCK.read_text(encoding="utf-8"))["followthemoney"]["commit"]
    if commit != locked:
        raise SystemExit(f"external/followthemoney is at {commit}, lock says {locked}")
    raw: dict[str, dict[str, Any]] = {}
    files: dict[str, str] = {}
    for path in sorted(SCHEMA_DIR.glob("*.yaml")):
        for name, body in _load_yaml(path).items():
            raw[name] = body or {}
            # Hash LF bytes so a Windows checkout (CRLF) records the same digest.
            body_bytes = path.read_bytes().replace(b"\r\n", b"\n")
            files[path.name] = hashlib.sha256(body_bytes).hexdigest()

    def ancestors(name: str) -> list[str]:
        seen: list[str] = []
        stack = [name]
        while stack:
            current = stack.pop(0)
            if current in seen:
                continue
            seen.append(current)
            stack.extend(raw[current].get("extends") or [])
        return seen

    def properties(name: str) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for schema in reversed(ancestors(name)):
            for prop, spec in (raw[schema].get("properties") or {}).items():
                spec = spec or {}
                entry = {"type": spec.get("type", "string"), "defined_in": schema}
                if spec.get("range"):
                    entry["range"] = spec["range"]
                merged[prop] = entry
        return merged

    def required(name: str) -> list[str]:
        # followthemoney/schema.py reads `required` from the schema's own YAML only; it
        # is not inherited through `extends`.
        return sorted(raw[name].get("required") or [])

    schemata: dict[str, Any] = {}
    ranges: set[str] = set()
    used_files: set[str] = set()
    for name, props in USED.items():
        available = properties(name)
        missing = [p for p in props if p not in available]
        if missing:
            raise SystemExit(f"{name} has no properties {missing} in the locked FtM source")
        kept = {p: available[p] for p in sorted(props)}
        ranges.update(v["range"] for v in kept.values() if "range" in v)
        schemata[name] = {
            "abstract": bool(raw[name].get("abstract", False)),
            "is_a": sorted(ancestors(name)),
            "required": required(name),
            "edge": raw[name].get("edge", {}).get("source") is not None,
            "properties": kept,
        }
        used_files.update(f"{s}.yaml" for s in ancestors(name))
    for name in sorted(ranges - set(schemata)):
        used_files.update(f"{s}.yaml" for s in ancestors(name))
    return {
        "source": {
            "repository": "https://github.com/opensanctions/followthemoney",
            "commit": commit,
            "files_sha256": {f: files[f] for f in sorted(used_files)},
        },
        "range_schemata": sorted(ranges),
        "schemata": schemata,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.write_bytes(text.encode("utf-8"))
        print(f"Wrote {OUTPUT.relative_to(ROOT)}")
        return
    current = OUTPUT.read_bytes().decode("utf-8") if OUTPUT.exists() else ""
    if current != text:
        sys.exit("ftm_schema_subset.v1.json differs from the locked FtM source; rerun --write")
    print("FtM schema subset matches the locked source")


if __name__ == "__main__":
    main()
