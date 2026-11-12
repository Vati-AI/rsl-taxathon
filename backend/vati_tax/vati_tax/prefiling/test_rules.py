"""Frappe-free tests for check before you file (TAX-129, ADR-051).

The synthetic TAX-118 taxpayers are read through the TAX-111 fixture adapter, exactly as
the site reads them, and reconciled with the real TAX-048 module.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from vati_tax.prefiling import rules

REPO = pathlib.Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))
try:
    from ai.evaluation.assurance_pipeline import build_sources
    from ai.evaluation.assurance_population import generate
    from ai.tools.refund_reconciliation import reconcile_refund
except ImportError:  # pragma: no cover - ai/ is not on the path
    generate = None  # type: ignore[assignment]

PARAMS = {
    "n_taxpayers": 12,
    "n_dev": 4,
    "issue_counts": {"fake_invoices": 2, "untaxed_imported_services": 1,
                     "disguised_employment": 1, "related_party_loss": 1, "policy_gap": 1,
                     "mixed": 0},
}  # fmt: skip
TODAY = date(2026, 11, 12)
# Words that would make the result a decision or a score.
FORBIDDEN = ("score", "risk", "lane", "eligible", "approved", "rejected", "fraud", "penalty",
             "blocked", "suspicious")  # fmt: skip
OK = {"etax": "ok", "idms": "ok", "asycuda": "ok"}
RETURN = {"return_id": "VAT-1", "period": "2026-09", "output_vat": "100.00",
          "input_vat_claimed": "400.00", "refund_claim": "300.00", "filed_on": "2026-10-19"}  # fmt: skip


def invoice(n: int, vat: str, declared: bool = True, day: str = "2026-09-10") -> dict[str, Any]:
    return {"idms_id": f"I{n}", "invoice_number": f"INV-{n}", "supplier_tin": f"51000001{n}",
            "buyer_tin": "510000006", "issue_date": day, "net_amount": "1000.00",
            "vat_amount": vat, "supplier_output_declared": declared}  # fmt: skip


def run(
    vat_return: dict[str, Any] | None,
    invoices: list[dict[str, Any]],
    entries: list[dict[str, Any]] | None = None,
    statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    recon = (
        reconcile_refund(rules.build_record(vat_return, invoices, entries or []))
        if vat_return
        else None
    )
    return rules.result(period="2026-09", statuses=statuses or OK, vat_return=vat_return,
                        invoices=invoices, entries=entries or [], reconciliation=recon,
                        file_url="https://refunds.example.test/")  # fmt: skip


def assert_guidance_only(case: unittest.TestCase, view: dict[str, Any]) -> None:
    case.assertTrue(view["guidance_only"])
    case.assertEqual(view["file_with"]["name"], "Online Refunds Application")
    case.assertIn(view["status"], {rules.NO_ISSUES, rules.CHECK_THESE, rules.INCOMPLETE})
    text = json.dumps({k: v for k, v in view.items() if k != "note"}).lower()
    for word in FORBIDDEN:
        case.assertNotIn(word, text)


@unittest.skipIf(generate is None, "ai/ is not on the path")
class SyntheticTaxpayerTests(unittest.TestCase):
    def test_every_period_reads_rsl_data_and_gives_guidance_only(self) -> None:
        world, key = generate(13, PARAMS)
        sources = build_sources(world)
        tenant = str(world["tenant_id"])
        flagged: dict[str, set[str]] = {}
        checked = 0
        for tin in sorted(world["submissions"]):
            read = {s: sources.read(s, k, tenant_id=tenant, taxpayer=tin)["records"]
                    for s, k in (("etax", "vat_return"), ("idms", "invoice"),
                                 ("asycuda", "entry"))}  # fmt: skip
            for period in sorted({r["period"] for r in read["etax"]}):
                vat_return, invoices, entries = rules.select(
                    period, read["etax"], read["idms"], read["asycuda"]
                )
                assert vat_return is not None
                recon = reconcile_refund(rules.build_record(vat_return, invoices, entries))
                view = rules.result(period=period, statuses=OK, vat_return=vat_return,
                                    invoices=invoices, entries=entries, reconciliation=recon,
                                    file_url=None)  # fmt: skip
                assert_guidance_only(self, view)
                # The figures are RSL's own, not typed by the taxpayer.
                self.assertEqual(Decimal(view["figures"]["lekuka_input_vat"]),
                                 sum((Decimal(i["vat_amount"]) for i in invoices),
                                     Decimal("0")))  # fmt: skip
                self.assertEqual(view["figures"]["input_vat_claimed"],
                                 f"{Decimal(vat_return['input_vat_claimed']):.2f}")  # fmt: skip
                if view["status"] == rules.CHECK_THESE:
                    flagged.setdefault(tin, set()).add(period)
                checked += 1
        self.assertGreater(checked, 12)
        fake = [t for t, v in key["taxpayers"].items() if "fake_invoices" in v["issues"]]
        self.assertTrue(fake)
        for tin in fake:
            self.assertIn(tin, flagged, "a taxpayer with fake invoices gets guidance")


@unittest.skipIf(generate is None, "ai/ is not on the path")
class ResultTests(unittest.TestCase):
    def test_matching_return_and_lekuka_has_no_issues(self) -> None:
        view = run(RETURN, [invoice(1, "300.00"), invoice(2, "100.00")])
        self.assertEqual(view["status"], rules.NO_ISSUES)
        self.assertEqual(view["prompts"], [])
        self.assertEqual(view["figures"]["lekuka_input_vat"], "400.00")
        self.assertEqual(view["file_with"]["url"], "https://refunds.example.test/")
        assert_guidance_only(self, view)

    def test_more_claimed_than_rsl_holds_and_undeclared_supplier(self) -> None:
        view = run(RETURN, [invoice(1, "100.00", declared=False)])
        codes = [p["code"] for p in view["prompts"]]
        self.assertIn("input_vat_above_rsl_records", codes)
        self.assertIn("supplier_not_declared", codes)
        self.assertEqual(view["status"], rules.CHECK_THESE)
        self.assertIn("LSL 300.00", view["prompts"][codes.index(
            "input_vat_above_rsl_records")]["message"])  # fmt: skip
        assert_guidance_only(self, view)

    def test_arithmetic_and_customs_entries(self) -> None:
        bad = {**RETURN, "refund_claim": "350.00"}
        entry = {"entry_id": "E1", "importer_tin": "510000006", "registered_on": "2026-09-03",
                 "origin_country": "za", "customs_value": "2000.00", "import_vat": "300.00",
                 "currency": "LSL"}  # fmt: skip
        view = run(bad, [invoice(1, "100.00")], [entry])
        self.assertIn("return_arithmetic_mismatch", [p["code"] for p in view["prompts"]])
        self.assertEqual(view["figures"]["asycuda_import_vat"], "300.00")

    def test_missing_sources_are_never_reported_as_clean(self) -> None:
        down = run(RETURN, [], statuses={"etax": "ok", "idms": "unavailable", "asycuda": "ok"})
        self.assertEqual(down["status"], rules.INCOMPLETE)
        self.assertIn("source_not_checked", [p["code"] for p in down["prompts"]])
        self.assertIsNone(down["figures"]["lekuka_input_vat"])
        none = run(None, [invoice(1, "100.00")])
        self.assertEqual(none["status"], rules.INCOMPLETE)
        self.assertEqual(none["prompts"][0]["code"], "no_vat_return")
        self.assertIsNone(none["figures"]["refund_claimed"])

    def test_period_selection_and_validation(self) -> None:
        later = {**RETURN, "return_id": "VAT-2", "filed_on": "2026-10-25"}
        chosen, invoices, _ = rules.select(
            "2026-09", [RETURN, later, {**RETURN, "period": "2026-08"}],
            [invoice(1, "1.00"), invoice(2, "1.00", day="2026-08-30")], [],
        )  # fmt: skip
        self.assertEqual(chosen["return_id"] if chosen else None, "VAT-2")
        self.assertEqual([i["idms_id"] for i in invoices], ["I1"])
        self.assertEqual(rules.check_period("2026-11", TODAY), "2026-11")
        for bad in ("2026-12", "2026-13", "26-09", "", None):
            with self.subTest(period=bad):
                with self.assertRaises(rules.InvalidPeriod):
                    rules.check_period(bad, TODAY)


if __name__ == "__main__":
    unittest.main()
