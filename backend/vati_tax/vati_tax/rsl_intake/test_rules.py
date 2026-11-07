"""Frappe-free tests for the RSL claim intake rules (TAX-122).

The envelopes have the shape the TAX-111 fixture adapter returns. The bench tests
(test_rsl_intake_bench.py) cover the Frappe path.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from typing import Any

from vati_tax.rsl_intake import rules

TIN = "100000001"


def digest(record: dict[str, Any]) -> str:
    text = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def claim(claim_id: str = "ORA-2026-0001", amount: str = "125000.50", **extra: Any) -> dict:
    record = {
        "claim_id": claim_id,
        "tin": TIN,
        "period": "2026-08",
        "return_id": "VAT-2026-08-1",
        "claimed_amount": amount,
        "submitted_on": "2026-09-20",
        "lines": [
            {
                "line_id": "L1",
                "kind": "input_vat",
                "supplier_ref": "S1",
                "invoice_number": "INV-1",
                "invoice_date": "2026-08-03",
                "net_amount": "1000.00",
                "vat_amount": "150.00",
            }
        ],  # fmt: skip
        "uploaded_documents": [{"document_id": "D1", "line_id": "L1", "kind": "invoice"}],
    }
    record.update(extra)
    return record


def envelope(*records: dict[str, Any], status: str = "ok", **extra: Any) -> dict[str, Any]:
    env = {
        "fixture": True,
        "adapter_id": "vati.rsl_sources.fixture",
        "adapter_version": "tax111-fixture-v1",
        "source_system": "online_refunds",
        "kind": "claim",
        "contract_id": "rsl.online_refunds.claim.v0-assumed",
        "tenant_id": "rsl-fixture",
        "taxpayer": TIN,
        "retrieved_at": "2026-11-07T10:00:00Z",
        "status": status,
        "records": list(records) if status == "ok" else [],
        "references": [
            {"source_system": "online_refunds", "record_id": r["claim_id"],
             "record_sha256": digest(r)}
            for r in records
        ] if status == "ok" else [],
    }  # fmt: skip
    env.update(extra)
    return env


class PlanTests(unittest.TestCase):
    def test_a_claim_becomes_one_reference_with_its_hash_and_no_figures_copied(self) -> None:
        record = claim()
        status, claims = rules.plan_claims(envelope(record), tin=TIN)
        self.assertEqual(status, "ok")
        [item] = claims
        self.assertEqual(item.claim_ref, "ORA-2026-0001")
        self.assertEqual(item.period, "2026-08")
        self.assertEqual(item.claimed_amount, "125000.50")
        self.assertEqual(item.record_sha256, digest(record))
        self.assertEqual(item.idempotency_key, "rsl:online-refunds:ORA-2026-0001")
        self.assertTrue(item.fixture)
        self.assertIsNone(item.refused)
        refs = item.source_refs
        self.assertEqual(refs["record_id"], "ORA-2026-0001")
        self.assertEqual(refs["contract_id"], "rsl.online_refunds.claim.v0-assumed")
        self.assertEqual(refs["lines"], [{"line_id": "L1", "kind": "input_vat"}])
        self.assertEqual(refs["documents"][0]["held_by"], "RSL")
        # References, not copies: no line amounts, invoice numbers or supplier refs.
        text = json.dumps(refs)
        for copied in ("1000.00", "150.00", "INV-1", "S1"):
            self.assertNotIn(copied, text)

    def test_the_idempotency_key_fits_the_submission_key_rule(self) -> None:
        from vati_tax.submissions import domain

        self.assertEqual(domain.validate_idempotency_key(rules.idempotency_key("C1")),
                         "rsl:online-refunds:C1")  # fmt: skip

    def test_rsl_not_answering_is_unavailable_never_no_claims(self) -> None:
        self.assertEqual(rules.plan_claims(envelope(status="unavailable"), tin=TIN),
                         ("unavailable", []))  # fmt: skip
        self.assertEqual(rules.run_status(["unavailable", "unavailable"]), "unavailable")
        self.assertEqual(rules.run_status(["ok", "unavailable"]), "partial")
        self.assertEqual(rules.run_status(["ok", "ok"]), "completed")
        self.assertEqual(rules.run_status([]), "completed")

    def test_a_zero_claim_is_refused_not_recorded(self) -> None:
        _, [item] = rules.plan_claims(envelope(claim(amount="0")), tin=TIN)
        self.assertEqual(item.refused, "claimed_amount_not_positive")

    def test_reads_that_break_the_contract_are_refused(self) -> None:
        cases = {
            "wrong_source": envelope(claim(), source_system="etax"),
            "wrong_contract": envelope(claim(), contract_id="rsl.etax.vat_return.v0-assumed"),
            "unknown_status": {**envelope(claim()), "status": "maybe"},
        }
        for reason, env in cases.items():
            with self.subTest(reason=reason):
                with self.assertRaises(rules.InvalidIntake) as ctx:
                    rules.plan_claims(env, tin=TIN)
                self.assertEqual(ctx.exception.reason, reason)
        with self.assertRaises(rules.InvalidIntake) as ctx:
            rules.plan_claims(envelope(claim()), tin="100000002")
        self.assertEqual(ctx.exception.reason, "wrong_taxpayer")
        other = envelope(claim(tin="100000002"))
        with self.assertRaises(rules.InvalidIntake) as ctx:
            rules.plan_claims(other, tin=TIN)
        self.assertEqual(ctx.exception.reason, "wrong_taxpayer")
        no_digest = envelope(claim())
        no_digest["references"] = []
        with self.assertRaises(rules.InvalidIntake) as ctx:
            rules.plan_claims(no_digest, tin=TIN)
        self.assertEqual(ctx.exception.reason, "missing_reference")


class ReplayTests(unittest.TestCase):
    def test_same_record_replays_and_a_changed_one_conflicts(self) -> None:
        _, [item] = rules.plan_claims(envelope(claim()), tin=TIN)
        self.assertEqual(rules.replay_outcome(item.record_sha256, item), "replayed")
        _, [changed] = rules.plan_claims(envelope(claim(amount="1.00")), tin=TIN)
        self.assertEqual(rules.replay_outcome(item.record_sha256, changed), "conflict")


if __name__ == "__main__":
    unittest.main()
