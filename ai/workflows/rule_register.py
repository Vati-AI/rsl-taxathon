"""Named candidate rule register and policy-gap register (TAX-116, framework BP9/BP10).

The TAX-052 registry deliberately holds synthetic placeholders only. This register names
the framework's candidate legal anchors so officers can see which provision a finding
would rely on, but every entry is ``unverified``: the loader refuses a verified entry, so a
candidate can never pass the legal-route gate. ``route_for`` therefore always returns
``eligible_for_assessment: False``:

- ``candidate_unverified``: candidate anchors exist, awaiting RSL Legal;
- ``policy_gap``: the finding code is in the policy-gap register (no legal route);
- ``no_candidate``: nothing is registered for the code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
CANDIDATES_PATH = HERE / "rule_register.candidates.json"
GAPS_PATH = HERE / "policy_gap_register.json"


class RuleRegisterError(ValueError):
    pass


@dataclass(frozen=True)
class RuleRegister:
    version: str
    entries: tuple[dict[str, Any], ...]
    gaps: tuple[dict[str, Any], ...]

    def route_for(self, finding_code: str) -> dict[str, Any]:
        gaps = [g for g in self.gaps if finding_code in g["finding_codes"]]
        if gaps:
            return {
                "finding_code": finding_code,
                "route_status": "policy_gap",
                "candidates": [],
                "policy_gaps": [g["gap_id"] for g in gaps],
                "eligible_for_assessment": False,
            }
        cands = [e for e in self.entries if finding_code in e["finding_codes"]]
        return {
            "finding_code": finding_code,
            "route_status": "candidate_unverified" if cands else "no_candidate",
            "candidates": [{"rule_id": e["rule_id"], "instrument": e["instrument"]} for e in cands],
            "policy_gaps": [],
            "eligible_for_assessment": False,
        }


def _load(path: Path, key: str, ident: str) -> tuple[str, tuple[dict[str, Any], ...]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("status") != "candidate_unverified":
        raise RuleRegisterError(f"{path.name} must be marked candidate_unverified")
    items = doc.get(key)
    if not isinstance(items, list):
        raise RuleRegisterError(f"{path.name}: {key} must be a list")
    seen: set[str] = set()
    for item in items:
        if item.get("verification_status") != "unverified":
            raise RuleRegisterError(f"{item.get(ident)}: only unverified candidates are allowed")
        if any(k in item for k in ("reviewer", "reviewed_on", "verified_by")):
            raise RuleRegisterError(f"{item.get(ident)}: a candidate cannot record a review")
        if item[ident] in seen:
            raise RuleRegisterError(f"duplicate {ident} {item[ident]}")
        seen.add(item[ident])
        if not isinstance(item.get("finding_codes"), list):
            raise RuleRegisterError(f"{item[ident]}: finding_codes must be a list")
    return str(doc["register_version"]), tuple(items)


def load_rule_register(candidates: Path = CANDIDATES_PATH, gaps: Path = GAPS_PATH) -> RuleRegister:
    version, entries = _load(candidates, "entries", "rule_id")
    gap_version, gap_items = _load(gaps, "gaps", "gap_id")
    return RuleRegister(f"{version}+{gap_version}", entries, gap_items)
