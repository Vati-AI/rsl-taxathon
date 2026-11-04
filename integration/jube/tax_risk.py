"""Versioned, explainable synthetic tax-risk rules (TAX-035).

The ruleset JSON is the reviewable source of truth. This module loads and validates it,
derives rule inputs from linked synthetic records (TAX-024 shape), evaluates every rule
deterministically and renders the equivalent Jube activation-rule scripts and invoke payload.

Outputs are review signals with rule version, input values and evidence IDs. They are never
tax assessments, refund decisions, fraud findings or legal conclusions.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RULESET_PATH = ROOT / "integration" / "jube" / "rules" / "tax_risk_rules.v1.json"
RECORDS_PATH = ROOT / "data" / "synthetic" / "tax024_records.json"

CENT = Decimal("0.01")
SEMVER = re.compile(r"\d+\.\d+\.\d+")
PASCAL = re.compile(r"[A-Z][A-Za-z0-9]*")
OPERATORS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}
JUBE_OPERATORS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "==": "="}
FEATURE_TYPES = {"money": 3, "integer": 2}  # Jube RequestXPath dataTypeId: 3 float, 2 integer
EVIDENCE_SELECTORS = {
    "return",
    "return_and_vat_bearing_purchase_invoices",
    "related_party_contracts_and_invoices",
    "unverified_imported_service_contracts_and_invoices",
}


class RulesetError(ValueError):
    """The ruleset file is not a valid, reviewable configuration."""


def money(value: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"money must be a decimal string, got {value!r}")
    return Decimal(value).quantize(CENT)


def load_ruleset(path: Path = RULESET_PATH) -> dict[str, Any]:
    ruleset = json.loads(path.read_text(encoding="utf-8"))
    validate_ruleset(ruleset)
    return ruleset


def validate_ruleset(ruleset: dict[str, Any]) -> None:
    if ruleset.get("synthetic") is not True:
        raise RulesetError("ruleset must be marked synthetic")
    if not SEMVER.fullmatch(str(ruleset.get("ruleset_version", ""))):
        raise RulesetError("ruleset_version must be semantic (x.y.z)")
    features = {}
    for feature in ruleset.get("features", []):
        if feature.get("type") not in FEATURE_TYPES:
            raise RulesetError(f"feature {feature.get('name')} has unknown type")
        if not PASCAL.fullmatch(str(feature.get("jube_name", ""))):
            raise RulesetError(f"feature {feature.get('name')} needs a PascalCase jube_name")
        if feature["name"] in features:
            raise RulesetError(f"duplicate feature {feature['name']}")
        features[feature["name"]] = feature
    if len({f["jube_name"] for f in features.values()}) != len(features):
        raise RulesetError("duplicate jube_name")
    rule_ids = set()
    rules = ruleset.get("rules", [])
    if not rules:
        raise RulesetError("ruleset has no rules")
    for rule in rules:
        rule_id = rule.get("rule_id")
        if not rule_id or rule_id in rule_ids:
            raise RulesetError(f"missing or duplicate rule_id {rule_id!r}")
        rule_ids.add(rule_id)
        if not SEMVER.fullmatch(str(rule.get("version", ""))):
            raise RulesetError(f"{rule_id}: version must be semantic (x.y.z)")
        if not rule.get("signal_code") or not rule.get("explanation"):
            raise RulesetError(f"{rule_id}: signal_code and explanation are required")
        if rule.get("evidence") not in EVIDENCE_SELECTORS:
            raise RulesetError(f"{rule_id}: unknown evidence selector {rule.get('evidence')!r}")
        conditions = [
            rule.get("condition"),
            rule.get("applies_when"),
            rule.get("cleared_condition"),
        ]
        if not rule.get("condition"):
            raise RulesetError(f"{rule_id}: condition is required")
        if bool(rule.get("cleared_code")) != bool(rule.get("cleared_condition")):
            raise RulesetError(f"{rule_id}: cleared_code and cleared_condition go together")
        for condition in filter(None, conditions):
            if condition.get("feature") not in features:
                raise RulesetError(f"{rule_id}: unknown feature {condition.get('feature')!r}")
            if condition.get("operator") not in OPERATORS:
                raise RulesetError(f"{rule_id}: unsupported operator {condition.get('operator')!r}")
            Decimal(str(condition.get("value")))
        for name in rule.get("inputs", []):
            if name not in features:
                raise RulesetError(f"{rule_id}: unknown input {name!r}")


def quarter_index(period: str) -> int:
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if not match:
        raise ValueError(f"period must look like 2025-Q4, got {period!r}")
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def consecutive_losses(period: str, loss_periods: list[str]) -> int:
    losses = {quarter_index(p) for p in loss_periods}
    current, run = quarter_index(period), 0
    while current - run in losses:
        run += 1
    return run


def derive_features(record: dict[str, Any]) -> dict[str, Decimal]:
    """Deterministic rule inputs from one linked synthetic record."""
    tax_return = record["return"]
    contracts = {c["id"]: c for c in record["contracts"]}
    purchases = [i for i in record["invoices"] if i["direction"] == "purchase"]
    claimed = money(tax_return["input_vat_claimed"])
    documented = sum((money(i["vat_amount"]) for i in purchases), Decimal("0.00"))
    related = sum(
        (money(i["net_amount"]) for i in purchases if contracts[i["contract_id"]]["related_party"]),
        Decimal("0.00"),
    )
    return {
        "refund_claim": money(tax_return["refund_claim"]),
        "claimed_input_vat": claimed,
        "documented_input_vat": documented,
        "unsupported_input_vat": claimed - documented,
        "related_party_purchase_net": related,
        "consecutive_loss_periods": Decimal(
            consecutive_losses(record["period"], record.get("loss_periods", []))
        ),
        "imported_service_unverified_count": Decimal(
            sum(1 for i in purchases if i.get("vat_status") == "treatment_unverified")
        ),
    }


def select_evidence(selector: str, record: dict[str, Any]) -> list[str]:
    contracts = {c["id"]: c for c in record["contracts"]}
    purchases = [i for i in record["invoices"] if i["direction"] == "purchase"]
    if selector == "return":
        return [record["return"]["id"]]
    if selector == "return_and_vat_bearing_purchase_invoices":
        return [record["return"]["id"]] + [i["id"] for i in purchases if money(i["vat_amount"]) > 0]
    if selector == "related_party_contracts_and_invoices":
        chosen = [i for i in purchases if contracts[i["contract_id"]]["related_party"]]
    elif selector == "unverified_imported_service_contracts_and_invoices":
        chosen = [i for i in purchases if i.get("vat_status") == "treatment_unverified"]
    else:
        raise RulesetError(f"unknown evidence selector {selector!r}")
    contract_ids = list(dict.fromkeys(i["contract_id"] for i in chosen))
    return contract_ids + [i["id"] for i in chosen]


def format_value(feature: dict[str, Any], value: Decimal) -> str:
    return f"{value.quantize(CENT)}" if feature["type"] == "money" else str(int(value))


def holds(condition: dict[str, Any] | None, values: dict[str, Decimal]) -> bool:
    if condition is None:
        return True
    compare = OPERATORS[condition["operator"]]
    return bool(compare(values[condition["feature"]], Decimal(str(condition["value"]))))


def describe(condition: dict[str, Any]) -> str:
    return f"{condition['feature']} {condition['operator']} {condition['value']}"


def record_digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def evaluate_record(ruleset: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    """One explainable result per rule: signal, cleared, no_signal or not_applicable."""
    if record.get("synthetic") is not True:
        raise ValueError("only synthetic records may be evaluated in this prototype")
    features = {f["name"]: f for f in ruleset["features"]}
    values = derive_features(record)
    shown = {name: format_value(features[name], value) for name, value in values.items()}
    currency = ruleset["currency"]
    digest = record_digest(record)
    results = []
    for rule in ruleset["rules"]:
        inputs = {name: shown[name] for name in rule["inputs"]}
        if not holds(rule.get("applies_when"), values):
            outcome, code = "not_applicable", None
        elif holds(rule["condition"], values):
            outcome, code = "signal", rule["signal_code"]
        elif rule.get("cleared_condition") and holds(rule["cleared_condition"], values):
            outcome, code = "cleared", rule["cleared_code"]
        else:
            outcome, code = "no_signal", None
        template = {"signal": rule["explanation"], "cleared": rule.get("cleared_explanation")}.get(
            outcome
        )
        labelled = {
            name: (f"{currency} {text}" if features[name]["type"] == "money" else text)
            for name, text in inputs.items()
        }
        results.append(
            {
                "case_id": record["case_id"],
                "taxpayer_id": record["taxpayer_id"],
                "period": record["period"],
                "ruleset_id": ruleset["ruleset_id"],
                "ruleset_version": ruleset["ruleset_version"],
                "rule_id": rule["rule_id"],
                "rule_version": rule["version"],
                "family": rule["family"],
                "outcome": outcome,
                "code": code,
                "condition": describe(rule["condition"]),
                "inputs": inputs,
                "evidence_ids": select_evidence(rule["evidence"], record) if code else [],
                "input_digest": digest,
                "explanation": template.format(**labelled) if template else None,
                "never_implies": rule.get("never_implies", []),
                "synthetic": True,
            }
        )
    return results


def evaluate_all(
    ruleset: dict[str, Any] | None = None, records: list[dict[str, Any]] | None = None
) -> dict[str, list[dict[str, Any]]]:
    ruleset = ruleset or load_ruleset()
    if records is None:
        records = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
    return {record["case_id"]: evaluate_record(ruleset, record) for record in records}


# --- Jube rendering -------------------------------------------------------------------------


def jube_rule_name(rule: dict[str, Any]) -> str:
    return f"{rule['rule_id']} v{rule['version']}"


def jube_literal(value: Any) -> str:
    number = Decimal(str(value))
    if number != number.to_integral_value():
        raise RulesetError("Jube thresholds are kept integral to avoid decimal tokens")
    return str(int(number))


def jube_script(ruleset: dict[str, Any], rule: dict[str, Any]) -> str:
    """VB.NET activation-rule fragment in Jube's documented dot notation."""
    names = {f["name"]: f["jube_name"] for f in ruleset["features"]}
    clauses = [
        f"Payload.{names[c['feature']]} {JUBE_OPERATORS[c['operator']]} {jube_literal(c['value'])}"
        for c in (rule.get("applies_when"), rule["condition"])
        if c
    ]
    return "If " + " AND ".join(clauses) + " Then\nReturn True\nEnd If"


def jube_payload(ruleset: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Invoke payload: identifiers plus derived inputs as decimal strings (no binary floats)."""
    features = ruleset["features"]
    values = derive_features(record)
    year, quarter = record["period"].split("-Q")
    end_month = int(quarter) * 3
    payload: dict[str, Any] = {
        ruleset["jube_model"]["entry_field"]: record["case_id"],
        "taxpayer_id": record["taxpayer_id"],
        ruleset["jube_model"]["reference_date_field"]: f"{year}-{end_month:02d}-28T00:00:00Z",
        "synthetic": True,
        "ruleset_version": ruleset["ruleset_version"],
    }
    for feature in features:
        payload[feature["jube_name"]] = format_value(feature, values[feature["name"]])
    return payload
