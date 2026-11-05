"""Bench tests for the TAX-087 event wiring (ADR-036).

Run on a disposable site: ``python3 scripts/frappe_dev.py verify``. Jube is replaced
at the port level and Docling, OpenAleph and yente at the transport level by
``vati_tax.pipeline.fakes`` (the real adapters still run). Covers the task's
verification: sending SYN-001 and SYN-002 through the portal path produces a Jube
case, extraction records and indexed entities with no manual step; a replay does no
second outward call; an unavailable or unconfigured service retries, dead-letters,
shows up in the operator view and recovers after an operator retry.
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.evidence import store as evidence_store
    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.ids.canonical import CaseNotFound
    from vati_tax.outbox import policy as outbox_policy
    from vati_tax.outbox import store as outbox_store
    from vati_tax.outbox import worker
    from vati_tax.pipeline import api, demo, scenarios, store
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

RUN = uuid.uuid4().hex[:8]
FAST = outbox_policy.RetryPolicy(
    max_attempts=5, base_delay=timedelta(seconds=10), max_delay=timedelta(seconds=20)
)


class PipelineBenchTests(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()
        self.run_id = uuid.uuid4().hex[:8]
        # A fresh tenant per test: the outbox worker commits, so tenants keep tests apart.
        self.tenant = ensure_tenant(str(uuid.uuid4()), "Synthetic TAX-087 tenant")
        self.service = ensure_service_identity(f"tax087-svc-{self.run_id}@example.test",
                                               [self.tenant])  # fmt: skip
        frappe.db.commit()
        self.services = demo.Services(live=False)
        self.services.install(self.service)
        self.clock = datetime.now(UTC)

    def tearDown(self):
        frappe.set_user("Administrator")
        demo.Services.uninstall()
        frappe.db.rollback()

    # --- helpers ---------------------------------------------------------------------

    def send(self, case: str = "SYN-002") -> dict[str, Any]:
        scenario = next(s for s in scenarios.SCENARIOS if s["case"] == case)
        return demo.send_scenario(self.tenant, scenario, f"{self.run_id}{case[-1]}")

    def dispatch(self, minutes: int = 0) -> dict[str, int]:
        self.clock += timedelta(minutes=minutes)
        return worker.dispatch(now=self.clock, tenant_id=self.tenant, retry=FAST,
                               rand=lambda: 1.0)  # fmt: skip

    def drain(self) -> None:
        while self.dispatch():
            pass

    def status(self, case_id: str) -> dict[str, Any]:
        return store.case_status(tenant_id=self.tenant, case_id=case_id)

    def events(self, case_id: str, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.status(case_id)["events"] if e["event_type"] == event_type]

    # --- end to end ------------------------------------------------------------------

    def test_syn_001_and_syn_002_reach_every_service_with_no_manual_step(self):
        sent = [self.send("SYN-001"), self.send("SYN-002")]
        self.drain()
        for item in sent:
            status = self.status(item["case_id"])
            self.assertEqual(status["failing"], [], item["scenario"])
            self.assertTrue(all(e["status"] == "delivered" for e in status["events"]))
            types = [e["event_type"] for e in status["events"]]
            documents = len(item["evidence_ids"])
            self.assertEqual(types.count("case.registered"), 1)
            self.assertEqual(types.count("evidence.received"), documents)
            self.assertEqual(types.count("extraction.completed"), documents)
            # One correlation ID traces the claim through every step.
            self.assertEqual(len({e["correlation_id"] for e in status["events"]}), 1)
            # Jube case (TAX-039), extraction records, indexed entities, screenings.
            links = {(link["service"], link["external_type"]) for link in status["links"]}
            self.assertIn(("jube", "case"), links)
            self.assertIn(("docling", "extraction"), links)
            self.assertIn(("docling", "conversion_job"), links)
            self.assertIn(("openaleph", "document"), links)
            self.assertEqual(sum(1 for link in status["links"]
                                 if link["service"] == "frappe"), documents)  # fmt: skip
            self.assertTrue(all(e["extraction"]["status"] == "success" for e in status["evidence"]))
            self.assertGreater(status["entities_indexed"], documents)
            self.assertEqual(status["reindex_required"], [])
            self.assertTrue(status["screenings"])
            self.assertTrue(all(s["dataset_version"] for s in status["screenings"]))
            # The extraction link carries the outbox event that made it.
            extraction_links = [link for link in status["links"]
                                if link["external_type"] == "extraction"]  # fmt: skip
            self.assertTrue(all(link["source_event_id"] for link in extraction_links))
        syn002 = self.status(sent[1]["case_id"])
        self.assertEqual(sum(int(s["candidate_count"]) for s in syn002["screenings"]), 1)
        candidates = json.loads(frappe.db.get_value(
            store.SCREENING, {"case_id": sent[1]["case_id"], "candidate_count": 1},
            "candidates_json"))  # fmt: skip
        self.assertEqual(candidates[0]["review_status"], "unreviewed")
        self.assertEqual(self.services.jube.invocations, 2)
        self.assertEqual(self.services.docling.submissions,
                         sum(len(i["evidence_ids"]) for i in sent))  # fmt: skip

    # --- replay ------------------------------------------------------------------------

    def test_replay_makes_no_second_outward_call(self):
        sent = self.send("SYN-001")
        self.drain()
        case_id = sent["case_id"]
        calls = self.services.calls()
        extractions = frappe.db.count(store.EXTRACTION, {"tenant_id": self.tenant})
        index_rows = frappe.db.count(store.INDEX, {"tenant_id": self.tenant})
        screenings = frappe.db.count(store.SCREENING, {"tenant_id": self.tenant})
        # Redeliver every pipeline event, as after a crash before the delivered mark.
        for event in self.status(case_id)["events"]:
            if event["event_type"] != "case.registered":
                frappe.db.set_value(outbox_store.EVENT, event["event_id"],
                                    {"status": "pending", "attempts": 0})  # fmt: skip
        frappe.db.commit()
        self.drain()
        self.assertEqual(self.status(case_id)["failing"], [])
        self.assertEqual(self.services.calls(), calls)
        self.assertEqual(frappe.db.count(store.EXTRACTION, {"tenant_id": self.tenant}), extractions)
        self.assertEqual(frappe.db.count(store.INDEX, {"tenant_id": self.tenant}), index_rows)
        self.assertEqual(frappe.db.count(store.SCREENING, {"tenant_id": self.tenant}), screenings)
        # Enqueueing a step again replays the stored event.
        evidence_id = sent["evidence_ids"][0]
        again = store.enqueue_evidence(self.tenant, case_id, evidence_id)
        assert again is not None
        self.assertTrue(again["replayed"])

    def test_docling_submissions_are_kept_in_the_effect_ledger(self):
        sent = self.send("SYN-001")
        self.dispatch()
        submissions = self.services.docling.submissions
        self.assertEqual(submissions, len(sent["evidence_ids"]))
        events = self.events(sent["case_id"], "evidence.received")
        self.assertTrue(all(e["status"] == "delivered" for e in events))
        # Each submission is in the effect ledger with its Docling task ID.
        effects = [e for e in self.status(sent["case_id"])["effects"]
                   if e["effect_name"] == store.DOCLING_SUBMIT]  # fmt: skip
        self.assertEqual(len(effects), submissions)
        self.assertTrue(all(e["state"] == "completed" and e["result_ref"] for e in effects))

    # --- unavailable services ----------------------------------------------------------

    def test_unavailable_docling_retries_dead_letters_and_recovers(self):
        self.services.docling.down = True
        sent = self.send("SYN-001")
        case_id = sent["case_id"]
        self.dispatch()
        first = self.events(case_id, "evidence.received")[0]
        self.assertEqual(first["status"], "retry")
        self.assertEqual(first["last_error_code"], "docling_upstream_status")
        # Docling refused the submission, so no effect row is left to block a retry.
        self.assertFalse([e for e in self.status(case_id)["effects"]
                          if e["effect_name"] == store.DOCLING_SUBMIT])  # fmt: skip
        for _ in range(6):
            self.dispatch(minutes=1)
        first = self.events(case_id, "evidence.received")[0]
        self.assertEqual(first["status"], "dead_letter")
        status = self.status(case_id)
        self.assertIn(first["event_id"], [e["event_id"] for e in status["failing"]])
        # Later steps of the case wait behind it (TAX-033 per-case order).
        self.assertEqual(self.events(case_id, "extraction.completed"), [])
        # Docling is back; an operator retries the dead letter.
        self.services.docling.down = False
        outbox_store.retry_dead_letter(tenant_id=self.tenant, event_id=first["event_id"],
                                       actor="Administrator")  # fmt: skip
        frappe.db.commit()
        for _ in range(8):
            self.dispatch(minutes=1)
        status = self.status(case_id)
        self.assertEqual(status["failing"], [])
        self.assertTrue(all(e["extraction"] for e in status["evidence"]))
        self.assertGreater(status["entities_indexed"], 0)

    def test_unavailable_openaleph_and_yente_retry_without_losing_extractions(self):
        self.services.openaleph.down = True
        sent = self.send("SYN-001")
        self.dispatch()
        settled = self.events(sent["case_id"], "extraction.completed")
        self.assertTrue(settled)
        self.assertEqual(settled[0]["last_error_code"], "openaleph_upstream_unavailable")
        self.assertEqual(frappe.db.count(store.INDEX, {"tenant_id": self.tenant}), 0)
        self.services.openaleph.down = False
        self.services.yente.down = True
        self.dispatch(minutes=1)
        settled = self.events(sent["case_id"], "extraction.completed")
        self.assertTrue(settled[0]["last_error_code"].startswith("yente_"))
        # What OpenAleph accepted stays recorded although screening failed afterwards.
        self.assertGreater(frappe.db.count(store.INDEX, {"tenant_id": self.tenant}), 0)
        self.services.yente.down = False
        for _ in range(4):
            self.dispatch(minutes=1)
        status = self.status(sent["case_id"])
        self.assertEqual(status["failing"], [])
        self.assertTrue(status["screenings"])

    def test_unconfigured_service_and_missing_identity_are_retryable_and_visible(self):
        store.set_services(service_user=None)  # no fakes, no service user
        sent = self.send("SYN-001")
        self.dispatch()
        event = self.events(sent["case_id"], "evidence.received")[0]
        self.assertEqual(event["status"], "retry")
        self.assertEqual(event["last_error_code"], "service_identity_not_configured")
        store.set_services(service_user=self.service)  # identity, but no Docling config
        self.dispatch(minutes=1)
        event = self.events(sent["case_id"], "evidence.received")[0]
        self.assertEqual(event["last_error_code"], "docling_not_configured")

    # --- entry points and operator view -------------------------------------------------

    def test_evidence_linked_to_a_case_later_is_extracted(self):
        sent = self.send("SYN-001")
        self.drain()
        auditor = f"tax087-auditor-{self.run_id}@example.test"
        frappe.get_doc({"doctype": "User", "email": auditor, "first_name": "Synthetic auditor",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(auditor, self.tenant, policy.AUDITOR)
        taxpayer = frappe.db.get_value("Vati Case Identity", sent["case_id"], "taxpayer_id")
        frappe.db.commit()
        frappe.set_user(auditor)
        clear_principal_cache()
        content = scenarios.contract_document("CTR-LATE", "SYN-SUPPLIER-001")
        late = evidence_store.register_upload(
            tenant_id=self.tenant, content=content, filename="late.csv",
            document_type="other", idempotency_key=f"tax087-late-{self.run_id}",
            taxpayer_id=taxpayer, case_id=sent["case_id"],
        )["evidence_id"]  # fmt: skip
        frappe.set_user("Administrator")
        frappe.db.commit()
        self.drain()
        status = self.status(sent["case_id"])
        self.assertIn(late, [e["evidence_id"] for e in status["evidence"]])
        self.assertTrue(
            next(e for e in status["evidence"] if e["evidence_id"] == late)["extraction"]
        )
        self.assertEqual(status["failing"], [])

    def test_operator_view_is_for_operators_and_tenant_scoped(self):
        sent = self.send("SYN-001")
        other = ensure_tenant(str(uuid.uuid4()), "Synthetic other tenant")
        frappe.db.commit()
        with self.assertRaises(CaseNotFound):
            store.case_status(tenant_id=other, case_id=sent["case_id"])
        user = f"tax087-plain-{self.run_id}@example.test"
        frappe.get_doc({"doctype": "User", "email": user, "first_name": "Synthetic plain",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)  # fmt: skip
        grant_membership(user, self.tenant, policy.SUPERVISOR)
        frappe.db.commit()
        frappe.set_user(user)
        with self.assertRaises(frappe.PermissionError):
            api.case_status(tenant_id=self.tenant, case_id=sent["case_id"])
        frappe.set_user("Administrator")
        view = api.case_status(tenant_id=self.tenant, case_id=sent["case_id"])
        self.assertEqual(view["case_id"], sent["case_id"])
        text = json.dumps(view, default=str)
        for word in ("synthetic,true", "net_amount", "Synthetic counterparty"):
            self.assertNotIn(word, text)  # IDs and codes only, no document content

    def test_ledgers_cannot_be_edited_or_deleted(self):
        self.send("SYN-001")
        self.drain()
        name = frappe.get_all(store.INDEX, filters={"tenant_id": self.tenant}, pluck="name")[0]
        doc = frappe.get_doc(store.INDEX, name)
        doc.entity_digest = "0" * 64
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.INDEX, name, ignore_permissions=True)
        screening = frappe.get_all(store.SCREENING, filters={"tenant_id": self.tenant},
                                   pluck="name")[0]  # fmt: skip
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc(store.SCREENING, screening).save(ignore_permissions=True)


if __name__ == "__main__":
    unittest.main()
