"""Bench tests for case findings and officer finding feedback (TAX-086).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. The SYN-002
rule rows and related-party result are the copies in test_engine.py (the app
cannot read integration/ or ai/); tests/integration/test_case_findings.py
records the live producers' output with the same engine.
"""

from __future__ import annotations

import copy
import json
import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.evidence import store as evidence_store
    from vati_tax.evidence.test_evidence_bench import pdf
    from vati_tax.findings import engine, store
    from vati_tax.findings.engine import Duplicate
    from vati_tax.findings.rules import FindingNotFound, InvalidFinding
    from vati_tax.findings.test_engine import PLAYBOOK_RESULT, PROVENANCE, SIGNALS, STAMP
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids import mapping
    from vati_tax.ids.canonical import CaseNotFound, IdempotencyConflict, InvalidMapping
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

RUN = uuid.uuid4().hex[:8]
SOURCES = (
    "RET-SYN-002",
    "INV-SYN-002-01",
    "INV-SYN-002-02",
    "CTR-SYN-002-01",
    "CTR-SYN-002-02",
    "PAY-SYN-002-01",
)


def email(name: str) -> str:
    return f"tax086-{name}-{RUN}@example.test"


def make_user(name: str) -> str:
    user = email(name)
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }
    ).insert(ignore_permissions=True)
    return user


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


class FindingBenchTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic findings tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic findings tenant two")
        cls.taxpayer = str(uuid.uuid4())
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer", cls.t1, policy.TAXPAYER, cls.taxpayer),
            ("auditor", cls.t1, policy.AUDITOR, None),
            ("auditor-2", cls.t1, policy.AUDITOR, None),
            ("supervisor", cls.t1, policy.SUPERVISOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        cls.users["service"] = ensure_service_identity(email("service"), [cls.t1])
        cls.case_id = mapping.register_case(
            tenant_id=cls.t1, idempotency_key=key(), taxpayer_id=cls.taxpayer
        )["case_id"]
        cls.t2_case = mapping.register_case(tenant_id=cls.t2, idempotency_key=key())["case_id"]
        frappe.db.commit()
        frappe.set_user(cls.users["auditor"])
        clear_principal_cache()
        cls.evidence = {}
        cls.sha = {}
        for source in SOURCES:
            view = evidence_store.register_upload(
                tenant_id=cls.t1,
                content=pdf(f"tax086-{source}-{RUN}"),
                filename=f"synthetic-{source.lower()}.pdf",
                document_type="invoice",
                idempotency_key=key(),
                taxpayer_id=cls.taxpayer,
                case_id=cls.case_id,
            )
            cls.evidence[source] = view["evidence_id"]
            cls.sha[view["evidence_id"]] = view["document_sha256"]
        frappe.set_user("Administrator")
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.rollback()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()

    def tearDown(self):
        frappe.set_user("Administrator")

    def as_user(self, name: str) -> str:
        frappe.set_user(self.users[name])
        clear_principal_cache()
        return self.users[name]

    def new_case(self) -> str:
        frappe.set_user("Administrator")
        case_id = mapping.register_case(
            tenant_id=self.t1, idempotency_key=key(), taxpayer_id=self.taxpayer
        )["case_id"]
        return case_id

    def record_signals(self, case_id: str) -> list[dict]:
        self.as_user("service")
        views = [
            store.record_rule_signal(
                tenant_id=self.t1, case_id=case_id, signal=s, evidence=self.evidence
            )
            for s in SIGNALS
        ]
        return [v for v in views if v]

    def record_playbook(self, case_id: str, run_id: str = "run-syn-002-bench") -> list[dict]:
        self.as_user("service")
        return store.record_playbook_result(
            tenant_id=self.t1, case_id=case_id, result=PLAYBOOK_RESULT, stamp=STAMP,
            run_id=run_id, evidence=self.evidence, analysis_provenance=PROVENANCE,
        )  # fmt: skip

    def test_rule_signals_are_stored_read_back_and_replays_write_nothing(self):
        case_id = self.new_case()
        recorded = self.record_signals(case_id)
        self.assertEqual(len(recorded), 3)
        rows = frappe.get_all(
            store.FINDING,
            filters={"case_id": case_id},
            fields=["tenant_id", "producer", "rule_id", "ruleset_version", "analysis_provenance",
                    "unresolved_amount", "unresolved_currency", "revision"],
        )  # fmt: skip
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row.tenant_id, self.t1)
            self.assertEqual(row.producer, "jube_rule")
            self.assertEqual(row.ruleset_version, "1.0.0")
            self.assertIsNone(row.analysis_provenance)
            self.assertEqual(row.revision, 1)
        mismatch = next(r for r in rows if r.rule_id == "TR-VAT-REFUND-MISMATCH")
        self.assertEqual((mismatch.unresolved_amount, mismatch.unresolved_currency),
                         ("15000.00", "LSL"))  # fmt: skip

        self.as_user("auditor")
        listed = store.list_findings(tenant_id=self.t1, case_id=case_id)
        self.assertEqual({f["finding_id"] for f in listed}, {v["finding_id"] for v in recorded})
        ref = listed[0]["evidence_refs"][0]
        self.assertEqual(ref["document_sha256"], self.sha[ref["evidence_id"]])

        again = self.record_signals(case_id)
        self.assertTrue(all(v["replayed"] for v in again))
        self.assertEqual(frappe.db.count(store.FINDING, {"case_id": case_id}), 3)

    def test_changed_body_under_the_same_key_conflicts(self):
        case_id = self.new_case()
        self.as_user("service")
        k = key()
        changed = copy.deepcopy(SIGNALS[1])
        store.record_rule_signal(
            tenant_id=self.t1, case_id=case_id, signal=SIGNALS[1], evidence=self.evidence,
            idempotency_key=k,
        )  # fmt: skip
        changed["explanation"] = "Purchases under a related-party contract were recorded."
        with self.assertRaises(IdempotencyConflict):
            store.record_rule_signal(
                tenant_id=self.t1, case_id=case_id, signal=changed, evidence=self.evidence,
                idempotency_key=k,
            )  # fmt: skip
        self.assertEqual(frappe.db.count(store.FINDING, {"case_id": case_id}), 1)

    def test_playbook_findings_store_provenance_and_revisions_are_unique(self):
        case_id = self.new_case()
        views = self.record_playbook(case_id)
        self.assertEqual(len(views), 5)
        self.as_user("supervisor")
        listed = store.list_findings(tenant_id=self.t1, case_id=case_id)
        self.assertEqual(len(listed), 5)
        for view in listed:
            self.assertEqual(view["analysis_provenance"], PROVENANCE)
            self.assertEqual(view["playbook"]["content_digest"], STAMP["content_digest"])
        self.assertTrue(all(v["replayed"] for v in self.record_playbook(case_id)))

        repo = store.FrappeRepository()
        stored = repo.get_finding(views[0]["finding_id"])
        assert stored is not None
        clash = engine.FindingRecord(
            **{**stored.__dict__, "finding_id": str(uuid.uuid4()), "registration_key": key()}
        )
        with self.assertRaises(Duplicate):
            repo.insert_finding(clash)  # same revision of the same lineage

    def test_findings_are_never_edited_or_deleted(self):
        case_id = self.new_case()
        (first, *_) = self.record_signals(case_id)
        frappe.set_user("Administrator")
        doc = frappe.get_doc(store.FINDING, first["finding_id"])
        doc.explanation = "Rewritten."
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.FINDING, first["finding_id"], ignore_permissions=True)

    def test_taxpayers_and_other_tenants_cannot_read_findings(self):
        case_id = self.new_case()
        (first, *_) = self.record_signals(case_id)
        self.as_user("taxpayer")
        with self.assertRaises(frappe.PermissionError):
            store.list_findings(tenant_id=self.t1, case_id=case_id)
        with self.assertRaises(frappe.PermissionError):
            store.get_finding(tenant_id=self.t1, case_id=case_id, finding_id=first["finding_id"])
        # No Vati Taxpayer DocPerm and no taxpayer_id column: listing is refused outright.
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list(store.FINDING, pluck="name")
        self.assertFalse(frappe.has_permission(store.FINDING, "read", doc=first["finding_id"]))

        self.as_user("auditor-t2")
        with self.assertRaises(frappe.PermissionError):  # TenantScopeDenied
            store.list_findings(tenant_id=self.t1, case_id=case_id)
        with self.assertRaises(CaseNotFound):
            store.list_findings(tenant_id=self.t2, case_id=case_id)
        with self.assertRaises(FindingNotFound):
            store.get_finding(
                tenant_id=self.t2, case_id=self.t2_case, finding_id=first["finding_id"]
            )
        self.assertFalse(frappe.has_permission(store.FINDING, "read", doc=first["finding_id"]))
        self.assertEqual(frappe.get_list(store.FINDING, filters={"case_id": case_id}), [])
        # The refused taxpayer read is kept in the TAX-059 ledger.
        frappe.set_user("Administrator")
        self.assertTrue(
            frappe.db.exists(
                "Vati Audit Event",
                {"tenant_id": self.t1, "actor": self.users["taxpayer"],
                 "action": "case.read.denied"},
            )
        )  # fmt: skip

    def test_feedback_is_append_only_linked_and_readable_by_the_feedback_loop(self):
        case_id = self.new_case()
        views = self.record_playbook(case_id)
        rule = self.record_signals(case_id)[0]
        finding_id = views[2]["finding_id"]
        user = self.as_user("auditor")
        body = {
            "verdict": "modified",
            "reason_code": "missing_context",
            "reason": "Delivery records may be kept by the group office.",
            "modified_explanation": "No delivery record for the group services is linked yet.",
            "recorded_by": self.users["supervisor"],
        }
        with self.assertRaises(InvalidMapping) as caught:
            store.record_finding_feedback(
                tenant_id=self.t1, case_id=case_id, finding_id=finding_id, body=body,
                idempotency_key=key(),
            )  # fmt: skip
        self.assertEqual(caught.exception.field_errors[0]["code"], "actor_not_allowed")
        del body["recorded_by"]
        k = key()
        first = store.record_finding_feedback(
            tenant_id=self.t1, case_id=case_id, finding_id=finding_id, body=body,
            idempotency_key=k,
        )  # fmt: skip
        self.assertEqual(first["recorded_by"], {"user": user, "role": "auditor"})
        replay = store.record_finding_feedback(
            tenant_id=self.t1, case_id=case_id, finding_id=finding_id, body=body,
            idempotency_key=k,
        )  # fmt: skip
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["feedback_id"], first["feedback_id"])
        with self.assertRaises(IdempotencyConflict):
            store.record_finding_feedback(
                tenant_id=self.t1, case_id=case_id, finding_id=finding_id,
                body={**body, "reason_code": "other"}, idempotency_key=k,
            )  # fmt: skip
        second = store.record_finding_feedback(
            tenant_id=self.t1, case_id=case_id, finding_id=finding_id,
            body={"verdict": "accepted", "reason_code": "evidence_supports", "reason": None,
                  "modified_explanation": None},
            idempotency_key=key(),
        )  # fmt: skip
        self.assertEqual(second["previous_feedback_id"], first["feedback_id"])
        with self.assertRaises(InvalidFinding):
            store.record_finding_feedback(
                tenant_id=self.t1, case_id=case_id, finding_id=rule["finding_id"],
                body={"verdict": "rejected", "reason_code": "other", "reason": "short",
                      "modified_explanation": None},
                idempotency_key=key(),
            )  # fmt: skip

        # The finding is unchanged; its summary counts this officer's latest verdict.
        finding = store.get_finding(tenant_id=self.t1, case_id=case_id, finding_id=finding_id)
        self.assertEqual(finding["explanation"], views[2]["explanation"])
        self.assertEqual(finding["feedback_summary"]["accepted"], 1)
        self.assertEqual(finding["feedback_summary"]["viewer_verdict"], "accepted")

        frappe.set_user("Administrator")
        doc = frappe.get_doc(store.FEEDBACK, first["feedback_id"])
        self.assertEqual(doc.changes_finding, 0)
        self.assertEqual(json.loads(doc.finding_snapshot)["producer"], "langgraph_run")
        doc.verdict = "rejected"
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.FEEDBACK, first["feedback_id"], ignore_permissions=True)

        for who in ("taxpayer", "service"):
            self.as_user(who)
            with self.assertRaises(frappe.PermissionError):
                store.record_finding_feedback(
                    tenant_id=self.t1, case_id=case_id, finding_id=finding_id,
                    body={"verdict": "accepted", "reason_code": "evidence_supports",
                          "reason": None, "modified_explanation": None},
                    idempotency_key=key(),
                )  # fmt: skip

        self.as_user("service")
        report = store.feedback_for_review(tenant_id=self.t1)
        mine = [i for i in report["items"] if i["case_id"] == case_id]
        self.assertEqual([i["current"] for i in mine], [False, True])
        self.assertEqual(mine[0]["provenance"][0]["kind"], "playbook")
        self.assertEqual(mine[0]["provenance"][0]["content_digest"], STAMP["content_digest"])
        self.as_user("taxpayer")
        with self.assertRaises(frappe.PermissionError):
            store.feedback_for_review(tenant_id=self.t1)
