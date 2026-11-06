"""Frappe-free tests for case findings and officer finding feedback (TAX-086).

``MemoryRepository`` has the same unique keys as the DocTypes. The rule rows and
playbook result below are the SYN-002 outputs of integration/jube/tax_risk.py
(TAX-035) and ai/tools/related_party.py (TAX-049), copied because the Frappe app
cannot read those trees; tests/integration/test_case_findings.py runs the same
recording against the live producers.
"""

from __future__ import annotations

import copy
import datetime
import unittest
import uuid
from typing import Any

from vati_tax.findings import engine, rules
from vati_tax.findings.engine import CaseRef, MemoryRepository, RevisionConflict
from vati_tax.findings.rules import FindingNotFound, InvalidFinding
from vati_tax.identity import policy
from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict, InvalidMapping

T1 = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"
TAXPAYER = "80015243-dd34-4e97-b085-7356ffdd042f"
CASE = "40000000-0000-4000-8000-000000000002"
CASE_T2 = "40000000-0000-4000-8000-000000000099"
NOW = datetime.datetime(2026, 11, 6, 10, 0, tzinfo=datetime.UTC)
SHA = {name: f"{i:x}" * 64 for i, name in enumerate(["a", "b", "c", "d", "e", "f"], start=10)}

# SYN-002 source records -> Vati Evidence IDs in tenant T1 (synthetic).
SOURCE = {
    "RET-SYN-002": "50000000-0000-4000-8000-000000000020",
    "INV-SYN-002-01": "50000000-0000-4000-8000-000000000021",
    "INV-SYN-002-02": "50000000-0000-4000-8000-000000000022",
    "CTR-SYN-002-01": "50000000-0000-4000-8000-000000000023",
    "CTR-SYN-002-02": "50000000-0000-4000-8000-000000000024",
    "PAY-SYN-002-01": "50000000-0000-4000-8000-000000000025",
}
EVIDENCE_SHA = {eid: list(SHA.values())[i] for i, eid in enumerate(SOURCE.values())}
OTHER_TENANT_EVIDENCE = "50000000-0000-4000-8000-000000000099"

DIGEST = "sha256:05312b06fe3550251c989cb484794907226b3e604705fc96955a2c7990b82766"


def _signal(rule_id: str, family: str, code: str | None, outcome: str, **extra: Any) -> dict:
    return {
        "case_id": "SYN-002",
        "taxpayer_id": "SYN-TAXPAYER-002",
        "period": "2025-Q4",
        "ruleset_id": "vati-synthetic-tax-risk",
        "ruleset_version": "1.0.0",
        "rule_id": rule_id,
        "rule_version": "1.0.0",
        "family": family,
        "outcome": outcome,
        "code": code,
        "input_digest": DIGEST,
        "synthetic": True,
        **extra,
    }


SIGNALS = [
    _signal(
        "TR-VAT-REFUND-MISMATCH",
        "vat_refund_mismatch",
        "refund_claim_exceeds_documented_input_vat",
        "signal",
        inputs={"unsupported_input_vat": "15000.00", "claimed_input_vat": "25000.00"},
        evidence_ids=["RET-SYN-002", "INV-SYN-002-01", "INV-SYN-002-02"],
        explanation="Claimed input VAT LSL 25000.00 exceeds VAT shown on the supplied purchase "
        "invoices LSL 10000.00 by LSL 15000.00. Request the missing supporting invoices; "
        "this is not a refund decision.",
    ),
    _signal(
        "TR-RELATED-PARTY-FEES",
        "related_party_fees",
        "related_party_transaction",
        "signal",
        inputs={"related_party_purchase_net": "80000.00"},
        evidence_ids=["CTR-SYN-002-01", "INV-SYN-002-01"],
        explanation="Purchases of LSL 80000.00 (net) were made under a contract marked "
        "related-party. Pricing, benefit and intent are not established; ask for the "
        "service agreement and pricing basis.",
    ),
    _signal(
        "TR-PERSISTENT-LOSSES",
        "persistent_losses",
        "consecutive_loss_periods",
        "signal",
        inputs={"consecutive_loss_periods": "3"},
        evidence_ids=["RET-SYN-002"],
        explanation="The synthetic return history shows 3 consecutive loss periods ending in "
        "the return period. Losses can be legitimate; ask for the business explanation.",
    ),
    _signal(
        "TR-IMPORTED-SERVICE-GAP",
        "imported_service_gap",
        None,
        "no_signal",
        inputs={"imported_service_unverified_count": "0"},
        evidence_ids=[],
        explanation=None,
    ),
]

PLAYBOOK_RESULT = {
    "kind": "related_party_review_proposal",
    "code": "related_party_transaction",
    "observed_risk": True,
    "proposed_next_step": "targeted_evidence_request",
    "observations": [
        {"code": "related_party_transaction", "evidence_ids": ["CTR-SYN-002-01", "INV-SYN-002-01"]},
        {"code": "consecutive_loss_periods", "evidence_ids": ["RET-SYN-002"], "periods": 3},
    ],
    "counter_evidence": [
        {"code": "no_duplication_observed", "evidence_ids": ["CTR-SYN-002-01", "CTR-SYN-002-02"]},
        {"code": "payment_trail_consistent", "evidence_ids": ["INV-SYN-002-01", "PAY-SYN-002-01"]},
    ],
    "evidence_gaps": [
        {"code": "service_delivery_evidence_missing",
         "evidence_ids": ["CTR-SYN-002-01", "INV-SYN-002-01"]},
        {"code": "service_benefit_evidence_missing",
         "evidence_ids": ["CTR-SYN-002-01", "INV-SYN-002-01"]},
        {"code": "loss_explanation_not_documented", "evidence_ids": ["RET-SYN-002"]},
    ],
    "discrepancies": [],
    "evidence_questions": [],
    "legal_conclusion": None,
    "enforcement_conclusion": None,
    "decision": None,
    "playbook_version": "tax049-related-party-playbook-v1",
}  # fmt: skip
STAMP = {
    "kind": "playbook",
    "name": "related_party",
    "version": "1.0",
    "content_digest": "b37f05c28d58a324783c18920367478bb28563c4da5f0b924a67a0ba862c004f",
    "approved_by": "SYN-SUPERVISOR-01",
    "registry_version": "SYN-PBR-2026.11.3",
}
# integration/api/examples/http/05_list_findings_imported_services_gap.json
PROVENANCE: dict[str, Any] = {
    "provider": "template",
    "model_id": "deterministic-template-v1",
    "configured_model_id": "deterministic-template-v1",
    "role": "reasoning",
    "task": "related_party_review",
    "prompt_version": "related-party-instructions-v1",
    "settings": {"kind": "template", "temperature": None, "sampling": None},
    "fallback_level": 2,
    "fallback_level_name": "template",
    "fell_back": True,
    "attempts": [
        {"level": 0, "name": "hosted", "provider": "groq", "model_id": "openai/gpt-oss-120b",
         "outcome": "rate_limited", "http_status": 429, "latency_ms": 180},
        {"level": 1, "name": "local", "provider": None, "model_id": None,
         "outcome": "unconfigured", "http_status": None, "latency_ms": None},
        {"level": 2, "name": "template", "provider": "template",
         "model_id": "deterministic-template-v1", "outcome": "ok", "http_status": None,
         "latency_ms": 2},
    ],
    "latency_ms": 190,
    "token_usage": {"input": None, "output": None, "total": None},
    "validation": {"status": "passed", "reason": None},
    "router_version": "tax089-router-v1",
}  # fmt: skip


def scope(user: str, role: policy.Role, tenant: str = T1) -> policy.TenantScope:
    taxpayers = frozenset({TAXPAYER}) if role == policy.TAXPAYER else frozenset()
    return policy.TenantScope(
        user=user, tenant_id=tenant, roles=frozenset({role}), taxpayer_ids=taxpayers
    )


SERVICE = scope("pipeline@example.test", policy.SERVICE)
AUDITOR = scope("auditor@example.test", policy.AUDITOR)
AUDITOR_2 = scope("auditor2@example.test", policy.AUDITOR)
SUPERVISOR = scope("supervisor@example.test", policy.SUPERVISOR)
TAXPAYER_SCOPE = scope("taxpayer@example.test", policy.TAXPAYER)
AUDITOR_T2 = scope("auditor@example.test", policy.AUDITOR, T2)


def ctx(s: policy.TenantScope, minutes: int = 0) -> engine.Context:
    return engine.Context.build(s, None, NOW + datetime.timedelta(minutes=minutes))


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


def repository() -> MemoryRepository:
    repo = MemoryRepository()
    repo.cases[CASE] = CaseRef(T1, CASE, TAXPAYER)
    repo.cases[CASE_T2] = CaseRef(T2, CASE_T2, None)
    for evidence_id, sha in EVIDENCE_SHA.items():
        repo.evidence[evidence_id] = (T1, TAXPAYER, sha)
    repo.evidence[OTHER_TENANT_EVIDENCE] = (T2, None, "9" * 64)
    return repo


def officer_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "observed_fact",
        "category": "related_party",
        "explanation": "The service agreement names the same director on both sides.",
        "evidence_refs": [
            {"evidence_id": SOURCE["CTR-SYN-002-01"], "locator": {"page": 1, "record": None}}
        ],
        "unresolved_amount": None,
        "confidence": "medium",
        "alternative_explanations": [],
        "producer": "officer",
        "producer_version": "officer-manual-v1",
    }
    body.update(overrides)
    return body


class RecordRuleSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = repository()

    def record_all(self, c: engine.Context | None = None) -> list[dict[str, Any] | None]:
        return [
            engine.record_rule_signal(
                c or ctx(SERVICE), self.repo, case_id=CASE, signal=s, evidence=SOURCE
            )
            for s in SIGNALS
        ]

    def test_syn002_rule_signals_are_recorded_and_read_back_per_case(self) -> None:
        views = self.record_all()
        self.assertIsNone(views[3])  # no_signal records nothing
        not_applicable = dict(SIGNALS[0], outcome="not_applicable", code=None, evidence_ids=[])
        self.assertIsNone(
            engine.record_rule_signal(
                ctx(SERVICE), self.repo, case_id=CASE, signal=not_applicable, evidence=SOURCE
            )
        )
        recorded = [v for v in views if v]
        self.assertEqual(len(recorded), 3)
        mismatch = recorded[0]
        self.assertEqual(mismatch["producer"], "jube_rule")
        self.assertEqual(mismatch["producer_version"], "vati-synthetic-tax-risk@1.0.0")
        self.assertEqual(
            mismatch["rule"],
            {"rule_id": "TR-VAT-REFUND-MISMATCH", "rule_version": "1.0.0",
             "ruleset_id": "vati-synthetic-tax-risk", "ruleset_version": "1.0.0",
             "input_digest": DIGEST},
        )  # fmt: skip
        self.assertEqual(mismatch["unresolved_amount"], {"amount": "15000.00", "currency": "LSL"})
        self.assertEqual(mismatch["kind"], "missing_evidence")
        self.assertEqual(mismatch["category"], "refund_reconciliation")
        self.assertIsNone(mismatch["analysis_provenance"])
        self.assertEqual(
            mismatch["evidence_refs"][0],
            {
                "evidence_id": SOURCE["RET-SYN-002"],
                "document_sha256": EVIDENCE_SHA[SOURCE["RET-SYN-002"]],
                "locator": {"page": None, "record": "RET-SYN-002"},
            },
        )
        self.assertEqual(mismatch["legal_route"]["status"], "unverified")
        self.assertIs(mismatch["proposed_action"]["requires_human_approval"], True)
        listed = engine.list_findings(ctx(AUDITOR), self.repo, case_id=CASE)
        self.assertEqual({f["finding_id"] for f in listed}, {v["finding_id"] for v in recorded})
        self.assertEqual(
            {f["rule"]["rule_id"] for f in listed},
            {"TR-VAT-REFUND-MISMATCH", "TR-RELATED-PARTY-FEES", "TR-PERSISTENT-LOSSES"},
        )
        for item in listed:
            self.assertEqual(
                item["feedback_summary"],
                {"accepted": 0, "modified": 0, "rejected": 0, "viewer_verdict": None},
            )

    def test_replaying_the_same_evaluation_is_idempotent(self) -> None:
        first = self.record_all()
        again = self.record_all(ctx(SERVICE, minutes=5))
        for a, b in zip(first, again, strict=True):
            if a is None:
                self.assertIsNone(b)
                continue
            assert b is not None
            self.assertTrue(b["replayed"])
            self.assertEqual(a["finding_id"], b["finding_id"])
        self.assertEqual(len(self.repo.findings), 3)

    def test_the_same_evaluation_on_another_case_is_its_own_finding(self) -> None:
        other = "40000000-0000-4000-8000-000000000003"
        self.repo.cases[other] = CaseRef(T1, other, TAXPAYER)
        first = engine.record_rule_signal(
            ctx(SERVICE), self.repo, case_id=CASE, signal=SIGNALS[1], evidence=SOURCE
        )
        second = engine.record_rule_signal(
            ctx(SERVICE), self.repo, case_id=other, signal=SIGNALS[1], evidence=SOURCE
        )
        assert first and second
        self.assertFalse(second["replayed"])
        self.assertNotEqual(first["finding_id"], second["finding_id"])
        self.assertEqual(second["case_id"], other)

    def test_changed_inputs_append_a_superseding_revision(self) -> None:
        first = engine.record_rule_signal(
            ctx(SERVICE), self.repo, case_id=CASE, signal=SIGNALS[0], evidence=SOURCE
        )
        changed = copy.deepcopy(SIGNALS[0])
        changed["input_digest"] = "sha256:" + "1" * 64
        changed["inputs"]["unsupported_input_vat"] = "12000.00"
        second = engine.record_rule_signal(
            ctx(SERVICE, 1), self.repo, case_id=CASE, signal=changed, evidence=SOURCE
        )
        assert first and second
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["supersedes_finding_id"], first["finding_id"])
        current = engine.list_findings(ctx(AUDITOR), self.repo, case_id=CASE)
        self.assertEqual([f["finding_id"] for f in current], [second["finding_id"]])
        history = engine.list_findings(
            ctx(AUDITOR), self.repo, case_id=CASE, include_superseded=True
        )
        self.assertEqual(
            [(f["revision"], f["superseded_by"]) for f in history],
            [(1, second["finding_id"]), (2, None)],
        )
        # The first revision is unchanged.
        self.assertEqual(history[0]["unresolved_amount"]["amount"], "15000.00")

    def test_same_key_with_a_changed_body_is_an_idempotency_conflict(self) -> None:
        body = officer_body()
        k = key()
        engine.record_finding(ctx(AUDITOR), self.repo, case_id=CASE, body=body, idempotency_key=k)
        replay = engine.record_finding(
            ctx(AUDITOR), self.repo, case_id=CASE, body=body, idempotency_key=k
        )
        self.assertTrue(replay["replayed"])
        with self.assertRaises(IdempotencyConflict):
            engine.record_finding(
                ctx(AUDITOR), self.repo, case_id=CASE,
                body=officer_body(confidence="high"), idempotency_key=k,
            )  # fmt: skip
        self.assertEqual(len(self.repo.findings), 1)

    def test_unmapped_or_foreign_evidence_is_refused_and_nothing_is_stored(self) -> None:
        partial = {k: v for k, v in SOURCE.items() if k != "INV-SYN-002-02"}
        with self.assertRaises(InvalidFinding) as caught:
            engine.record_rule_signal(
                ctx(SERVICE), self.repo, case_id=CASE, signal=SIGNALS[0], evidence=partial
            )
        self.assertEqual(caught.exception.field_errors[0]["code"], "evidence_unmapped")
        foreign = dict(SOURCE, **{"RET-SYN-002": OTHER_TENANT_EVIDENCE})
        with self.assertRaises(CaseNotFound):
            engine.record_rule_signal(
                ctx(SERVICE), self.repo, case_id=CASE, signal=SIGNALS[0], evidence=foreign
            )
        wrong_hash = officer_body(
            evidence_refs=[
                {"evidence_id": SOURCE["RET-SYN-002"], "document_sha256": "0" * 64,
                 "locator": {"page": 1, "record": None}}
            ]
        )  # fmt: skip
        with self.assertRaises(InvalidFinding) as caught:
            engine.record_finding(
                ctx(AUDITOR), self.repo, case_id=CASE, body=wrong_hash, idempotency_key=key()
            )
        self.assertEqual(caught.exception.field_errors[0]["code"], "evidence_hash_mismatch")
        self.assertEqual(self.repo.findings, {})


class RecordPlaybookResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = repository()

    def record(self, **overrides: Any) -> list[dict[str, Any]]:
        args: dict[str, Any] = {
            "case_id": CASE,
            "result": PLAYBOOK_RESULT,
            "stamp": STAMP,
            "run_id": "run-syn-002-0001",
            "evidence": SOURCE,
            "analysis_provenance": PROVENANCE,
        }
        args.update(overrides)
        return engine.record_playbook_result(ctx(SERVICE), self.repo, **args)

    def test_syn002_related_party_result_becomes_cited_findings_with_provenance(self) -> None:
        views = self.record()
        self.assertEqual(
            [(v["kind"], v["code"]) for v in views],
            [
                ("observed_fact", "related_party_transaction"),
                ("observed_fact", "consecutive_loss_periods"),
                ("missing_evidence", "service_delivery_evidence_missing"),
                ("missing_evidence", "service_benefit_evidence_missing"),
                ("missing_evidence", "loss_explanation_not_documented"),
            ],
        )
        for view in views:
            self.assertEqual(view["producer"], "langgraph_run")
            self.assertEqual(view["producer_version"], "related_party@1.0")
            self.assertEqual(view["analysis_provenance"], PROVENANCE)
            self.assertEqual(view["playbook"]["content_digest"], STAMP["content_digest"])
            self.assertEqual(view["playbook"]["run_id"], "run-syn-002-0001")
            self.assertEqual(view["category"], "related_party")
            self.assertEqual(len(view["alternative_explanations"]), 2)
            self.assertTrue(view["evidence_refs"])
            self.assertEqual(rules.labelling_terms(view["explanation"]), [])
        self.assertIn("3 consecutive loss periods", views[1]["explanation"])
        self.assertEqual(views[0]["proposed_action"]["type"], "request_evidence")
        listed = engine.list_findings(ctx(SUPERVISOR), self.repo, case_id=CASE)
        self.assertEqual(len(listed), 5)

    def test_replaying_the_run_is_idempotent(self) -> None:
        first = self.record()
        again = self.record()
        self.assertEqual([v["finding_id"] for v in first], [v["finding_id"] for v in again])
        self.assertTrue(all(v["replayed"] for v in again))
        self.assertEqual(len(self.repo.findings), 5)

    def test_provenance_is_taken_from_the_interpretation_and_required(self) -> None:
        with_interpretation = dict(
            PLAYBOOK_RESULT, interpretation={"analysis_provenance": PROVENANCE}
        )
        views = self.record(result=with_interpretation, analysis_provenance=None)
        self.assertEqual(views[0]["analysis_provenance"], PROVENANCE)
        repo_before = dict(self.repo.findings)
        with self.assertRaises(InvalidFinding) as caught:
            self.record(run_id="run-syn-002-0002", analysis_provenance=None)
        self.assertEqual(caught.exception.field_errors[0]["code"], "provenance_required")
        self.assertEqual(self.repo.findings, repo_before)

    def test_invalid_provenance_is_refused(self) -> None:
        for path, value in [
            (("provider",), "openai"),
            (("role",), "planner"),
            (("settings", "temperature"), 0.2),
            (("validation", "status"), "skipped"),
            (("attempts",), []),
            (("token_usage", "input"), -1),
        ]:
            with self.subTest(field=path):
                broken = copy.deepcopy(PROVENANCE)
                node = broken
                for part in path[:-1]:
                    node = node[part]
                node[path[-1]] = value
                with self.assertRaises(InvalidFinding):
                    self.record(analysis_provenance=broken)
        extra = dict(PROVENANCE, prompt="system prompt text")
        with self.assertRaises(InvalidFinding):
            self.record(analysis_provenance=extra)
        self.assertEqual(self.repo.findings, {})

    def test_a_result_with_a_conclusion_is_refused(self) -> None:
        for field in ("legal_conclusion", "enforcement_conclusion", "decision"):
            with self.subTest(field=field), self.assertRaises(InvalidFinding):
                self.record(result=dict(PLAYBOOK_RESULT, **{field: "established"}))

    def test_rule_and_officer_findings_carry_no_provenance(self) -> None:
        with self.assertRaises(InvalidFinding) as caught:
            engine.record_finding(
                ctx(AUDITOR), self.repo, case_id=CASE,
                body=officer_body(analysis_provenance=PROVENANCE), idempotency_key=key(),
            )  # fmt: skip
        self.assertEqual(caught.exception.field_errors[0]["code"], "provenance_not_allowed")


class FindingRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = repository()

    def refused(self, reason: str, **overrides: Any) -> None:
        with self.assertRaises(InvalidFinding) as caught:
            engine.record_finding(
                ctx(AUDITOR), self.repo, case_id=CASE, body=officer_body(**overrides),
                idempotency_key=key(),
            )  # fmt: skip
        self.assertEqual(caught.exception.field_errors[0]["code"], reason)

    def test_text_that_labels_a_person_is_refused(self) -> None:
        for text in [
            "The director is a fraudster.",
            "High-risk supplier group.",
            "Supplier is a SHELL company",
            "red_flag on the contract",
        ]:
            with self.subTest(text=text):
                self.refused("risk_label_not_allowed", explanation=text)
        self.refused("risk_label_not_allowed", alternative_explanations=["Suspicious timing."])
        self.refused("risk_label_not_allowed", risk_score=3)

    def test_money_is_a_decimal_string_with_a_currency(self) -> None:
        self.refused("invalid_amount", unresolved_amount={"amount": 15000.0, "currency": "LSL"})
        self.refused("invalid_amount", unresolved_amount={"amount": "1e4", "currency": "LSL"})
        self.refused("invalid_currency", unresolved_amount={"amount": "10.00", "currency": "lsl"})

    def test_legal_route_cannot_be_verified_when_recording(self) -> None:
        self.refused(
            "route_not_verifiable_here",
            legal_route={"status": "unverified", "citation": "VAT Act s.1", "verified_by": None},
        )
        self.refused(
            "invalid_choice",
            legal_route={"status": "verified_by_reviewer", "citation": None, "verified_by": None},
        )

    def test_proposed_actions_always_need_human_approval(self) -> None:
        self.refused(
            "human_approval_required",
            proposed_action={"type": "request_evidence", "requires_human_approval": False},
        )
        self.refused("invalid_choice", proposed_action={"type": "deny_refund"})

    def test_producer_specific_fields(self) -> None:
        self.refused("unexpected_field", rule={"rule_id": "TR-X"})
        self.refused("invalid_choice", producer="model")
        self.refused("invalid_choice", kind="proven_abuse")


class AccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = repository()
        engine.record_rule_signal(
            ctx(SERVICE), self.repo, case_id=CASE, signal=SIGNALS[1], evidence=SOURCE
        )

    def test_taxpayers_can_neither_read_nor_record_findings(self) -> None:
        with self.assertRaises(policy.ActionDenied):
            engine.list_findings(ctx(TAXPAYER_SCOPE), self.repo, case_id=CASE)
        (finding_id,) = self.repo.findings
        with self.assertRaises(policy.ActionDenied):
            engine.get_finding(ctx(TAXPAYER_SCOPE), self.repo, case_id=CASE, finding_id=finding_id)
        with self.assertRaises(policy.ActionDenied):
            engine.record_rule_signal(
                ctx(TAXPAYER_SCOPE), self.repo, case_id=CASE, signal=SIGNALS[0], evidence=SOURCE
            )
        with self.assertRaises(policy.ActionDenied):
            engine.feedback_for_review(ctx(TAXPAYER_SCOPE), self.repo)

    def test_another_tenant_cannot_read_the_case_or_finding(self) -> None:
        (finding_id,) = self.repo.findings
        with self.assertRaises(CaseNotFound):
            engine.list_findings(ctx(AUDITOR_T2), self.repo, case_id=CASE)
        with self.assertRaises(CaseNotFound):
            engine.get_finding(ctx(AUDITOR_T2), self.repo, case_id=CASE, finding_id=finding_id)
        # A finding is only reachable through its own case.
        with self.assertRaises(FindingNotFound):
            engine.get_finding(ctx(AUDITOR_T2), self.repo, case_id=CASE_T2, finding_id=finding_id)
        self.assertEqual(engine.list_findings(ctx(AUDITOR_T2), self.repo, case_id=CASE_T2), [])

    def test_officer_findings_need_finding_review_not_a_service(self) -> None:
        with self.assertRaises(policy.ActionDenied):
            engine.record_finding(
                ctx(SERVICE), self.repo, case_id=CASE, body=officer_body(), idempotency_key=key()
            )
        view = engine.record_finding(
            ctx(AUDITOR), self.repo, case_id=CASE, body=officer_body(), idempotency_key=key()
        )
        self.assertEqual(view["recorded_by"], {"user": "auditor@example.test", "role": "auditor"})
        self.assertIsNone(view["feedback_summary"])


class FeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = repository()
        rule = engine.record_rule_signal(
            ctx(SERVICE), self.repo, case_id=CASE, signal=SIGNALS[0], evidence=SOURCE
        )
        agent = engine.record_playbook_result(
            ctx(SERVICE), self.repo, case_id=CASE, result=PLAYBOOK_RESULT, stamp=STAMP,
            run_id="run-syn-002-0001", evidence=SOURCE, analysis_provenance=PROVENANCE,
        )  # fmt: skip
        assert rule
        self.rule_id = rule["finding_id"]
        self.agent_id = agent[2]["finding_id"]

    def feedback(
        self, s: policy.TenantScope = AUDITOR, finding_id: str | None = None, minutes: int = 1,
        k: str | None = None, extra: dict[str, Any] | None = None, **body: Any,
    ) -> dict[str, Any]:  # fmt: skip
        request: dict[str, Any] = {
            "verdict": "accepted",
            "reason_code": "evidence_supports",
            "reason": None,
            "modified_explanation": None,
        }
        request.update(body)
        request.update(extra or {})
        return engine.record_feedback(
            ctx(s, minutes), self.repo, case_id=CASE, finding_id=finding_id or self.agent_id,
            body=request, idempotency_key=k or key(),
        )  # fmt: skip

    def test_feedback_is_append_only_with_the_session_actor_and_a_snapshot(self) -> None:
        first = self.feedback(
            verdict="modified",
            reason_code="missing_context",
            reason="Delivery records may sit with the group office.",
            modified_explanation="No delivery record for the group services is linked yet.",
        )
        self.assertEqual(first["recorded_by"], {"user": "auditor@example.test", "role": "auditor"})
        self.assertIsNone(first["previous_feedback_id"])
        self.assertIs(first["changes_finding"], False)
        self.assertEqual(
            first["finding"],
            {
                "kind": "missing_evidence",
                "category": "related_party",
                "producer": "langgraph_run",
                "producer_version": "related_party@1.0",
                "analysis_provenance": PROVENANCE,
            },
        )
        second = self.feedback(verdict="accepted", minutes=2)
        self.assertEqual(second["previous_feedback_id"], first["feedback_id"])
        other = self.feedback(AUDITOR_2, verdict="rejected", reason_code="legitimate_explanation",
                              reason="The delivery reports are on file already.", minutes=3)  # fmt: skip
        self.assertIsNone(other["previous_feedback_id"])
        history = engine.list_feedback(ctx(SUPERVISOR), self.repo, case_id=CASE)
        self.assertEqual(
            [f["feedback_id"] for f in history],
            [first["feedback_id"], second["feedback_id"], other["feedback_id"]],
        )
        # The finding itself is unchanged and its summary counts latest verdicts per officer.
        finding = engine.get_finding(
            ctx(AUDITOR), self.repo, case_id=CASE, finding_id=self.agent_id
        )
        self.assertEqual(
            finding["explanation"], rules.PLAYBOOK_TEXT["service_delivery_evidence_missing"]
        )
        self.assertEqual(
            finding["feedback_summary"],
            {"accepted": 1, "modified": 0, "rejected": 1, "viewer_verdict": "accepted"},
        )

    def test_replay_returns_the_stored_feedback_and_a_changed_body_conflicts(self) -> None:
        k = key()
        first = self.feedback(k=k)
        again = self.feedback(k=k, minutes=5)
        self.assertTrue(again["replayed"])
        self.assertEqual(first["feedback_id"], again["feedback_id"])
        with self.assertRaises(IdempotencyConflict):
            self.feedback(k=k, reason="Now with a different reason text.")
        # The same key from another officer is that officer's own request.
        mine = self.feedback(AUDITOR_2, k=k)
        self.assertNotEqual(mine["feedback_id"], first["feedback_id"])
        self.assertEqual(len(self.repo.feedback), 2)

    def test_reason_rules_follow_the_contract(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"verdict": "rejected", "reason": None}, "reason_required"),
            ({"verdict": "rejected", "reason": "too short"}, "reason_required"),
            ({"verdict": "modified", "reason": "Long enough reason."}, "modified_text_required"),
            ({"modified_explanation": "Changed wording."}, "modified_text_not_allowed"),
            ({"verdict": "approved"}, "invalid_choice"),
            ({"reason_code": "because"}, "invalid_choice"),
            ({"verdict": "modified", "reason": "Long enough reason.",
              "modified_explanation": "The director is a fraudster."}, "risk_label_not_allowed"),
        ]  # fmt: skip
        for body, code in cases:
            with self.subTest(code=code), self.assertRaises(InvalidFinding) as caught:
                self.feedback(extra=body)
            self.assertEqual(caught.exception.field_errors[0]["code"], code)
        self.assertEqual(self.repo.feedback, {})

    def test_an_actor_in_the_body_is_refused(self) -> None:
        for field in ("recorded_by", "officer_id", "actor"):
            with self.subTest(field=field), self.assertRaises(InvalidMapping) as caught:
                self.feedback(extra={field: "supervisor@example.test"})
            self.assertEqual(caught.exception.code, "invalid_request")
            self.assertEqual(caught.exception.field_errors[0]["code"], "actor_not_allowed")

    def test_who_may_give_feedback(self) -> None:
        for s in (TAXPAYER_SCOPE, SERVICE):
            with self.subTest(role=sorted(s.roles)), self.assertRaises(policy.ActionDenied):
                self.feedback(s)
        with self.assertRaises(CaseNotFound):
            self.feedback(AUDITOR_T2)
        officer = engine.record_finding(
            ctx(AUDITOR), self.repo, case_id=CASE, body=officer_body(), idempotency_key=key()
        )
        with self.assertRaises(InvalidFinding) as caught:
            self.feedback(finding_id=officer["finding_id"])
        self.assertEqual(caught.exception.field_errors[0]["code"], "finding_not_reviewable")
        with self.assertRaises(FindingNotFound):
            self.feedback(finding_id=str(uuid.uuid4()))

    def test_a_concurrent_second_successor_is_refused(self) -> None:
        first = self.feedback()
        stale = engine.FeedbackRecord(
            **{**self.repo.feedback[first["feedback_id"]].__dict__,
               "feedback_id": str(uuid.uuid4()), "registration_key": "other"}
        )  # fmt: skip
        with self.assertRaises(engine.Duplicate):
            self.repo.insert_feedback(stale)  # same chain key: one successor per step

        original = self.repo.latest_feedback
        self.repo.latest_feedback = lambda finding_id, user: None  # type: ignore[method-assign]
        try:
            with self.assertRaises(RevisionConflict):
                self.feedback(minutes=2)
        finally:
            self.repo.latest_feedback = original  # type: ignore[method-assign]

    def test_feedback_for_review_gives_tax070_component_stamps(self) -> None:
        first = self.feedback(finding_id=self.rule_id)
        self.feedback(finding_id=self.rule_id, verdict="rejected",
                      reason_code="evidence_does_not_support",
                      reason="The missing invoices were filed later.", minutes=2)  # fmt: skip
        self.feedback(finding_id=self.agent_id, minutes=3)
        report = engine.feedback_for_review(ctx(SERVICE), self.repo)
        self.assertEqual(report["tenant_id"], T1)
        self.assertIs(report["changes_findings"], False)
        items = report["items"]
        self.assertEqual([i["verdict"] for i in items], ["accepted", "rejected", "accepted"])
        self.assertEqual([i["current"] for i in items], [False, True, True])
        self.assertEqual(items[0]["feedback_id"], first["feedback_id"])
        self.assertEqual(
            items[0]["provenance"],
            [{"kind": "rule", "rule_id": "TR-VAT-REFUND-MISMATCH", "ruleset_version": "1.0.0"}],
        )
        self.assertEqual(
            items[2]["provenance"],
            [{"kind": "playbook", "name": "related_party", "version": "1.0",
              "content_digest": STAMP["content_digest"],
              "registry_version": "SYN-PBR-2026.11.3"}],
        )  # fmt: skip
        later = engine.feedback_for_review(
            ctx(SERVICE), self.repo, since=NOW + datetime.timedelta(minutes=3)
        )
        self.assertEqual(len(later["items"]), 1)
        self.assertEqual(engine.feedback_for_review(ctx(AUDITOR_T2), self.repo)["items"], [])


if __name__ == "__main__":
    unittest.main()
