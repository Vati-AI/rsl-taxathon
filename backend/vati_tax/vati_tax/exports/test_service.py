"""Frappe-free tests for evidence export, manifest and retention (TAX-062).

Covers the task's verification: an export denies unauthorised records and
includes a hash/provenance manifest. Every step and refusal is checked against
the TAX-059 audit ledger the workflow writes to.
"""

from __future__ import annotations

import datetime
import unittest
import uuid
from dataclasses import replace
from typing import Any

from vati_tax.audit import ledger
from vati_tax.audit import service as audit
from vati_tax.audit.ledger import MemoryLedger
from vati_tax.exports import rules, service
from vati_tax.exports.rules import EvidenceItem
from vati_tax.identity import policy
from vati_tax.ids.canonical import IdempotencyConflict, InvalidMapping

T1 = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"
NOW = datetime.datetime(2026, 11, 4, 10, 0, tzinfo=datetime.UTC)
CASE = "20000000-0000-4000-8000-000000000001"
OTHER_CASE = "20000000-0000-4000-8000-000000000002"
TAXPAYER_ID = "80015243-dd34-4e97-b085-7356ffdd042f"
REASON = "Supervisory review of the refund evidence before the case conference."


def scope(user: str, role: policy.Role, tenant: str = T1) -> policy.TenantScope:
    return policy.TenantScope(
        user=user,
        tenant_id=tenant,
        roles=frozenset({role}),
        taxpayer_ids=frozenset({TAXPAYER_ID}) if role == policy.TAXPAYER else frozenset(),
    )


AUDITOR = scope("auditor@example.test", policy.AUDITOR)
AUDITOR_2 = scope("auditor2@example.test", policy.AUDITOR)
SPECIALIST = scope("specialist@example.test", policy.SPECIALIST)
SUPERVISOR = scope("supervisor@example.test", policy.SUPERVISOR)
SUPERVISOR_2 = scope("supervisor2@example.test", policy.SUPERVISOR)
SERVICE = scope("service@example.test", policy.SERVICE)
TAXPAYER = scope("taxpayer@example.test", policy.TAXPAYER)
SUPERVISOR_T2 = scope("supervisor@example.test", policy.SUPERVISOR, T2)


def sha(n: int) -> str:
    return f"{n:064x}"


def item(n: int, *, tenant: str = T1, case: str | None = CASE, **changes: Any) -> EvidenceItem:
    evidence_id = f"30000000-0000-4000-8000-{n:012d}"
    base = EvidenceItem(
        evidence_id=evidence_id,
        tenant_id=tenant,
        taxpayer_id=TAXPAYER_ID,
        case_id=case,
        version=1,
        lineage_id=evidence_id,
        supersedes_evidence_id=None,
        document_sha256=sha(n),
        media_type="application/pdf",
        size_bytes=1000 + n,
        document_type="invoice",
        source="synthetic_fixture",
        received_at=NOW - datetime.timedelta(days=3),
        synthetic=True,
        extraction={
            "extraction_id": f"40000000-0000-4000-8000-{n:012d}",
            "revision": 1,
            "status": "success",
            "extractor": "synthetic_fixture",
            "extractor_version": "1.0.0",
            "document_sha256": sha(n),
        },
    )
    return replace(base, **changes)


E1, E2, E3 = item(1), item(2), item(3)
E_OTHER_CASE = item(7, case=OTHER_CASE)
E_OTHER_TENANT = item(8, tenant=T2)


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


class Harness:
    def __init__(self, schedule: dict[str, int | None] | None = None) -> None:
        self.exports = service.MemoryExports()
        self.evidence = service.MemoryEvidence()
        self.evidence.add(T1, CASE, E1, E2, E3)
        self.evidence.add(T1, OTHER_CASE, E_OTHER_CASE)
        self.evidence.add(T2, CASE, E_OTHER_TENANT)
        self.ledger = MemoryLedger()
        self.ports = service.Ports(
            exports=self.exports,
            evidence=self.evidence,
            ledger=self.ledger,
            schedule=rules.retention_schedule(schedule),
        )

    def ctx(self, who: policy.TenantScope, now: datetime.datetime = NOW) -> audit.Context:
        return audit.Context.build(who, None, now)

    def request(
        self, who: policy.TenantScope = AUDITOR, idem: str | None = None, **body: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "case_id": CASE,
            "evidence_ids": [E2.evidence_id, E1.evidence_id],
            "purpose": "supervisory_review",
            "recipient": "RSL case conference panel (synthetic)",
            "reason": REASON,
        }
        payload.update(body)
        return service.request_export(
            self.ctx(who), self.ports, body=payload, idempotency_key=idem or key()
        )

    def approve(
        self, export: dict[str, Any], who: policy.TenantScope = SUPERVISOR, **kw: Any
    ) -> dict[str, Any]:
        now = kw.pop("now", NOW)
        version = kw.pop("expected_version", export["version"])
        return service.approve_export(
            self.ctx(who, now),
            self.ports,
            export_id=export["export_id"],
            expected_version=version,
            **kw,
        )

    def manifest(
        self,
        export: dict[str, Any],
        who: policy.TenantScope = AUDITOR,
        now: datetime.datetime = NOW,
    ) -> dict[str, Any]:
        return service.get_manifest(self.ctx(who, now), self.ports, export_id=export["export_id"])

    def stored(self, export: dict[str, Any]) -> rules.ExportRecord:
        record = self.exports.get(T1, export["export_id"])
        if record is None:
            raise AssertionError("export not stored")
        return record

    def actions(self, tenant: str = T1) -> list[tuple[str, str, str]]:
        return [(e.action, e.outcome, e.actor) for e in self.ledger.entries(tenant)]


class RequestTests(unittest.TestCase):
    def test_officer_requests_a_scoped_export_and_it_is_recorded(self):
        h = Harness()
        export = h.request()
        self.assertEqual(export["status"], "requested")
        self.assertEqual(export["evidence_ids"], sorted([E1.evidence_id, E2.evidence_id]))
        self.assertFalse(export["replayed"])
        self.assertEqual(h.actions(), [("export.requested", "requested", AUDITOR.user)])
        entry = h.ledger.entries(T1)[0]
        self.assertEqual((entry.category, entry.subject_type), ("write", "export"))
        self.assertEqual(entry.case_id, CASE)

    def test_evidence_from_another_case_is_refused_and_recorded(self):
        h = Harness()
        with self.assertRaises(rules.ExportNotFound):
            h.request(evidence_ids=[E1.evidence_id, E_OTHER_CASE.evidence_id])
        self.assertEqual(h.exports.rows, {})
        self.assertEqual(h.actions(), [("export.request.denied", "not_found", AUDITOR.user)])

    def test_evidence_from_another_tenant_is_refused_like_a_missing_one(self):
        h = Harness()
        with self.assertRaises(rules.ExportNotFound) as other_tenant:
            h.request(evidence_ids=[E_OTHER_TENANT.evidence_id])
        with self.assertRaises(rules.ExportNotFound) as unknown:
            h.request(evidence_ids=[str(uuid.uuid4())])
        self.assertEqual(str(other_tenant.exception), str(unknown.exception))
        self.assertEqual(h.exports.rows, {})

    def test_foreign_tenant_evidence_is_refused_even_if_the_source_leaks_it(self):
        # Defence in depth: a faulty evidence query must not widen an export.
        h = Harness()
        h.evidence.add(T1, CASE, E_OTHER_TENANT)
        with self.assertRaises(rules.ExportNotFound):
            h.request(evidence_ids=[E_OTHER_TENANT.evidence_id])
        with self.assertRaises(rules.ExportNotFound):
            rules.check_scope([E_OTHER_TENANT.evidence_id], T1, [E1, E_OTHER_TENANT])

    def test_unknown_case_is_refused(self):
        h = Harness()
        with self.assertRaises(rules.ExportNotFound):
            h.request(case_id=str(uuid.uuid4()))
        self.assertEqual(h.actions()[0][:2], ("export.request.denied", "not_found"))

    def test_taxpayer_and_service_cannot_request_and_attempts_are_recorded(self):
        for who in (TAXPAYER, SERVICE):
            h = Harness()
            with self.subTest(role=sorted(who.roles)[0]), self.assertRaises(policy.ActionDenied):
                h.request(who)
            self.assertEqual(h.actions(), [("export.request.denied", "forbidden", who.user)])
            self.assertEqual(h.exports.rows, {})

    def test_non_synthetic_evidence_is_refused_in_the_pilot(self):
        h = Harness()
        real = item(9, synthetic=False)
        h.evidence.add(T1, CASE, real)
        with self.assertRaises(rules.ExportConflict) as raised:
            h.request(evidence_ids=[real.evidence_id])
        self.assertEqual(raised.exception.field_errors[0]["code"], "not_synthetic")

    def test_request_needs_named_evidence_purpose_recipient_and_reason(self):
        h = Harness()
        bad: list[dict[str, Any]] = [
            {"evidence_ids": []},
            {"evidence_ids": [E1.evidence_id, E1.evidence_id]},
            {"evidence_ids": ["not-a-uuid"]},
            {"purpose": "fishing"},
            {"recipient": ""},
            {"reason": "too short"},
        ]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(InvalidMapping):
                h.request(**body)
        self.assertEqual(h.ledger.entries(T1), [])

    def test_replay_returns_the_request_and_a_changed_body_conflicts(self):
        h = Harness()
        idem = key()
        first = h.request(idem=idem)
        again = h.request(idem=idem)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["export_id"], first["export_id"])
        with self.assertRaises(IdempotencyConflict):
            h.request(idem=idem, purpose="specialist_review")
        with self.assertRaises(IdempotencyConflict):
            h.request(AUDITOR_2, idem=idem)
        self.assertEqual(len(h.exports.rows), 1)


class ApprovalTests(unittest.TestCase):
    def test_a_different_supervisor_approves_and_the_manifest_is_frozen(self):
        h = Harness()
        export = h.request()
        approved = h.approve(export)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["decided_by"], SUPERVISOR.user)
        self.assertEqual(len(approved["manifest_sha256"]), 64)
        self.assertEqual([a for a, _, _ in h.actions()], ["export.requested", "export.approved"])
        self.assertEqual(h.ledger.entries(T1)[1].category, "approval")

    def test_supervisor_cannot_approve_own_request_and_attempt_is_signalled(self):
        h = Harness()
        export = h.request(SUPERVISOR)
        for _ in range(2):
            with self.assertRaises(policy.ActionDenied):
                h.approve(export, SUPERVISOR)
        self.assertEqual(h.stored(export).status, "requested")
        denied = [a for a in h.actions() if a[0] == "export.approve.denied"]
        self.assertEqual(len(denied), 2)
        signals = ledger.integrity_signals(h.ledger.entries(T1), now=NOW, viewer=SUPERVISOR_2.user)[
            "signals"
        ]
        self.assertIn("repeated_separation_of_duties_attempts", [s["kind"] for s in signals])

    def test_auditor_specialist_and_service_cannot_approve(self):
        for who in (AUDITOR_2, SPECIALIST, SERVICE):
            h = Harness()
            export = h.request()
            with self.subTest(user=who.user), self.assertRaises(policy.ActionDenied):
                h.approve(export, who)
            self.assertEqual(h.actions()[-1][:2], ("export.approve.denied", "forbidden"))

    def test_rejection_needs_a_reason_and_leaves_no_manifest(self):
        h = Harness()
        export = h.request()
        with self.assertRaises(InvalidMapping):
            service.reject_export(
                h.ctx(SUPERVISOR),
                h.ports,
                export_id=export["export_id"],
                expected_version=1,
                reason=None,
            )
        rejected = service.reject_export(
            h.ctx(SUPERVISOR),
            h.ports,
            export_id=export["export_id"],
            expected_version=1,
            reason="Purpose is covered by the existing case file review.",
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertIsNone(rejected["manifest_sha256"])
        with self.assertRaises(rules.ExportConflict):
            h.manifest(export)

    def test_stale_version_decided_and_lapsed_requests_are_refused(self):
        h = Harness()
        export = h.request()
        with self.assertRaises(rules.ExportConflict) as stale:
            h.approve(export, expected_version=0)
        self.assertEqual(stale.exception.field_errors[0]["code"], "stale_version")
        h.approve(export)
        with self.assertRaises(rules.ExportConflict) as decided:
            h.approve(export, SUPERVISOR_2)
        self.assertEqual(decided.exception.field_errors[0]["code"], "not_pending")
        late = h.request()
        with self.assertRaises(rules.ExportConflict) as lapsed:
            h.approve(late, now=NOW + rules.REQUEST_TTL + datetime.timedelta(minutes=1))
        self.assertEqual(lapsed.exception.field_errors[0]["code"], "lapsed")
        self.assertEqual(
            service.get_export(
                h.ctx(AUDITOR, NOW + datetime.timedelta(days=4)),
                h.ports,
                export_id=late["export_id"],
            )["status"],
            "lapsed",
        )

    def test_evidence_removed_from_the_case_after_request_blocks_approval(self):
        h = Harness()
        export = h.request()
        h.evidence.cases[(T1, CASE)] = [E2, E3]
        with self.assertRaises(rules.ExportNotFound):
            h.approve(export)
        self.assertEqual(h.stored(export).status, "requested")

    def test_another_tenant_cannot_see_or_decide_the_export(self):
        h = Harness()
        export = h.request()
        with self.assertRaises(rules.ExportNotFound):
            h.approve(export, SUPERVISOR_T2)
        with self.assertRaises(rules.ExportNotFound):
            service.get_export(h.ctx(SUPERVISOR_T2), h.ports, export_id=export["export_id"])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.export = self.h.request()
        self.h.approve(self.export)

    def test_manifest_lists_hashes_lineage_and_extraction_provenance(self):
        m = self.h.manifest(self.export)
        self.assertEqual(m["schema"], rules.MANIFEST_SCHEMA)
        self.assertEqual(m["entry_count"], 2)
        self.assertFalse(m["content_included"])
        self.assertTrue(m["synthetic"])
        first = m["entries"][0]
        self.assertEqual(first["evidence_id"], E1.evidence_id)
        self.assertEqual(first["document_sha256"], E1.document_sha256)
        self.assertEqual(first["lineage_id"], E1.lineage_id)
        self.assertEqual(first["extraction"]["revision"], 1)
        self.assertTrue(first["extraction"]["matches_document"])
        self.assertEqual(first["retention"]["disposal"], "manual_review_only")
        self.assertNotIn("text", str(first.keys()))
        self.assertTrue(rules.verify_manifest(m))

    def test_extraction_of_different_bytes_is_flagged_in_the_manifest(self):
        h = Harness()
        odd = item(5, extraction={**(E1.extraction or {}), "document_sha256": sha(99)})
        h.evidence.add(T1, CASE, odd)
        export = h.request(evidence_ids=[odd.evidence_id])
        h.approve(export)
        entry = h.manifest(export)["entries"][0]
        self.assertFalse(entry["extraction"]["matches_document"])

    def test_any_change_to_the_manifest_is_detected(self):
        m = self.h.manifest(self.export)
        tampered = {**m, "entries": [{**m["entries"][0], "document_sha256": sha(42)}]}
        self.assertFalse(rules.verify_manifest(tampered))
        stored = self.h.stored(self.export)
        self.h.exports.rows[(T1, stored.export_id)] = replace(stored, manifest=tampered)
        with self.assertRaises(rules.ExportConflict) as raised:
            self.h.manifest(self.export)
        self.assertEqual(raised.exception.field_errors[0]["code"], "manifest_altered")

    def test_only_requester_and_approver_read_it_and_reads_are_recorded(self):
        self.h.manifest(self.export, AUDITOR)
        self.h.manifest(self.export, SUPERVISOR)
        for who in (AUDITOR_2, SUPERVISOR_2):
            with self.subTest(user=who.user), self.assertRaises(rules.ExportNotFound):
                self.h.manifest(self.export, who)
        with self.assertRaises(policy.ActionDenied):
            self.h.manifest(self.export, TAXPAYER)
        reads = [(a, actor) for a, _, actor in self.h.actions() if a == "export.read"]
        self.assertEqual(reads, [("export.read", AUDITOR.user), ("export.read", SUPERVISOR.user)])
        denied = [a for a in self.h.actions() if a[0] == "export.read.denied"]
        self.assertEqual(len(denied), 3)

    def test_access_expires_after_24_hours(self):
        later = NOW + rules.ACCESS_TTL + datetime.timedelta(minutes=1)
        with self.assertRaises(rules.ExportConflict) as raised:
            self.h.manifest(self.export, now=later)
        self.assertEqual(raised.exception.field_errors[0]["code"], "access_expired")

    def test_the_whole_workflow_leaves_an_intact_ledger(self):
        self.h.manifest(self.export)
        with self.assertRaises(rules.ExportNotFound):
            self.h.manifest(self.export, AUDITOR_2)
        report = ledger.verify_chain(self.h.ledger.entries(T1))
        self.assertTrue(report["intact"])
        self.assertEqual(
            [e.category for e in self.h.ledger.entries(T1)],
            ["write", "approval", "read", "denied"],
        )


class RetentionTests(unittest.TestCase):
    def test_default_schedule_sets_no_disposal_date(self):
        schedule = rules.retention_schedule()
        meta = rules.retention_metadata("evidence", NOW, schedule)
        self.assertIsNone(meta["review_after"])
        self.assertFalse(meta["policy_verified"])
        self.assertEqual(meta["disposal"], "manual_review_only")

    def test_configured_period_gives_a_review_date_never_a_deletion(self):
        schedule = rules.retention_schedule({"evidence": 30})
        meta = rules.retention_metadata("evidence", NOW, schedule)
        self.assertEqual(meta["review_after"], "2026-12-04T10:00:00Z")
        self.assertEqual(meta["disposal"], "manual_review_only")

    def test_audit_ledger_is_permanent_and_bad_config_is_refused(self):
        with self.assertRaises(InvalidMapping):
            rules.retention_schedule({"audit_ledger": 365})
        for bad in ({"evidance": 30}, {"evidence": 0}, {"evidence": "30"}, {"evidence": True}):
            with self.subTest(config=bad), self.assertRaises(InvalidMapping):
                rules.retention_schedule(bad)
        meta = rules.retention_metadata("audit_ledger", NOW, {"audit_ledger": 1})
        self.assertIsNone(meta["retain_days"])

    def test_policy_view_is_staff_only_and_labelled_unverified(self):
        h = Harness({"evidence": 30})
        view = service.retention_policy(h.ctx(AUDITOR), h.ports)
        self.assertFalse(view["policy_verified"])
        classes = {c["retention_class"]: c for c in view["classes"]}
        self.assertEqual(classes["evidence"]["retain_days"], 30)
        self.assertTrue(classes["audit_ledger"]["permanent"])
        for who in (TAXPAYER, SERVICE):
            with self.subTest(user=who.user), self.assertRaises(policy.ActionDenied):
                service.retention_policy(h.ctx(who), h.ports)


class PolicyMatrixTests(unittest.TestCase):
    def test_export_actions_are_human_only_with_separation_of_duties(self):
        for action in (policy.EXPORT_REQUEST, policy.EXPORT_APPROVE, policy.EXPORT_READ):
            self.assertIn(action, policy.HUMAN_ONLY_ACTIONS)
            self.assertNotIn(policy.SERVICE, policy.ACTION_ROLES[action])
            self.assertNotIn(policy.TAXPAYER, policy.ACTION_ROLES[action])
        self.assertIn(policy.EXPORT_APPROVE, policy.SEPARATION_OF_DUTIES)
        self.assertEqual(policy.ACTION_ROLES[policy.EXPORT_APPROVE], {policy.SUPERVISOR})


if __name__ == "__main__":
    unittest.main()
