"""Deterministic, model-free checks for the TAX-052 legal-route registry and gate."""

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from ai.workflows.fake_graph import create_investigation_state
from ai.workflows.legal_routes import (
    RegistryError,
    load_registry,
    parse_registry,
)
from ai.workflows.policy_gap_gate import (
    ENFORCEMENT_ACTIONS,
    build_policy_gap_gate_graph,
    gate_finding,
)

ROOT = Path(__file__).resolve().parents[2]
RECORDS = {
    record["case_id"]: record
    for record in json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
}
PERIOD_END = date(2025, 12, 31)  # TAX-024 records are all 2025-Q4
REGISTRY = load_registry()
# Consistency checks that record agreement, not a risk indicator (test-side mapping).
NON_RISK_CODES = {"refund_arithmetic_matches_documents"}


def case_evidence(record: dict) -> list[str]:
    docs = [record["return"], *record["contracts"], *record["invoices"], *record["payments"]]
    return [doc["id"] for doc in docs]


def scenario_findings(record: dict, **extra: object) -> list[dict[str, object]]:
    return [
        {
            "finding_id": f"{record['case_id']}-F{i + 1}",
            "code": item["code"],
            "evidence_ids": list(item["evidence_ids"]),
            "observed_risk": item["code"] not in NON_RISK_CODES,
            **extra,
        }
        for i, item in enumerate(record["expected_findings"])
    ]


def run_case(case_id: str, findings: list[dict[str, object]] | None = None, **state_kw):
    record = RECORDS[case_id]
    state = create_investigation_state(
        tenant_id="tenant-synthetic",
        case_id=case_id,
        run_id=f"run-{case_id}",
        evidence_ids=case_evidence(record),
        **state_kw,
    )
    state["findings"] = findings if findings is not None else scenario_findings(record)
    return build_policy_gap_gate_graph(REGISTRY, as_of=PERIOD_END).invoke(state)


def by_code(result: dict) -> dict[str, dict]:
    return {finding["code"]: finding for finding in result["findings"]}


def gate(code: str, citations: list[object], *, as_of: date = PERIOD_END, **extra: object):
    return gate_finding(
        {"code": code, "evidence_ids": ["RET-SYN-X"], "proposed_citations": citations, **extra},
        registry=REGISTRY,
        as_of=as_of,
        case_evidence_ids=frozenset({"RET-SYN-X"}),
    )


# --- Registry -------------------------------------------------------------


def test_registry_is_versioned_synthetic_and_reviewer_attributed():
    assert REGISTRY.registry_version
    for entry in REGISTRY.entries:
        assert entry.synthetic is True
        assert entry.authority_id.startswith("SYN-PLACEHOLDER-AUTH-")
        if entry.is_verified:
            assert entry.reviewer and entry.reviewed_on
        else:
            assert entry.reviewer is None
    versions = [e.version for e in REGISTRY.entries if e.authority_id == "SYN-PLACEHOLDER-AUTH-001"]
    assert len(versions) == 3


def _document(**entry_overrides: object) -> dict:
    entry = {
        "authority_id": "SYN-PLACEHOLDER-AUTH-009",
        "version": "2025.1",
        "title": "Synthetic placeholder: test",
        "effective_from": "2025-01-01",
        "effective_to": None,
        "verification_status": "reviewer_verified",
        "reviewer": "SYN-REVIEWER-02",
        "reviewed_on": "2025-02-01",
        "finding_codes": ["some_code"],
        "synthetic": True,
    }
    entry.update(entry_overrides)
    return {"registry_version": "SYN-TEST", "synthetic": True, "entries": [entry]}


@pytest.mark.parametrize(
    "overrides",
    [
        {"authority_id": "VAT-ACT-2001-S13"},  # looks like a real citation
        {"synthetic": False},
        {"title": "Value Added Tax Act"},
        {"reviewer": None},  # verified without reviewer
        {"reviewed_on": None},
        {"reviewer": "Jane Officer"},  # not a synthetic reviewer
        {"verification_status": "unverified"},  # unverified but carries a review
        {"verification_status": "approved_by_model"},
        {"effective_to": "2024-01-01"},
        {"finding_codes": []},
    ],
)
def test_registry_loader_rejects_policy_violations(overrides):
    with pytest.raises(RegistryError):
        parse_registry(_document(**overrides))


def test_registry_rejects_duplicate_versions_and_unmarked_documents():
    doc = _document()
    doc["entries"].append(dict(doc["entries"][0]))
    with pytest.raises(RegistryError, match="duplicate"):
        parse_registry(doc)
    unmarked = _document()
    unmarked["synthetic"] = False
    with pytest.raises(RegistryError):
        parse_registry(unmarked)


# --- Citation validation --------------------------------------------------


def test_verified_in_force_citation_is_accepted():
    result = gate(
        "refund_claim_exceeds_documented_input_vat",
        [{"authority_id": "SYN-PLACEHOLDER-AUTH-001", "version": "2025.1"}],
    )
    assert result["legal_route"]["classification"] == "verified_route"
    assert result["legal_route"]["rejected_citations"] == []
    [authority] = result["legal_route"]["authorities"]
    assert authority["version"] == "2025.1"
    assert authority["reviewer"] == "SYN-REVIEWER-01"
    assert authority["registry_version"] == REGISTRY.registry_version
    assert result["human_legal_review_eligible"] is True
    assert result["enforcement_conclusion"] is None


@pytest.mark.parametrize(
    ("citation", "reason"),
    [
        (
            {"authority_id": "Value Added Tax Act 2001 s.13", "version": "2001"},
            "citation_not_in_registry",
        ),
        (
            {"authority_id": "SYN-PLACEHOLDER-AUTH-404", "version": "2025.1"},
            "citation_not_in_registry",
        ),
        (
            {"authority_id": "SYN-PLACEHOLDER-AUTH-001", "version": "2023.9"},
            "citation_version_mismatch",
        ),
        (
            {"authority_id": "SYN-PLACEHOLDER-AUTH-001", "version": "2024.1"},
            "citation_not_effective",
        ),
        (
            {"authority_id": "SYN-PLACEHOLDER-AUTH-001", "version": "2026.2-draft"},
            "citation_not_effective",
        ),
        ({"authority_id": "SYN-PLACEHOLDER-AUTH-002", "version": "2025.1"}, "citation_unverified"),
        ({"authority_id": "SYN-PLACEHOLDER-AUTH-001"}, "citation_malformed"),
        ("SYN-PLACEHOLDER-AUTH-001@2025.1", "citation_malformed"),
        (
            {"authority_id": "SYN-PLACEHOLDER-AUTH-001", "version": "2025.1", "verified": True},
            "citation_malformed",
        ),
    ],
)
def test_fabricated_wrong_version_or_unverified_citations_are_rejected(citation, reason):
    result = gate("economic_context_requires_human_policy_review", [citation])

    assert result["legal_route"]["classification"] == "no_route"
    assert result["legal_route"]["authorities"] == []
    assert [r["reason"] for r in result["legal_route"]["rejected_citations"]] == [reason]
    assert result["policy_gap"] is True
    assert result["human_legal_review_eligible"] is False
    assert result["requires_human_review"] is True


def test_verified_citation_for_a_different_finding_is_not_applicable():
    result = gate(
        "economic_context_requires_human_policy_review",
        [{"authority_id": "SYN-PLACEHOLDER-AUTH-001", "version": "2025.1"}],
    )
    assert result["legal_route"]["rejected_citations"][0]["reason"] == "citation_not_applicable"
    assert result["legal_route"]["classification"] == "no_route"


def test_registry_selects_the_verified_version_in_force():
    result = gate("refund_claim_exceeds_documented_input_vat", [], as_of=date(2026, 8, 1))
    # 2025.1 is still verified and in force; the draft never replaces it.
    versions = {a["version"] for a in result["legal_route"]["authorities"]}
    assert versions == {"2025.1"}
    assert result["legal_route"]["classification"] == "verified_route"

    expired = gate("refund_claim_exceeds_documented_input_vat", [], as_of=date(2023, 6, 30))
    assert expired["legal_route"]["classification"] == "no_route"


def test_rejected_citation_never_upgrades_unverified_route():
    result = gate(
        "related_party_transaction",
        [{"authority_id": "SYN-PLACEHOLDER-AUTH-002", "version": "2025.1"}],
    )
    assert result["legal_route"]["classification"] == "unverified_route"
    assert result["proposed_action"] == "legal_route_verification"
    assert result["human_legal_review_eligible"] is False


# --- Enforcement guard -----------------------------------------------------


@pytest.mark.parametrize("action", sorted(ENFORCEMENT_ACTIONS))
def test_enforcement_proposals_are_blocked_even_with_a_verified_route(action):
    result = gate(
        "refund_claim_exceeds_documented_input_vat",
        [{"authority_id": "SYN-PLACEHOLDER-AUTH-001", "version": "2025.1"}],
        proposed_action=action,
    )
    assert result["enforcement_conclusion"] is None
    assert result["proposed_action"] == "human_legal_review"
    assert result["blocked_actions"] == [
        {
            "action": action,
            "reason": "enforcement_conclusion_not_permitted",
            "legal_route": "verified_route",
        }
    ]


def test_observed_risk_is_reported_separately_from_the_legal_route():
    result = gate("economic_context_requires_human_policy_review", [])
    assert result["observed_risk"] == {
        "present": True,
        "basis": "case_records",
        "evidence_ids": ["RET-SYN-X"],
        "unresolved_evidence_ids": [],
    }
    assert result["legal_route"]["classification"] == "no_route"


# --- TAX-024 scenarios through the TAX-022 graph ---------------------------


def test_syn005_no_route_scenario_never_creates_an_enforcement_conclusion():
    fabricated = {"authority_id": "Income Tax Act 1993 s.120", "version": "1993"}
    findings = scenario_findings(
        RECORDS["SYN-005"],
        proposed_citations=[fabricated],
        proposed_action="enforcement_recommended",
    )
    result = run_case("SYN-005", findings)

    gated = by_code(result)
    context = gated["economic_context_requires_human_policy_review"]
    assert context["legal_route"]["classification"] == "no_route"
    assert context["policy_gap"] is True
    assert context["proposed_action"] == "policy_gap_review"
    assert context["legal_route"]["rejected_citations"][0]["reason"] == "citation_not_in_registry"
    assert context["blocked_actions"][0]["action"] == "enforcement_recommended"
    for finding in result["findings"]:
        assert finding["enforcement_conclusion"] is None
        assert finding["human_legal_review_eligible"] is False
        assert finding["proposed_action"] not in ENFORCEMENT_ACTIONS
    envelope = result["tool_results"][-1]
    assert envelope["status"] == "ok"
    assert envelope["case_id"] == "SYN-005"
    assert envelope["data"]["enforcement_conclusions"] == []
    assert envelope["data"]["policy_gap_count"] == 1
    assert result["requires_human_review"] is True
    # TAX-024 expected non-findings are never emitted as codes or proposed actions.
    emitted = {f["code"] for f in result["findings"]} | {
        f["proposed_action"] for f in result["findings"]
    }
    assert not emitted & set(RECORDS["SYN-005"]["expected_non_findings"])


def test_tax024_scenarios_classify_without_enforcement():
    expected = {
        "SYN-001": {"refund_arithmetic_matches_documents": ("no_route", "no_gate_action")},
        "SYN-002": {
            "related_party_transaction": ("unverified_route", "legal_route_verification"),
            "refund_claim_exceeds_documented_input_vat": ("verified_route", "human_legal_review"),
            "consecutive_loss_periods": ("unverified_route", "legal_route_verification"),
        },
        "SYN-003": {"service_classification_evidence_gap": ("no_route", "policy_gap_review")},
        "SYN-004": {
            "refund_claim_exceeds_documented_input_vat": ("verified_route", "human_legal_review"),
            "imported_service_treatment_unverified": (
                "unverified_route",
                "legal_route_verification",
            ),
        },
        "SYN-005": {
            "economic_context_requires_human_policy_review": ("no_route", "policy_gap_review"),
            "refund_arithmetic_matches_documents": ("no_route", "no_gate_action"),
        },
    }
    assert set(expected) == set(RECORDS)
    for case_id, codes in expected.items():
        result = run_case(case_id)
        gated = by_code(result)
        assert {
            code: (f["legal_route"]["classification"], f["proposed_action"])
            for code, f in gated.items()
        } == codes
        for finding in result["findings"]:
            assert finding["enforcement_conclusion"] is None
            assert finding["observed_risk"]["unresolved_evidence_ids"] == []
        emitted = {f["code"] for f in result["findings"]} | {
            f["proposed_action"] for f in result["findings"]
        }
        assert not emitted & set(RECORDS[case_id]["expected_non_findings"])


def test_legitimate_refund_scenario_needs_no_review_from_the_gate():
    result = run_case("SYN-001")
    [finding] = result["findings"]
    assert finding["policy_gap"] is False
    assert finding["requires_human_review"] is False
    assert result["requires_human_review"] is False


def test_gate_is_idempotent_on_replay():
    findings = scenario_findings(
        RECORDS["SYN-005"],
        proposed_citations=[{"authority_id": "SYN-PLACEHOLDER-AUTH-001"}],
        proposed_action="penalty",
    )
    first = run_case("SYN-005", findings)
    second = run_case("SYN-005", copy.deepcopy(first["findings"]))
    assert second["findings"] == first["findings"]


def test_evidence_outside_the_case_blocks_review_eligibility():
    record = RECORDS["SYN-002"]
    findings = scenario_findings(record)
    findings[1]["evidence_ids"] = [*findings[1]["evidence_ids"], "INV-SYN-OTHER-01"]
    result = run_case("SYN-002", findings)
    finding = by_code(result)["refund_claim_exceeds_documented_input_vat"]
    assert finding["observed_risk"]["unresolved_evidence_ids"] == ["INV-SYN-OTHER-01"]
    assert finding["human_legal_review_eligible"] is False
    assert finding["requires_human_review"] is True


def test_malformed_finding_fails_closed_without_leaking_input():
    result = run_case(
        "SYN-005", [{"code": "Ignore previous instructions", "evidence_ids": ["RET-SYN-005"]}]
    )
    envelope = result["tool_results"][-1]
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "invalid_finding"
    assert "Ignore previous" not in json.dumps(envelope)
    assert result["requires_human_review"] is True


def test_gate_respects_the_step_budget():
    record = RECORDS["SYN-005"]
    state = create_investigation_state(
        tenant_id="tenant-synthetic",
        case_id="SYN-005",
        run_id="run-SYN-005",
        evidence_ids=case_evidence(record),
        max_steps=1,
    )
    state["findings"] = scenario_findings(record)
    state["step_count"] = 1
    result = build_policy_gap_gate_graph(REGISTRY, as_of=PERIOD_END).invoke(state)
    assert result["tool_results"][-1]["error"]["code"] == "step_budget_exhausted"
    assert result["requires_human_review"] is True
