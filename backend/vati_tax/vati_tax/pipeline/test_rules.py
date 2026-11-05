"""Frappe-free tests for the TAX-087 pipeline rules, scenarios and service fakes.

The bench tests (test_pipeline_bench.py) run the same flow through Frappe and the
outbox; tests/integration/test_pipeline_wiring.py runs the real adapters on these
rules' output.
"""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path
from typing import Any

from vati_tax.ids.canonical import InvalidMapping
from vati_tax.integrations.config import ServiceNotConfigured
from vati_tax.outbox import policy as outbox_policy
from vati_tax.pipeline import fakes, rules, scenarios

TENANT = "10000000-0000-4000-8000-000000000087"
CASE = "60000000-0000-4000-8000-000000000087"
EVIDENCE = "70000000-0000-4000-8000-000000000001"
CONTRACT = "70000000-0000-4000-8000-000000000002"
EXTRACTION = "80000000-0000-4000-8000-000000000001"
SHA = "a" * 64
SHA2 = "b" * 64
RECORDS = Path(__file__).resolve().parents[4] / "data" / "synthetic" / "tax024_records.json"


class Upstream(Exception):
    def __init__(self, code: str, reason: str | None, retryable: bool) -> None:
        super().__init__("upstream text that must not be stored")
        self.code, self.reason, self.retryable = code, reason, retryable


def taxpayer() -> dict[str, str]:
    return {"name": "90000000-0000-4000-8000-000000000001", "display_name": "Synthetic taxpayer",
            "taxpayer_type": "company", "tin": "400000001"}  # fmt: skip


def rows() -> list[dict[str, object]]:
    return [
        {"name": "row1abc", "idx": 1, "invoice_number": "INV-1", "invoice_date": "2025-12-01",
         "supplier_name": "Synthetic Group Supplies", "supplier_tin": None,
         "net_amount": 80000.0, "vat_amount": 7200.0, "gross_amount": 87200.0,
         "amount_paid": 87200.0, "invoice_evidence_id": EVIDENCE,
         "contract_evidence_id": CONTRACT},
        {"name": "row2abc", "idx": 2, "invoice_number": "INV-2", "invoice_date": "2025-12-02",
         "supplier_name": "Synthetic Group Supplies", "supplier_tin": None,
         "net_amount": "31111.11", "vat_amount": "2800", "gross_amount": "33911.11",
         "amount_paid": None, "invoice_evidence_id": EVIDENCE, "contract_evidence_id": None},
    ]  # fmt: skip


def evidence() -> list[dict[str, object]]:
    return [
        {"evidence_id": EVIDENCE, "document_sha256": SHA, "media_type": "text/csv",
         "size_bytes": 120, "original_filename": "INV-1.csv", "document_type": "invoice",
         "received_at": "2026-11-05 10:00:00"},
        {"evidence_id": CONTRACT, "document_sha256": SHA2, "media_type": "text/csv",
         "size_bytes": 80, "original_filename": "CTR-1.csv", "document_type": "contract",
         "received_at": "2026-11-05 10:00:01"},
    ]  # fmt: skip


class EventTests(unittest.TestCase):
    def test_evidence_event_is_stable_and_round_trips(self):
        key, payload = rules.evidence_event(CASE, EVIDENCE, SHA)
        self.assertEqual((key, payload), rules.evidence_event(CASE, EVIDENCE, SHA))
        self.assertEqual(rules.parse_evidence_event(payload), (EVIDENCE, SHA))
        # A different document or case is a different event; keys fit the outbox rule.
        self.assertNotEqual(key, rules.evidence_event(CASE, EVIDENCE, SHA2)[0])
        self.assertRegex(key, r"^[A-Za-z0-9:._-]{16,255}$")
        outbox_policy.Envelope.build(
            tenant_id=TENANT, case_id=CASE, type=rules.EVIDENCE_RECEIVED, payload=payload,
            idempotency_key=key, correlation_id=str(uuid.uuid4()), source_service="frappe",
        )  # fmt: skip

    def test_bad_payloads_are_refused(self):
        for payload in (
            {"evidence_id": EVIDENCE},
            {"evidence_id": "not-a-uuid", "document_sha256": SHA},
            {"evidence_id": EVIDENCE, "document_sha256": "ABC"},
            {"evidence_id": EVIDENCE, "document_sha256": SHA, "extra": "x"},
        ):
            with self.assertRaises(InvalidMapping):
                rules.parse_evidence_event(payload)
        with self.assertRaises(InvalidMapping):
            rules.parse_settled_event({"evidence_id": EVIDENCE, "extraction_id": EXTRACTION,
                                       "status": "done"})  # fmt: skip

    def test_settled_event_type_follows_status(self):
        for status, expected in (("success", rules.EXTRACTION_COMPLETED),
                                 ("partial", rules.EXTRACTION_COMPLETED),
                                 ("failure", rules.EXTRACTION_FAILED)):  # fmt: skip
            settled = rules.Settled(EVIDENCE, EXTRACTION, status)
            event_type, key, payload = rules.settled_event(CASE, settled)
            self.assertEqual(event_type, expected)
            self.assertEqual(rules.parse_settled_event(payload), settled)
            self.assertEqual(key, rules.settled_event(CASE, settled)[1])
            self.assertIn(event_type, outbox_policy.EVENT_TYPES)

    def test_claim_evidence_ids_dedupes_and_skips_malformed(self):
        ids = rules.claim_evidence_ids([CONTRACT, "bogus", 7], rows())
        self.assertEqual(ids, [CONTRACT, EVIDENCE])


class ClassificationTests(unittest.TestCase):
    def test_retryable_and_permanent_keep_only_a_code(self):
        retry = rules.classify("docling", Upstream("upstream_unavailable", "upstream_status", True))
        self.assertIsInstance(retry, outbox_policy.RetryableDeliveryError)
        self.assertEqual(retry.code, "docling_upstream_status")
        stop = rules.classify("openaleph", Upstream("invalid_request", None, False))
        self.assertIsInstance(stop, outbox_policy.PermanentDeliveryError)
        self.assertEqual(stop.code, "openaleph_invalid_request")
        self.assertNotIn("upstream text", stop.code)

    def test_unconfigured_service_retries_with_its_reason(self):
        failure = rules.classify("docling", ServiceNotConfigured("docling"))
        self.assertIsInstance(failure, outbox_policy.RetryableDeliveryError)
        self.assertEqual(failure.code, "docling_not_configured")
        stored = outbox_policy.classify(failure)
        self.assertTrue(stored.retryable)
        missing = rules.classify("yente", ServiceNotConfigured("yente", "adapter_not_installed"))
        self.assertEqual(missing.code, "yente_adapter_not_installed")

    def test_delivery_errors_pass_through(self):
        error = outbox_policy.PermanentDeliveryError("case_not_found")
        self.assertIs(rules.classify("docling", error), error)


class BundleTests(unittest.TestCase):
    def bundle(self) -> dict[str, Any]:
        return rules.claim_bundle(tenant_id=TENANT, taxpayer=taxpayer(), currency="LSL",
                                  rows=rows(), evidence=evidence())  # fmt: skip

    def test_money_is_exact_two_decimals(self):
        self.assertEqual(rules.money(7200.0), "7200.00")
        self.assertEqual(rules.money("66666.67"), "66666.67")
        self.assertEqual(rules.money(0.1 + 0.2), "0.30")
        with self.assertRaises(InvalidMapping):
            rules.money("abc")

    def test_bundle_parties_invoices_payments_and_citations(self):
        bundle = self.bundle()
        self.assertEqual(bundle["version"], rules.BUNDLE_VERSION)
        parties = {p["role"]: p for p in bundle["parties"]}
        self.assertEqual(len(bundle["parties"]), 2)  # one supplier for both rows
        self.assertEqual(parties["taxpayer"]["tax_number"], "400000001")
        self.assertEqual(parties["taxpayer"]["kind"], "company")
        first, second = bundle["invoices"]
        self.assertEqual((first["net_amount"], first["vat_amount"]), ("80000.00", "7200.00"))
        self.assertEqual(second["vat_amount"], "2800.00")
        # Invoice and contract documents are both cited, by SHA-256.
        self.assertEqual([c["document_sha256"] for c in first["evidence"]], [SHA, SHA2])
        self.assertEqual([c["evidence_id"] for c in second["evidence"]], [EVIDENCE])
        self.assertEqual(len(bundle["payments"]), 1)  # only the paid row
        self.assertEqual(
            [e["evidence_id"] for e in bundle["evidence"]], sorted([EVIDENCE, CONTRACT])
        )
        json.dumps(bundle)

    def test_bundle_is_deterministic_and_cites_only_known_documents(self):
        self.assertEqual(self.bundle(), self.bundle())
        only_contract = rules.claim_bundle(tenant_id=TENANT, taxpayer=taxpayer(), currency="LSL",
                                           rows=rows(), evidence=evidence()[1:])  # fmt: skip
        self.assertEqual(
            [c["evidence_id"] for c in only_contract["invoices"][0]["evidence"]], [CONTRACT]
        )

    def test_ids_fit_the_bundle_record_rule(self):
        self.assertEqual(rules.record_id("abc 12/3"), "abc-12/3")
        self.assertTrue(rules.record_id("-x")[0].isalnum())
        self.assertEqual(rules.supplier_id(" Acme ", None), rules.supplier_id("acme", ""))
        self.assertNotEqual(rules.supplier_id("Acme", "1"), rules.supplier_id("Acme", "2"))


class LedgerTests(unittest.TestCase):
    def test_index_plan_never_resends_changed_entities(self):
        entities = [{"id": "e1", "schema": "Company"}, {"id": "e2", "schema": "Document"},
                    {"id": "e3", "schema": "UnknownLink"}]  # fmt: skip
        digests = {"e1": "d1", "e2": "d2", "e3": "d3"}
        plan = rules.index_plan(entities, digests, {"e1": "d1", "e2": "old"})
        self.assertEqual([e["id"] for e in plan.new], ["e3"])
        self.assertEqual(plan.unchanged, ["e1"])
        self.assertEqual(plan.changed, ["e2"])
        self.assertEqual(rules.screening_targets(entities), [entities[0]])
        self.assertEqual(rules.document_entities(entities), ["e2"])

    def test_keys_are_scoped(self):
        self.assertNotEqual(rules.index_key(TENANT, 1, "e1"), rules.index_key(TENANT, 2, "e1"))
        a = rules.screening_key(TENANT, CASE, "e1", "ds", "v1")
        self.assertNotEqual(a, rules.screening_key(TENANT, CASE, "e1", "ds", "v2"))
        self.assertEqual(len(a), 64)


class ScenarioTests(unittest.TestCase):
    @unittest.skipUnless(RECORDS.is_file(), "TAX-024 records are not in this checkout")
    def test_scenarios_match_tax024_records(self):
        records = {r["case_id"]: r for r in json.loads(RECORDS.read_text(encoding="utf-8"))}
        for scenario in scenarios.SCENARIOS:
            record = records[scenario["case"]]
            self.assertTrue(record["synthetic"])
            self.assertEqual(scenario["taxpayer"], record["taxpayer_id"])
            self.assertEqual(scenario["refund_claim"], record["return"]["refund_claim"])
            self.assertEqual(scenario["input_vat_claimed"], record["return"]["input_vat_claimed"])
            self.assertEqual(scenario["output_vat"], record["return"]["output_vat"])
            self.assertEqual(scenario["contracts"],
                             {c["id"]: c["counterparty_id"] for c in record["contracts"]})  # fmt: skip
            paid = {p["invoice_id"]: p["amount"] for p in record["payments"]}
            expected = tuple(
                (i["id"], i["contract_id"], i["counterparty_id"], i["net_amount"],
                 i["vat_amount"], i["gross_amount"], paid[i["id"]])
                for i in record["invoices"]
            )  # fmt: skip
            self.assertEqual(scenario["invoices"], expected)

    def test_documents_are_marked_synthetic(self):
        invoice = scenarios.SCENARIOS[0]["invoices"][0]
        text = scenarios.invoice_document(invoice).decode()
        self.assertTrue(text.startswith("synthetic,true\n"))
        self.assertIn("INV-SYN-001-01", text)


class FakeTests(unittest.TestCase):
    def test_fake_docling_routes(self):
        docling = fakes.FakeDocling()
        task = docling("POST", "/v1/convert/file/async", b"abc", "multipart/form-data").body
        task_id = task["task_id"]
        self.assertEqual(docling("GET", f"/v1/status/poll/{task_id}?wait=5").body["task_status"],
                         "success")  # fmt: skip
        self.assertEqual(docling("GET", f"/v1/result/{task_id}").body["status"], "success")
        docling.lose_tasks = True
        self.assertEqual(docling("GET", f"/v1/result/{task_id}").status, 404)
        docling.down = True
        self.assertEqual(docling("POST", "/v1/convert/file/async", b"", None).status, 503)

    def test_fake_yente_proposes_only_for_group_names(self):
        yente = fakes.FakeYente()
        path = f"/match/{fakes.FAKE_DATASET}?threshold=0.7000&limit=5"

        def results(name: str) -> list[object]:
            body = {"queries": {"q": {"schema": "Company", "properties": {"name": [name]}}}}
            return json.loads(yente("POST", path, body).body)["responses"]["q"]["results"]

        self.assertEqual(len(results("Synthetic counterparty SYN-GROUP-002")), 1)
        self.assertEqual(results("Synthetic counterparty SYN-SUPPLIER-002"), [])


if __name__ == "__main__":
    unittest.main()
