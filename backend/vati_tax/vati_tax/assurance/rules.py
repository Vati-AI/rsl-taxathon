"""Pure rules for running the TAX-112-116 assurance checks in the pipeline (TAX-121, ADR-042).

No Frappe import. ``store`` binds these rules to the outbox, the findings record and the
``Vati Lane Proposal`` / ``Vati Assurance Run`` DocTypes; ``engine`` records lane proposals
and runs idempotently over a repository port.

- ``request_event`` / ``parse_request``: the ``assurance.requested`` outbox event of one
  taxpayer send (references only, no amounts).
- ``bundle_csv`` / ``parse_bundle``: the taxpayer's own supporting records (SAF-T-style
  books, bank file, agreements, permits, certificates, loss explanations, copies) as one
  CSV evidence document, so they are stored, hashed and read back like any evidence.
- ``finding_bodies``: one ``assess_taxpayer`` result (ai/evaluation/assurance_pipeline.py)
  as TAX-086 finding bodies with producer ``vati_check``: category, kind, a neutral
  explanation from the check's reason, evidence references (the taxpayer's records) and
  RSL source references (ADR-040: references, never copies), the unresolved amount, the
  alternative explanations the check tested, the TAX-116 candidate route (legal route
  ``unverified``, or ``no_verified_route`` for a policy gap) and a proposed action that
  always needs human approval.
- ``lane_proposal``: the TAX-114 lane of one claim (lane, reasons, service target and due
  date, claimed, undisputed and disputed amounts, inputs used, line matches).
- ``source_status`` / ``run_status``: which RSL systems answered; a required system that
  did not answer means the checks did not run (``not_run``), never a clean result.

Nothing here decides, pays, denies or assesses: every lane is a proposal for an officer,
every amount is advisory and every route is an unverified candidate.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from vati_tax.findings import rules as finding_rules
from vati_tax.ids.canonical import InvalidMapping, canonical_uuid

PRODUCER: Final = "vati_check"
EVENT_REQUESTED: Final = "assurance.requested"
SOURCE_SERVICE: Final = "frappe"
RULESET_ID: Final = "vati.assurance"
BUNDLE_FORMAT: Final = "vati.taxpayer_records.v1"
BUNDLE_FILENAME: Final = "taxpayer-records.csv"
BUNDLE_DOCUMENT_TYPE: Final = "financial_statement"
CURRENCY: Final = "LSL"

# TAX-118 issue -> (finding category, kind, proposed action, confidence).
ISSUES: Final[dict[str, tuple[str, str, str, str]]] = {
    "fake_invoices": ("refund_reconciliation", "missing_evidence", "request_evidence", "high"),
    "untaxed_imported_services": (
        "imported_services", "unverified_evidence", "request_evidence", "medium",
    ),
    "disguised_employment": (
        "worker_classification", "contradictory_evidence", "specialist_review", "medium",
    ),
    "related_party_loss": ("related_party", "observed_fact", "specialist_review", "medium"),
    "policy_gap": ("policy_gap", "observed_fact", "policy_gap_review", "medium"),
    # TAX-139: a persistent credit no structural explanation covers; evidence is requested.
    "persistent_credit": (
        "refund_reconciliation", "missing_evidence", "request_evidence", "medium",
    ),
}  # fmt: skip
LANES: Final = ("fast", "standard", "investigation")

# RSL source kinds the checks read (assurance_pipeline.SOURCE_KINDS). Without e-Tax or the
# Online Refunds Application there is no declared position or claim: the checks do not run.
SOURCE_KINDS: Final = (
    ("etax", "vat_return"),
    ("etax", "wht_return"),
    ("etax", "cit_return"),
    ("idms", "invoice"),
    ("online_refunds", "claim"),
    ("rsl_fraud_model", "score"),
    ("asycuda", "entry"),
    ("central_bank", "cross_border_payment"),
)
REQUIRED_SYSTEMS: Final = frozenset({"etax", "online_refunds"})
# Which checks lose an input when an optional system does not answer.
DEGRADES: Final[dict[str, tuple[str, ...]]] = {
    "idms": ("idms_line_match", "refund_lane"),
    "rsl_fraud_model": ("refund_lane",),
    "asycuda": ("idms_line_match", "persistent_credit"),
    "central_bank": ("three_way_match",),
    "registry": ("idms_line_match",),
}
CHECKS: Final = (
    "source_recompute",
    "three_way_match",
    "related_party_signals",
    "work_arrangement",
    "persistent_credit",
    "rule_register",
    "idms_line_match",
    "refund_lane",
)
_AMOUNT = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$")
_PREFIXED = re.compile(r"^(bank|ledger|document|saft):")


class InvalidAssuranceInput(InvalidMapping):
    """The check result or the taxpayer's records do not have the expected shape."""


def _fail(message: str, field: str, reason: str) -> InvalidAssuranceInput:
    return InvalidAssuranceInput(message, field, reason)


def sha256_text(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- outbox event -------------------------------------------------------------------


def request_event(submission_id: str, version: int) -> tuple[str, dict[str, Any]]:
    """Idempotency key and payload of ``assurance.requested`` for one send (references only)."""
    submission = canonical_uuid(submission_id, "submission_id")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidMapping("version must be a positive integer.", "version")
    key = f"vati:assurance:{submission}:v{version}"
    return key, {"submission_id": submission, "submission_version": version}


def parse_request(payload: Mapping[str, Any]) -> tuple[str, int]:
    if set(payload) != {"submission_id", "submission_version"}:
        raise InvalidMapping("Unexpected assurance payload.", "payload")
    version = payload["submission_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidMapping("submission_version must be a positive integer.", "payload")
    return canonical_uuid(payload["submission_id"], "submission_id"), version


# --- the taxpayer's supporting records as one CSV evidence document -----------------

_BUNDLE_HEADER = ("format", "path", "kind", "key", "value_json")


def bundle_csv(records: Mapping[str, Any]) -> bytes:
    """Encode the taxpayer's records losslessly as CSV (one record per row, JSON values).

    ``path`` is the top-level field (``saft.<part>`` for the books); ``kind`` is
    ``list`` / ``dict`` (a container marker, so empty ones survive), ``item`` (list
    element, ``key`` = index), ``entry`` (mapping entry, ``key`` = record ID) or
    ``value`` (a scalar or a small object stored whole).
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(_BUNDLE_HEADER)

    def put(path: str, value: Any, *, split_dict: bool) -> None:
        if isinstance(value, list):
            writer.writerow((BUNDLE_FORMAT, path, "list", "", ""))
            for index, item in enumerate(value):
                writer.writerow((BUNDLE_FORMAT, path, "item", str(index), _json(item)))
        elif isinstance(value, dict) and split_dict:
            writer.writerow((BUNDLE_FORMAT, path, "dict", "", ""))
            for key, item in value.items():
                writer.writerow((BUNDLE_FORMAT, path, "entry", str(key), _json(item)))
        else:
            writer.writerow((BUNDLE_FORMAT, path, "value", "", _json(value)))

    for field, value in records.items():
        if field == "saft" and isinstance(value, dict):
            writer.writerow((BUNDLE_FORMAT, "saft", "dict", "", ""))
            for part, rows in value.items():
                put(f"saft.{part}", rows, split_dict=False)
        else:
            put(field, value, split_dict=field == "documents")
    return out.getvalue().encode("utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_bundle(content: bytes) -> dict[str, Any]:
    """Decode ``bundle_csv`` output; anything else is ``InvalidAssuranceInput``."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise _fail("The records file is not UTF-8.", "bundle", "invalid_encoding") from None
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(rows[0]) != _BUNDLE_HEADER:
        raise _fail("Not a taxpayer records file.", "bundle", "invalid_format")
    out: dict[str, Any] = {}

    def container(path: str) -> tuple[dict[str, Any], str]:
        if path.startswith("saft."):
            saft = out.get("saft")
            if not isinstance(saft, dict):
                raise _fail("Books part before the books.", "bundle", "invalid_format")
            return saft, path.removeprefix("saft.")
        return out, path

    for line, row in enumerate(rows[1:], start=2):
        if len(row) != len(_BUNDLE_HEADER) or row[0] != BUNDLE_FORMAT:
            raise _fail(f"Row {line} is not a records row.", "bundle", "invalid_format")
        _format, path, kind, key, value = row
        parent, name = container(path)
        try:
            if kind == "list":
                parent[name] = []
            elif kind == "dict":
                parent[name] = {}
            elif kind == "item":
                if not isinstance(parent.get(name), list) or int(key) != len(parent[name]):
                    raise ValueError
                parent[name].append(json.loads(value))
            elif kind == "entry":
                if not isinstance(parent.get(name), dict):
                    raise ValueError
                parent[name][key] = json.loads(value)
            elif kind == "value":
                parent[name] = json.loads(value)
            else:
                raise ValueError
        except ValueError:
            raise _fail(f"Row {line} is malformed.", "bundle", "invalid_format") from None
    for field in ("tin", "saft"):
        if field not in out:
            raise _fail(f"The records file has no {field}.", "bundle", "invalid_format")
    return out


# --- source references --------------------------------------------------------------


def source_status(envelopes: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """``ok`` / ``unavailable`` per RSL system; a system is ``ok`` only if every read was."""
    status: dict[str, str] = {}
    for env in envelopes:
        system = str(env.get("source_system"))
        ok = env.get("status") == "ok"
        status[system] = "ok" if ok and status.get(system, "ok") == "ok" else "unavailable"
    return dict(sorted(status.items()))


def source_index(envelopes: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Record ID -> contract ``RslSourceRef`` (without locator) from fixture read envelopes."""
    index: dict[str, dict[str, Any]] = {}
    for env in envelopes:
        for ref in env.get("references") or []:
            index[str(ref["record_id"])] = {
                "source_system": str(ref["source_system"]),
                "record_id": str(ref["record_id"]),
                "contract_id": str(ref["contract_id"]),
                "record_sha256": str(ref["record_sha256"]),
            }
    return index


def run_status(statuses: Mapping[str, str]) -> tuple[str, str | None, dict[str, str]]:
    """Run status, reason and per-check status from the RSL systems' answers.

    A required system that did not answer stops the run (``not_run``): the checks would
    otherwise see no declared position and look clean. An optional one degrades the checks
    that read it; they still run on what is there and say so.
    """
    missing = sorted(s for s in REQUIRED_SYSTEMS if statuses.get(s) != "ok")
    if missing:
        return "not_run", f"{missing[0]}_unavailable", {check: "not_run" for check in CHECKS}
    checks = {check: "ran" for check in CHECKS}
    for system, affected in DEGRADES.items():
        if statuses.get(system, "ok") != "ok":
            for check in affected:
                checks[check] = f"degraded_{system}_unavailable"
    degraded = any(value != "ran" for value in checks.values())
    return ("degraded" if degraded else "completed"), None, checks


def cite(
    ids: Iterable[object],
    *,
    sources: Mapping[str, Mapping[str, Any]],
    bundle_evidence_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a check's cited IDs into RSL source references and taxpayer-record references.

    An ID the RSL sources served is a source reference; a claim line (``ORA-...-L03``) is
    a reference to its claim with the line as locator. Every other ID is one of the
    taxpayer's own records (book entries, bank lines, agreements, permits, copies) and is
    cited in the stored records document with the ID as its locator.
    """
    source_refs: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, str | None]] = set()
    seen_records: set[str] = set()
    for raw in ids:
        ident = str(raw)
        ref, locator = sources.get(ident), None
        if ref is None and not _PREFIXED.match(ident) and "-" in ident:
            parent = ident.rsplit("-", 1)[0]
            if parent in sources and sources[parent]["source_system"] == "online_refunds":
                ref, locator = sources[parent], ident
        if ref is not None:
            key = (ref["record_id"], locator)
            if key not in seen_sources:
                seen_sources.add(key)
                source_refs.append({**ref, "locator": locator})
            continue
        if ident not in seen_records:
            seen_records.add(ident)
            evidence_refs.append(
                {
                    "evidence_id": bundle_evidence_id,
                    "locator": {"page": None, "record": ident[:255]},
                }
            )
    return source_refs, evidence_refs


# --- findings -----------------------------------------------------------------------


def _neutral(text: object, fallback: str) -> str:
    """The check's own reason when it labels nobody, else the neutral fallback."""
    if isinstance(text, str) and text.strip() and not finding_rules.labelling_terms(text):
        return text.strip()[: finding_rules.MAX_EXPLANATION]
    return fallback


def _money(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    amount = value.get("amount") if isinstance(value, Mapping) else value
    try:
        text = str(Decimal(str(amount)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        raise _fail("Amount is not a decimal.", "amount", "invalid_amount") from None
    if not _AMOUNT.fullmatch(text):
        raise _fail("Amount is not a decimal.", "amount", "invalid_amount")
    currency = value.get("currency", CURRENCY) if isinstance(value, Mapping) else CURRENCY
    return {"amount": text, "currency": str(currency)}


def _route(route: Mapping[str, Any], register_version: str) -> dict[str, Any]:
    return {
        "route_status": route["route_status"],
        "candidates": [
            {"rule_id": str(c["rule_id"]), "instrument": str(c["instrument"])}
            for c in route.get("candidates", [])
        ],
        "policy_gaps": [str(g) for g in route.get("policy_gaps", [])],
        "register_version": register_version,
        "eligible_for_assessment": False,
    }


def _tested(values: Iterable[Any]) -> list[str]:
    """Explanations the check reports it tested, as short neutral sentences."""
    out = []
    for value in values:
        code = value.get("code") if isinstance(value, Mapping) else value
        if not code:
            continue
        text = f"Tested alternative explanation: {str(code).replace('_', ' ')}."
        if not finding_rules.labelling_terms(text) and text not in out:
            out.append(text)
    return out[: finding_rules.MAX_ALTERNATIVES]


def _details(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Pair each entry of ``result['findings']`` with the tool output behind it.

    ``assess_taxpayer`` appends findings in a fixed order (input VAT, then the three-way
    flags, then the related-party findings, then the work-arrangement referral), so each
    tool's entries are taken in its own order.
    """
    flags = iter(result.get("three_way", {}).get("flags", []))
    rp = iter(result.get("related_party", {}).get("findings", []))
    pc = iter(result.get("persistent_credit", {}).get("findings", []))
    out = []
    for finding in result.get("findings", []):
        issue = finding.get("issue")
        if issue == "untaxed_imported_services":
            out.append({"finding": finding, "source": next(flags, {})})
        elif issue in ("related_party_loss", "policy_gap"):
            out.append({"finding": finding, "source": next(rp, {})})
        elif issue == "persistent_credit":
            out.append({"finding": finding, "source": next(pc, {})})
        else:
            out.append({"finding": finding, "source": {}})
    return out


def _input_vat(result: Mapping[str, Any]) -> tuple[str, dict[str, str] | None, list[Any]]:
    claims = [c for c in result.get("claims", []) if c.get("authenticity_indicator")]
    excess = [
        f
        for f in result.get("recompute", {}).get("findings", [])
        if f.get("code") == "declared_input_vat_exceeds_recomputed"
    ]
    disputed = sum(
        (Decimal(c["lane"]["disputed_amount"]["amount"]) for c in claims), Decimal("0.00")
    )
    over = sum((Decimal(f["amount"]["amount"]) for f in excess), Decimal("0.00"))
    parts = []
    cited: list[Any] = []
    if claims:
        parts.append(
            f"On {len(claims)} refund claim(s) M{disputed:,} of claimed input VAT is not "
            "supported by an independent record: the supplier is not registered, the invoice "
            "is absent from IDMS for an integrated supplier, or the invoice is absent from the "
            "taxpayer's own books."
        )
        for claim in claims:
            for line in claim["match"]["lines"]:
                if line.get("questions") or line.get("notes"):
                    cited.append(line["line_id"])
            cited.extend(claim["lane"]["inputs"].get("recompute", {}).get("lines_not_in_books", []))
    if excess:
        parts.append(
            f"In {len(excess)} period(s) the declared input VAT exceeds the VAT recomputed "
            f"from the taxpayer's books by M{over:,} in total."
        )
        for f in excess:
            cited.extend(f.get("evidence_ids", []))
    amount = disputed if claims else over
    text = " ".join(parts) + " This is a request for evidence, not a refund decision."
    return text, _money(amount), cited


def finding_bodies(
    result: Mapping[str, Any],
    *,
    sources: Mapping[str, Mapping[str, Any]],
    bundle_evidence_id: str,
    register_version: str,
    input_digest: str,
) -> list[tuple[dict[str, Any], str]]:
    """Every finding of one ``assess_taxpayer`` result as a finding body and its key."""
    if result.get("decision") is not None:
        raise _fail("A check result cannot carry a decision.", "decision", "decision_not_allowed")
    version = str(result.get("pipeline_version") or "")
    out: list[tuple[dict[str, Any], str]] = []
    subjects: set[str] = set()
    for item in _details(result):
        finding, source = item["finding"], item["source"]
        issue = str(finding.get("issue"))
        if issue not in ISSUES:
            raise _fail("Unknown issue.", "issue", "invalid_choice")
        category, kind, action, confidence = ISSUES[issue]
        code = str(finding["code"])
        route = finding["route"]
        if route.get("eligible_for_assessment") is not False:
            raise _fail("A route cannot be eligible.", "route", "route_not_verifiable_here")
        alternatives: list[str] = []
        if issue == "fake_invoices":
            explanation, amount, cited = _input_vat(result)
            rule_version = str(result.get("recompute", {}).get("recompute_version", version))
            subject = code
        elif issue == "untaxed_imported_services":
            explanation = _neutral(
                source.get("reason"),
                "Foreign payments, the books and the declared withholding or imported-services "
                "VAT do not agree beyond the tolerance.",
            )
            amount = _money(source.get("amount"))
            cited = list(source.get("evidence_ids", []))
            rows = result["three_way"].get("rows", [])
            counterparty = source.get("counterparty")
            alternatives = _tested(
                e
                for row in rows
                if counterparty is None or row.get("counterparty") == counterparty
                for e in row.get("explanations_tested", [])
            )
            rule_version = str(result["three_way"].get("match_version", version))
            subject = f"{code}:{counterparty or 'all'}:{source.get('year', '-')}"
        elif issue in ("related_party_loss", "policy_gap"):
            explanation = _neutral(
                source.get("reason"),
                "Related-party records meet a review trigger; the records do not establish "
                "pricing, benefit or intent.",
            )
            if issue == "policy_gap":
                explanation += (
                    " No verified legal route exists for this exposure; it is recorded for "
                    "policy review, not as a finding against the taxpayer."
                )
            amount = None
            cited = list(source.get("evidence_ids", []))
            alternatives = _tested(
                result["related_party"].get("alternative_explanations_tested", [])
            )
            rule_version = str(result["related_party"].get("signals_version", version))
            subject = f"{code}:{source.get('counterparty') or 'all'}"
        elif issue == "persistent_credit":
            review = result.get("persistent_credit", {})
            explanation = _neutral(
                source.get("reason"),
                "VAT credits persist across periods and the structural explanations tested "
                "do not cover them.",
            ) + (
                " The records do not establish why; this is a request for evidence, not a "
                "refund decision."
            )
            amount = _money(source.get("unexplained_total"))
            periods = set(source.get("periods", []))
            cited = [
                row["return_id"]
                for row in review.get("periods", [])
                if row.get("period") in periods and row.get("return_id")
            ]
            alternatives = _tested(review.get("explanation_order", []))
            rule_version = str(review.get("policy_version", version))
            subject = code
        else:  # disguised_employment
            referrals = result.get("work_arrangement", {}).get("referrals", [])
            explanation = (
                f"The recorded facts for {len(referrals)} consultancy agreement(s) conflict "
                "(stated contract terms against permit presence), so the working arrangement is "
                "referred to a specialist. The facts do not establish employment."
            )
            amount = None
            cited = [e for r in referrals for e in [r.get("contract_id"), *r.get("evidence_ids", [])]
                     if e]  # fmt: skip
            rule_version = str(result.get("work_arrangement", {}).get("review_version", version))
            subject = code
        base, n = subject, 2
        while subject in subjects:
            subject, n = f"{base}#{n}", n + 1
        subjects.add(subject)
        source_refs, evidence_refs = cite(
            cited, sources=sources, bundle_evidence_id=bundle_evidence_id
        )
        policy_gap = route["route_status"] == "policy_gap"
        body = {
            "kind": kind,
            "category": category,
            "code": code,
            "explanation": explanation,
            "evidence_refs": evidence_refs,
            "source_refs": source_refs,
            "unresolved_amount": amount,
            "confidence": confidence,
            "alternative_explanations": alternatives,
            "legal_route": {
                "status": "no_verified_route" if policy_gap else "unverified",
                "citation": None,
                "verified_by": None,
            },
            "candidate_route": _route(route, register_version),
            "proposed_action": {"type": action, "requires_human_approval": True},
            "producer": PRODUCER,
            "producer_version": version,
            "subject": subject,
            "rule": {
                "rule_id": code,
                "rule_version": rule_version,
                "ruleset_id": RULESET_ID,
                "ruleset_version": version,
                "input_digest": input_digest,
            },
        }
        key = "check:" + sha256_text(version, code, subject, input_digest)
        out.append((body, key))
    return out


# --- lane proposals -----------------------------------------------------------------

LANE_KEYS: Final = (
    "claim_ref", "period", "lane", "policy_version", "reasons", "service_target",
    "claimed_amount", "undisputed_amount", "undisputed_status", "disputed_amount",
    "disputed_items", "questions", "inputs", "line_match", "authenticity_indicator",
    "requires_officer_review", "source_refs",
)  # fmt: skip


def lane_proposal(
    claim: Mapping[str, Any], *, sources: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """The TAX-114 lane of one claim as a validated lane-proposal body."""
    lane = claim["lane"]
    claim_ref = str(claim["claim_id"])
    ref = sources.get(claim_ref)
    body = {
        "claim_ref": claim_ref,
        "period": str(claim["period"]),
        "lane": lane["lane"],
        "policy_version": str(lane.get("policy_version")),
        "reasons": [str(r) for r in lane.get("reasons", [])],
        "service_target": dict(lane.get("service_target") or {}),
        "claimed_amount": lane["claimed_amount"],
        "undisputed_amount": lane["undisputed_amount"],
        "undisputed_status": lane.get("undisputed_status"),
        "disputed_amount": lane["disputed_amount"],
        "disputed_items": list(lane.get("disputed_items", [])),
        "questions": list(lane.get("questions", [])),
        "inputs": dict(lane.get("inputs", {})),
        "line_match": [
            {
                "line_id": line["line_id"],
                "status": line["status"],
                "treatment": line.get("treatment"),
            }
            for line in claim["match"]["lines"]
        ],  # fmt: skip
        "authenticity_indicator": bool(claim.get("authenticity_indicator")),
        "requires_officer_review": True,
        "source_refs": [{**ref, "locator": None}] if ref else [],
    }
    if lane.get("decision") is not None:
        raise _fail("A lane is a proposal, never a decision.", "decision", "decision_not_allowed")
    return validate_lane(body)


def validate_lane(body: Mapping[str, Any]) -> dict[str, Any]:
    """Check a lane-proposal body; amounts are decimal strings, nothing is decided."""
    extra = sorted(set(body) - set(LANE_KEYS))
    if extra:
        raise _fail(f"{extra[0]} is not accepted.", extra[0], "unexpected_field")
    missing = sorted(set(LANE_KEYS) - set(body))
    if missing:
        raise _fail(f"{missing[0]} is required.", missing[0], "required")
    if body["lane"] not in LANES:
        raise _fail("lane must be fast, standard or investigation.", "lane", "invalid_choice")
    if body["requires_officer_review"] is not True:
        raise _fail("Every lane is reviewed by an officer.", "requires_officer_review", "invalid")
    amounts = {}
    for field in ("claimed_amount", "undisputed_amount", "disputed_amount"):
        value = body[field]
        if not isinstance(value, Mapping) or not isinstance(value.get("amount"), str):
            raise _fail(f"{field} must be Money.", field, "invalid_amount")
        if not _AMOUNT.fullmatch(value["amount"]) or value.get("currency") != CURRENCY:
            raise _fail(f"{field} must be a decimal string in LSL.", field, "invalid_amount")
        amounts[field] = Decimal(value["amount"])
    if amounts["undisputed_amount"] + amounts["disputed_amount"] > amounts["claimed_amount"]:
        raise _fail("Undisputed and disputed exceed the claim.", "undisputed_amount", "invalid")
    target = body["service_target"]
    if not isinstance(target, Mapping) or "decision_due" not in target:
        raise _fail("service_target needs a due date.", "service_target", "required")
    for text in body["reasons"]:
        if not isinstance(text, str) or len(text) > 1000:
            raise _fail("reasons are short texts.", "reasons", "invalid")
    result: dict[str, Any] = json.loads(json.dumps(dict(body)))
    return result


def run_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Counts and totals the checks computed (what an operator sees per run)."""
    rec = result.get("recompute", {})
    three = result.get("three_way", {})
    return {
        "pipeline_version": result.get("pipeline_version"),
        "claims_assessed": len(result.get("claims", [])),
        "findings": len(result.get("findings", [])),
        "issues": sorted(result.get("flags", [])),
        "recompute_comparison_rows": len(rec.get("comparison", [])),
        "recompute_findings": len(rec.get("findings", [])),
        "three_way_rows": len(three.get("rows", [])),
        "three_way_total_gap": _money(three.get("total_gap")) if three.get("total_gap") else None,
        "work_referrals": len(result.get("work_arrangement", {}).get("referrals", [])),
        "flagged_amounts": {
            k: _money(v) for k, v in sorted((result.get("flagged_amounts") or {}).items())
        },
        "inputs_status": dict(result.get("inputs_status") or {}),
        "check_seconds": result.get("seconds"),
    }


def input_digest(
    *, bundle_sha256: str, sources_digest: str, claim_ref: str, pipeline_version: str
) -> str:
    """What the checks read for this case: replaying the same inputs records nothing new."""
    return "sha256:" + sha256_text(bundle_sha256, sources_digest, claim_ref, pipeline_version)


def references_digest(envelopes: Sequence[Mapping[str, Any]]) -> str:
    refs = sorted(
        (str(r["source_system"]), str(r["record_id"]), str(r["record_sha256"]))
        for env in envelopes
        for r in env.get("references") or []
    )
    statuses = sorted((str(e.get("source_system")), str(e.get("kind")), str(e.get("status")))
                      for e in envelopes)  # fmt: skip
    return digest({"references": refs, "statuses": statuses})


def assess_case(
    sources: Any,
    params: Mapping[str, Any],
    records: Mapping[str, Any],
    *,
    tin: str,
    period: str,
    claimed_amount: str,
    bundle_evidence_id: str,
    bundle_sha256: str,
    assess: Any,
    register_version: str,
    claim_ref: str | None = None,
) -> dict[str, Any]:
    """Run the checks for one sent claim and return what to record (no I/O but the reads).

    ``sources`` is the TAX-111 ``FixtureRslSources`` of the site, ``params`` its stated
    assumptions (RSL tenant, synthetic rates, materiality), ``records`` the taxpayer's own
    records read from Frappe and ``assess`` the TAX-118 ``assess_taxpayer``. The claim is
    the taxpayer's Online Refunds Application record for ``period``; its amount must equal
    the amount sent in Frappe. A claim read from RSL (TAX-122) passes its RSL ``claim_ref``
    and is matched by that ID, not only by period. Returns ``status`` (``completed``, ``degraded`` or
    ``not_run``), ``reason``, per-check status, the RSL systems' answers and, when it ran,
    the finding bodies, the claim's lane proposal and the run summary.
    """
    tenant = str(params["tenant_id"])
    envelopes = [sources.read(system, kind, tenant_id=tenant, taxpayer=tin)
                 for system, kind in SOURCE_KINDS]  # fmt: skip
    statuses = source_status(envelopes)
    statuses["registry"] = sources.lookup(
        "registry", "entity", tenant_id=tenant, field="tin", values=[]
    )["status"]
    statuses = dict(sorted(statuses.items()))
    status, reason, checks = run_status(statuses)
    plan: dict[str, Any] = {"status": status, "reason": reason, "checks": checks,
                            "systems": statuses, "claim_ref": None}  # fmt: skip
    if status == "not_run":
        return plan
    claims = next(e for e in envelopes if e["source_system"] == "online_refunds")["records"]
    found = next(
        (c for c in claims
         if c["period"] == period and (claim_ref is None or str(c["claim_id"]) == claim_ref)),
        None,
    )  # fmt: skip
    if found is None:
        return {**plan, "status": "not_run", "reason": "rsl_claim_not_found"}
    plan["claim_ref"] = str(found["claim_id"])
    cent = Decimal("0.01")
    if Decimal(str(found["claimed_amount"])).quantize(cent) != Decimal(
        str(claimed_amount)
    ).quantize(cent):
        return {**plan, "status": "not_run", "reason": "claim_differs_from_rsl_record"}
    world = {
        "tenant_id": tenant,
        "rates_assumed": params["rates_assumed"],
        "materiality": params["materiality"],
        "submissions": {tin: records},
    }
    result = assess(world, sources, tin)
    version = str(result["pipeline_version"])
    digest_value = input_digest(
        bundle_sha256=bundle_sha256,
        sources_digest=references_digest(envelopes),
        claim_ref=plan["claim_ref"],
        pipeline_version=version,
    )
    index = source_index(envelopes)
    claim_result = next(c for c in result["claims"] if c["claim_id"] == plan["claim_ref"])
    return {
        **plan,
        "version": version,
        "input_digest": digest_value,
        "bodies": finding_bodies(result, sources=index, bundle_evidence_id=bundle_evidence_id,
                                 register_version=register_version, input_digest=digest_value),
        "lane": lane_proposal(claim_result, sources=index),
        "summary": run_summary(result),
    }  # fmt: skip
