"""Frappe-free tests for the assurance pipeline step (TAX-121, ADR-042).

The check result below has the shape ``ai/evaluation/assurance_pipeline.assess_taxpayer``
returns (trimmed); tests/ai/test_assurance_live.py runs the real checks end to end over
memory repositories and the TAX-118 scorer.
"""

from __future__ import annotations

import copy
import datetime
import unittest
from typing import Any

from vati_tax.assurance import engine, rules
from vati_tax.findings import engine as findings_engine
from vati_tax.findings import rules as finding_rules
from vati_tax.findings.engine import CaseRef, MemoryRepository
from vati_tax.identity import policy
from vati_tax.ids.canonical import CaseNotFound, InvalidMapping

T1 = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"
TAXPAYER = "80015243-dd34-4e97-b085-7356ffdd042f"
CASE = "40000000-0000-4000-8000-000000000121"
SUBMISSION = "30000000-0000-4000-8000-000000000121"
BUNDLE = "50000000-0000-4000-8000-000000000121"
BUNDLE_SHA = "a" * 64
NOW = datetime.datetime(2026, 11, 6, 12, 0, tzinfo=datetime.UTC)
DIGEST = "sha256:" + "0" * 64
REGISTER = "tax116-candidates-2026.11.0+tax116-policy-gaps-2026.11.0"


def scope(role: policy.Role, tenant: str = T1) -> policy.TenantScope:
    taxpayers = frozenset({TAXPAYER}) if role == policy.TAXPAYER else frozenset()
    return policy.TenantScope(user=f"{role}@example.test", tenant_id=tenant,
                              roles=frozenset({role}), taxpayer_ids=taxpayers)  # fmt: skip


SERVICE, AUDITOR, TAXPAYER_SCOPE = (
    scope(policy.SERVICE), scope(policy.AUDITOR), scope(policy.TAXPAYER)
)  # fmt: skip
SOURCES = {
    "ORA-111-001": {"source_system": "online_refunds", "record_id": "ORA-111-001",
                    "contract_id": "rsl.online_refunds.claim.v0-assumed", "record_sha256": "b" * 64},
    "VAT-111-2025-04": {"source_system": "etax", "record_id": "VAT-111-2025-04",
                        "contract_id": "rsl.etax.vat_return.v0-assumed", "record_sha256": "c" * 64},
    "CIT-111-2024": {"source_system": "etax", "record_id": "CIT-111-2024",
                     "contract_id": "rsl.etax.cit_return.v0-assumed", "record_sha256": "d" * 64},
}  # fmt: skip


def money(amount: str) -> dict[str, str]:
    return {"amount": amount, "currency": "LSL"}


def route(code: str, *, gap: bool = False) -> dict[str, Any]:
    if gap:
        return {"finding_code": code, "route_status": "policy_gap", "candidates": [],
                "policy_gaps": ["GAP-ARMS-LENGTH"], "eligible_for_assessment": False}  # fmt: skip
    return {"finding_code": code, "route_status": "candidate_unverified",
            "candidates": [{"rule_id": "CAND-X", "instrument": "VAT Act 2001"}],
            "policy_gaps": [], "eligible_for_assessment": False}  # fmt: skip


def result() -> dict[str, Any]:
    lane = {
        "claim_id": "ORA-111-001", "lane": "investigation", "policy_version": "tax114-lanes-v1",
        "reasons": ["M100.00 of input VAT is unsupported with an authenticity indicator."],
        "inputs": {"recompute": {"lines_not_in_books": ["ORA-111-001-L02"]}},
        "service_target": {"decision_target_working_days": 90, "decision_due": "2025-09-25",
                           "status": "proposed_target_verify_against_vat_act"},
        "claimed_amount": money("300.00"), "undisputed_amount": money("200.00"),
        "undisputed_status": "recommendation_for_refund_auditor",
        "disputed_amount": money("100.00"), "disputed_items": [], "questions": [],
        "requires_officer_review": True, "decision": None,
    }  # fmt: skip
    return {
        "tin": "111",
        "pipeline_version": "tax118-pipeline-v1",
        "flags": ["fake_invoices", "policy_gap", "related_party_loss",
                  "untaxed_imported_services"],
        "findings": [
            {"code": "input_vat_unsupported", "issue": "fake_invoices",
             "route": route("input_vat_unsupported")},
            {"code": "wht_declared_below_expected", "issue": "untaxed_imported_services",
             "reason": "M1.8m booked to F-ZA-1 (ZA); withholding declared M0.",
             "route": route("wht_declared_below_expected")},
            {"code": "related_party_fees_with_persistent_losses", "issue": "related_party_loss",
             "reason": "Losses in 4 consecutive years.", "route": route("rp")},
            {"code": "related_party_interest_free_loan", "issue": "policy_gap",
             "reason": "This suspicious loan.", "route": route("loan", gap=True)},
        ],
        "claims": [{
            "claim_id": "ORA-111-001", "period": "2025-04", "lane": lane,
            "match": {"lines": [
                {"line_id": "ORA-111-001-L01", "status": "matched", "treatment": "accepted",
                 "notes": [], "questions": []},
                {"line_id": "ORA-111-001-L02", "status": "unmatched", "treatment": "question",
                 "notes": ["supplier_not_registered"], "questions": ["q"]},
            ]},
            "authenticity_indicator": True,
        }],
        "recompute": {"recompute_version": "tax113-recompute-v1", "comparison": [{}, {}, {}],
                      "findings": [{"code": "declared_input_vat_exceeds_recomputed",
                                    "amount": money("100.00"),
                                    "evidence_ids": ["VAT-111-2025-04", "saft:111:2025-04"]}]},
        "three_way": {"match_version": "tax115-v1", "total_gap": money("180000.00"),
                      "rows": [{"counterparty": "F-ZA-1",
                                "explanations_tested": ["treaty_relief_with_residence_certificate"]}],
                      "flags": [{"code": "wht_declared_below_expected", "counterparty": "F-ZA-1",
                                 "year": "window", "amount": money("180000.00"),
                                 "evidence_ids": ["bank:FX-1", "ledger:SI-1"],
                                 "reason": "M1.8m booked to F-ZA-1 (ZA); withholding declared M0."}]},
        "related_party": {"signals_version": "bp6-signals-v1", "alternative_explanations_tested": [],
                          "findings": [
                              {"code": "related_party_fees_with_persistent_losses",
                               "evidence_ids": ["CIT-111-2024"], "reason": "Losses in 4 consecutive years."},
                              {"code": "related_party_interest_free_loan", "counterparty": "F-MU-1",
                               "evidence_ids": ["AGR-111-1"], "reason": "This suspicious loan."}]},
        "work_arrangement": {"referrals": []},
        "flagged_amounts": {"fake_invoices": "100.00", "untaxed_imported_services": "180000.00"},
        "inputs_status": {"idms": "ok", "rsl_fraud_model": "ok", "use_score": True},
        "seconds": 0.01,
        "decision": None,
    }  # fmt: skip


def bodies(res: dict[str, Any] | None = None) -> list[tuple[dict[str, Any], str]]:
    return rules.finding_bodies(res or result(), sources=SOURCES, bundle_evidence_id=BUNDLE,
                                register_version=REGISTER, input_digest=DIGEST)  # fmt: skip


class EventTests(unittest.TestCase):
    def test_event_carries_references_only_and_round_trips(self) -> None:
        key, payload = rules.request_event(SUBMISSION, 2)
        self.assertEqual(key, f"vati:assurance:{SUBMISSION}:v2")
        self.assertEqual(rules.parse_request(payload), (SUBMISSION, 2))
        with self.assertRaises(InvalidMapping):
            rules.parse_request({**payload, "claimed_amount": "1.00"})
        with self.assertRaises(InvalidMapping):
            rules.request_event(SUBMISSION, 0)


class BundleTests(unittest.TestCase):
    def test_records_round_trip_exactly_including_empty_parts(self) -> None:
        records: dict[str, Any] = {
            "tin": "111",
            "saft": {"header": {"tin": "111"}, "suppliers": [], "supplier_invoices": [
                {"entry_id": "SI-1", "net": "10.00", "memo": 'comma, "quote"\nnewline'}]},
            "foreign_payments_csv": "a,b\n1,2\n",
            "agreements": [],
            "documents": {"DOC-1": {"fields": {"vat": "1.50"}}},
            "loss_explanations": [],
            "permits": None,
        }  # fmt: skip
        content = rules.bundle_csv(records)
        self.assertEqual(rules.parse_bundle(content), records)
        self.assertTrue(content.startswith(b"format,path,kind,key,value_json\n"))

    def test_anything_else_is_refused(self) -> None:
        for content in (b"", b"a,b\n1,2\n", b"\xff\xfe", rules.bundle_csv({"tin": "1"})):
            with self.subTest(content=content[:20]), self.assertRaises(rules.InvalidAssuranceInput):
                rules.parse_bundle(content)


class StatusTests(unittest.TestCase):
    def test_a_required_system_missing_means_not_run_never_clean(self) -> None:
        status, reason, checks = rules.run_status({"etax": "unavailable", "online_refunds": "ok"})
        self.assertEqual((status, reason), ("not_run", "etax_unavailable"))
        self.assertEqual(set(checks.values()), {"not_run"})

    def test_an_optional_system_missing_degrades_the_checks_that_read_it(self) -> None:
        ok = {"etax": "ok", "online_refunds": "ok"}
        self.assertEqual(rules.run_status(ok)[:2], ("completed", None))
        status, _reason, checks = rules.run_status({**ok, "idms": "unavailable"})
        self.assertEqual(status, "degraded")
        self.assertEqual(checks["idms_line_match"], "degraded_idms_unavailable")
        self.assertEqual(checks["source_recompute"], "ran")

    def test_source_status_is_ok_only_when_every_read_was(self) -> None:
        envs = [{"source_system": "etax", "status": "ok"},
                {"source_system": "etax", "status": "unavailable"},
                {"source_system": "idms", "status": "ok"}]  # fmt: skip
        self.assertEqual(rules.source_status(envs), {"etax": "unavailable", "idms": "ok"})


class CitationTests(unittest.TestCase):
    def test_rsl_records_become_source_refs_and_the_rest_taxpayer_records(self) -> None:
        source_refs, evidence_refs = rules.cite(
            ["VAT-111-2025-04", "ORA-111-001-L02", "saft:111:2025-04", "AGR-111-1", "AGR-111-1"],
            sources=SOURCES, bundle_evidence_id=BUNDLE,
        )  # fmt: skip
        self.assertEqual([(r["record_id"], r["locator"]) for r in source_refs],
                         [("VAT-111-2025-04", None), ("ORA-111-001", "ORA-111-001-L02")])  # fmt: skip
        self.assertEqual([r["locator"]["record"] for r in evidence_refs],
                         ["saft:111:2025-04", "AGR-111-1"])  # fmt: skip
        self.assertTrue(all(r["evidence_id"] == BUNDLE for r in evidence_refs))


class FindingBodyTests(unittest.TestCase):
    def test_every_finding_is_a_valid_vati_check_finding(self) -> None:
        out = bodies()
        self.assertEqual([b["category"] for b, _ in out],
                         ["refund_reconciliation", "imported_services", "related_party",
                          "policy_gap"])  # fmt: skip
        for body, key in out:
            finding = finding_rules.FindingInput.build(body)
            self.assertEqual(finding.producer, "vati_check")
            self.assertEqual(finding.rule_id, body["code"])
            self.assertTrue(key.startswith("check:"))
            self.assertIs(body["candidate_route"]["eligible_for_assessment"], False)
            self.assertIs(body["proposed_action"]["requires_human_approval"], True)
            self.assertIsNone(body["legal_route"]["citation"])

    def test_amounts_routes_and_actions_follow_the_check(self) -> None:
        by_code = {b["code"]: b for b, _ in bodies()}
        vat = by_code["input_vat_unsupported"]
        self.assertEqual(vat["unresolved_amount"], money("100.00"))
        self.assertEqual(vat["proposed_action"]["type"], "request_evidence")
        self.assertIn(("ORA-111-001", "ORA-111-001-L02"),
                      {(r["record_id"], r["locator"]) for r in vat["source_refs"]})  # fmt: skip
        wht = by_code["wht_declared_below_expected"]
        self.assertEqual(wht["unresolved_amount"], money("180000.00"))
        self.assertEqual(wht["alternative_explanations"],
                         ["Tested alternative explanation: treaty relief with residence certificate."])  # fmt: skip
        gap = by_code["related_party_interest_free_loan"]
        self.assertEqual(gap["legal_route"]["status"], "no_verified_route")
        self.assertEqual(gap["proposed_action"]["type"], "policy_gap_review")
        self.assertEqual(gap["candidate_route"]["policy_gaps"], ["GAP-ARMS-LENGTH"])
        # The check's reason used a labelling word, so the neutral text replaced it.
        self.assertNotIn("suspicious", gap["explanation"].lower())
        self.assertEqual(by_code["related_party_fees_with_persistent_losses"]["proposed_action"]
                         ["type"], "specialist_review")  # fmt: skip

    def test_service_payments_outside_service_accounts_ask_for_evidence(self) -> None:
        # TAX-141: fees booked as general expenses, described as services by the bank.
        res = result()
        code = "foreign_service_payments_outside_service_accounts"
        reason = ("M2.0m paid to F-ZA-2 (ZA) in window is described as services by the bank "
                  "narration. Ask the taxpayer what the payments were for.")  # fmt: skip
        res["findings"].append({"code": code, "issue": "untaxed_imported_services",
                                "reason": reason, "route": route(code)})  # fmt: skip
        res["three_way"]["rows"].append(
            {"counterparty": "F-ZA-2",
             "explanations_tested": ["central_bank_purpose_not_reported",
                                     "bank_narration_describes_services"]})  # fmt: skip
        res["three_way"]["flags"].append(
            {"code": code, "counterparty": "F-ZA-2", "year": "window",
             "amount": money("50000.00"), "evidence_ids": ["bank:FX-2", "ledger:SI-2"],
             "reason": reason})  # fmt: skip
        body = {b["code"]: b for b, _ in bodies(res)}[code]
        finding_rules.FindingInput.build(body)
        self.assertEqual(body["category"], "imported_services")
        self.assertEqual(body["unresolved_amount"], money("50000.00"))
        self.assertEqual(body["proposed_action"]["type"], "request_evidence")
        self.assertIn("Tested alternative explanation: bank narration describes services.",
                      body["alternative_explanations"])  # fmt: skip
        self.assertIs(body["candidate_route"]["eligible_for_assessment"], False)

    def test_same_inputs_give_the_same_keys_and_new_inputs_new_ones(self) -> None:
        self.assertEqual([k for _, k in bodies()], [k for _, k in bodies()])
        other = rules.finding_bodies(result(), sources=SOURCES, bundle_evidence_id=BUNDLE,
                                     register_version=REGISTER, input_digest="sha256:" + "1" * 64)  # fmt: skip
        self.assertFalse({k for _, k in bodies()} & {k for _, k in other})

    def test_a_decision_or_an_eligible_route_is_refused(self) -> None:
        res = result()
        res["decision"] = "refund_denied"
        with self.assertRaises(rules.InvalidAssuranceInput):
            bodies(res)
        res = result()
        res["findings"][0]["route"]["eligible_for_assessment"] = True
        with self.assertRaises(rules.InvalidAssuranceInput):
            bodies(res)

    def test_findings_record_refuses_inconsistent_routes(self) -> None:
        body = copy.deepcopy(bodies()[3][0])
        body["legal_route"]["status"] = "unverified"
        with self.assertRaises(finding_rules.InvalidFinding):
            finding_rules.FindingInput.build(body)
        body = copy.deepcopy(bodies()[0][0])
        body["candidate_route"]["eligible_for_assessment"] = True
        with self.assertRaises(finding_rules.InvalidFinding):
            finding_rules.FindingInput.build(body)
        body = copy.deepcopy(bodies()[0][0])
        body["source_refs"][0]["record_sha256"] = "nothex"
        with self.assertRaises(finding_rules.InvalidFinding):
            finding_rules.FindingInput.build(body)
        del body["rule"]
        with self.assertRaises(finding_rules.InvalidFinding):
            finding_rules.FindingInput.build(body)


def findings_repo() -> MemoryRepository:
    repo = MemoryRepository()
    repo.cases[CASE] = CaseRef(T1, CASE, TAXPAYER)
    repo.evidence[BUNDLE] = (T1, TAXPAYER, BUNDLE_SHA)
    return repo


class FindingsRecordTests(unittest.TestCase):
    def test_check_findings_are_recorded_once_and_read_back_with_routes(self) -> None:
        repo = findings_repo()
        ctx = findings_engine.Context.build(SERVICE, None, NOW)
        first = [findings_engine.record_finding(ctx, repo, case_id=CASE, body=b,
                                                idempotency_key=k) for b, k in bodies()]  # fmt: skip
        again = [findings_engine.record_finding(ctx, repo, case_id=CASE, body=b,
                                                idempotency_key=k) for b, k in bodies()]  # fmt: skip
        self.assertEqual(len(repo.findings), 4)
        self.assertTrue(all(v["replayed"] for v in again))
        self.assertEqual({v["finding_id"] for v in first}, {v["finding_id"] for v in again})
        read = findings_engine.list_findings(findings_engine.Context.build(AUDITOR, None, NOW),
                                             repo, case_id=CASE)  # fmt: skip
        view = next(v for v in read if v["code"] == "input_vat_unsupported")
        self.assertEqual(view["producer"], "vati_check")
        self.assertEqual(view["evidence_refs"][0]["document_sha256"], BUNDLE_SHA)
        self.assertEqual(view["candidate_route"]["candidates"][0]["rule_id"], "CAND-X")
        contract = findings_engine.contract_finding(view)
        self.assertIn("source_refs", contract)
        stamp = finding_rules.component_stamp(
            findings_engine.snapshot(repo.findings[first[0]["finding_id"]])
        )
        self.assertEqual(stamp, {"kind": "rule", "rule_id": "input_vat_unsupported",
                                 "ruleset_version": "tax118-pipeline-v1"})  # fmt: skip

    def test_taxpayers_cannot_record_check_findings(self) -> None:
        ctx = findings_engine.Context.build(TAXPAYER_SCOPE, None, NOW)
        body, key = bodies()[0]
        with self.assertRaises(policy.ActionDenied):
            findings_engine.record_finding(ctx, findings_repo(), case_id=CASE, body=body,
                                           idempotency_key=key)  # fmt: skip


IN_AUDIT = rules.rsl_claim_status({"status": "audit_in_progress"})


class LaneTests(unittest.TestCase):
    def lane(self) -> dict[str, Any]:
        return rules.lane_proposal(result()["claims"][0], sources=SOURCES, rsl_status=IN_AUDIT)

    def record(self, repo: engine.MemoryRepository, body: dict[str, Any], digest: str = DIGEST,
               who: policy.TenantScope = SERVICE) -> dict[str, Any]:  # fmt: skip
        return engine.record_lane_proposal(
            who, repo, case_id=CASE, body=body, producer_version="tax118-pipeline-v1",
            input_digest=digest, run_key="r" * 64, now=NOW, correlation_id=SUBMISSION,
        )  # fmt: skip

    def test_the_lane_carries_target_amounts_inputs_and_no_decision(self) -> None:
        lane = self.lane()
        self.assertEqual(lane["lane"], "investigation")
        self.assertEqual(lane["service_target"]["decision_due"], "2025-09-25")
        self.assertEqual(lane["undisputed_amount"], money("200.00"))
        self.assertEqual(lane["inputs"]["recompute"]["lines_not_in_books"], ["ORA-111-001-L02"])
        self.assertEqual([m["status"] for m in lane["line_match"]], ["matched", "unmatched"])
        self.assertEqual(lane["source_refs"][0]["record_id"], "ORA-111-001")
        self.assertNotIn("decision", lane)
        self.assertEqual(lane["undisputed_status"], "recommendation_for_refund_auditor")
        self.assertEqual(lane["rsl_claim_status"], {"status": "audit_in_progress",
                                                    "reported": True, "in_rsl_audit": True,
                                                    "owner": "RSL"})  # fmt: skip

    def test_the_rsl_status_is_read_as_rsls_and_unknown_codes_are_not_reported(self) -> None:
        # TAX-132: every TAX-111 status value, and records with none or an unknown one.
        for status in sorted(rules.RSL_CLAIM_STATUSES):
            view = rules.rsl_claim_status({"status": status})
            self.assertEqual((view["status"], view["reported"], view["owner"]),
                             (status, True, "RSL"))  # fmt: skip
            self.assertEqual(view["in_rsl_audit"], status == "audit_in_progress")
            withheld = rules.lane_withheld(view)
            self.assertEqual(withheld is not None, status in {"processed", "rejected",
                                                              "cancelled"})  # fmt: skip
        for record in ({}, {"status": None}, {"status": "paid_out"}):
            view = rules.rsl_claim_status(record)
            self.assertEqual((view["status"], view["reported"]), (None, False))
            self.assertIsNone(rules.lane_withheld(view))

    def test_no_lane_for_a_claim_rsl_already_decided(self) -> None:
        for status in ("processed", "rejected", "cancelled"):
            decided = rules.rsl_claim_status({"status": status})
            with self.subTest(status=status), self.assertRaises(rules.InvalidAssuranceInput):
                rules.validate_lane({**self.lane(), "rsl_claim_status": decided})

    def test_a_lane_never_sets_status_release_or_payment(self) -> None:
        lane = self.lane()
        for change in ({"undisputed_status": "released"}, {"undisputed_status": "approved"},
                       {"refund_status": "paid"}, {"release_amount": money("1.00")},
                       {"rsl_claim_status": {**IN_AUDIT, "owner": "Vati"}},
                       {"rsl_claim_status": {**IN_AUDIT, "status": "paid"}}):  # fmt: skip
            with self.subTest(change=change), self.assertRaises(rules.InvalidAssuranceInput):
                rules.validate_lane({**lane, **change})

    def test_invalid_lanes_are_refused(self) -> None:
        for change in ({"lane": "pay_now"}, {"undisputed_amount": money("250.00")},
                       {"claimed_amount": {"amount": 300.0, "currency": "LSL"}},
                       {"requires_officer_review": False}, {"decision": "paid"}):  # fmt: skip
            with self.subTest(change=change), self.assertRaises(rules.InvalidAssuranceInput):
                rules.validate_lane({**self.lane(), **change})
        claim = result()["claims"][0]
        claim["lane"]["decision"] = "approved"
        with self.assertRaises(rules.InvalidAssuranceInput):
            rules.lane_proposal(claim, sources=SOURCES, rsl_status=IN_AUDIT)

    def test_replay_writes_nothing_and_new_inputs_add_a_revision(self) -> None:
        repo = engine.MemoryRepository()
        first = self.record(repo, self.lane())
        self.assertTrue(self.record(repo, self.lane())["replayed"])
        self.assertEqual(len(repo.lanes), 1)
        changed = {**self.lane(), "lane": "standard"}
        second = self.record(repo, changed, digest="sha256:" + "2" * 64)
        self.assertEqual((second["revision"], second["supersedes_proposal_id"]),
                         (2, first["proposal_id"]))  # fmt: skip
        view = engine.case_assurance(AUDITOR, repo, case={"case_id": CASE, "tenant_id": T1})
        self.assertEqual([lane["lane"] for lane in view["lane_proposals"]], ["standard"])
        self.assertIsNone(view["decision"])

    def test_only_staff_and_services_record_or_read_and_other_tenants_see_nothing(self) -> None:
        repo = engine.MemoryRepository()
        with self.assertRaises(policy.ActionDenied):
            self.record(repo, self.lane(), who=TAXPAYER_SCOPE)
        self.record(repo, self.lane())
        with self.assertRaises(policy.ActionDenied):
            engine.case_assurance(TAXPAYER_SCOPE, repo, case={"case_id": CASE, "tenant_id": T1})
        with self.assertRaises(CaseNotFound):
            engine.case_assurance(scope(policy.AUDITOR, T2), repo,
                                  case={"case_id": CASE, "tenant_id": T1})  # fmt: skip


class RunTests(unittest.TestCase):
    def test_one_run_per_event(self) -> None:
        repo = engine.MemoryRepository()
        record = engine.RunRecord(
            run_id="60000000-0000-4000-8000-000000000001", tenant_id=T1, case_id=CASE,
            submission_id=SUBMISSION, claim_ref=None, status="not_run",
            reason="upstream_unavailable", services={"rsl_sources": {"mode": "unavailable"}},
            checks={c: "not_run" for c in rules.CHECKS}, summary={}, finding_ids=(),
            lane_proposal_id=None, producer_version=None, input_digest=None,
            event_id="70000000-0000-4000-8000-000000000001", correlation_id=SUBMISSION,
            started_at=NOW, seconds=0.01, run_key=engine.run_key(T1, "e1"),
        )  # fmt: skip
        self.assertFalse(engine.record_run(repo, record)["replayed"])
        self.assertTrue(engine.record_run(repo, record)["replayed"])
        self.assertEqual(len(repo.runs), 1)


if __name__ == "__main__":
    unittest.main()
