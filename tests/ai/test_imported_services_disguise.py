"""TAX-141 foreign service payments booked outside service accounts (synthetic, model-free)."""

import copy
import json
import unittest
from decimal import Decimal
from typing import Any

from ai.tools.foreign_payments import three_way_match
from ai.workflows.rule_register import load_rule_register
from integration.rsl_sources.contracts import (
    SourceContractError,
    contract_for,
    validate_record,
)

CODE = "foreign_service_payments_outside_service_accounts"
RATES = {"vat": Decimal("0.15"), "wht": Decimal("0.10"), "wht_treaty": Decimal("0.05")}
ACCUSATORY = ("fraud", "abuse", "evasion", "sham", "fake", "illegal", "penalt", "conceal")

# Synthetic: a South African parent (CMA, no Central Bank report) and a Mauritian
# consultant, each paid 100,000 a month for two months, with the fees booked to general
# operating expenses. Nothing is withheld or declared as imported-services VAT.
BASE: dict[str, Any] = {
    "tin": "710000141",
    "suppliers": [
        {"supplier_ref": "F-ZA-P", "country": "za", "resident": False, "bank_account_ref": "ZA-1"},
        {"supplier_ref": "F-MU-C", "country": "mu", "resident": False, "bank_account_ref": "MU-2"},
    ],
    "supplier_invoices": [
        {
            "entry_id": f"L-{ref}-{m}",
            "period": f"2025-1{m}",
            "supplier_ref": ref,
            "net": "100000.00",
            "category": "operating_expenses",
        }
        for ref in ("F-ZA-P", "F-MU-C")
        for m in (0, 1)
    ],
    "bank_payments": [
        {
            "provider_reference": f"FX-{acc}-{m}",
            "date": f"2025-1{m}-28",
            "direction": "outflow",
            "settled_amount": "100000.00",
            "counterparty_account_ref": acc,
            "counterparty_country": acc[:2].lower(),
            "narration": narration.format(m=m),
        }
        for acc, narration in (("ZA-1", "Management services 2025-1{m}"), ("MU-2", "Payment"))
        for m in (0, 1)
    ],
    "wht_lines": [],
    "vat_returns": [
        {"period": "2025-10", "imported_services_vat_declared": "0.00"},
        {"period": "2025-11", "imported_services_vat_declared": "0.00"},
    ],
    "residence_certificates": [],
    "central_bank_payments": [
        {
            "report_id": f"CB-MU-{m}",
            "resident_tin": "710000141",
            "value_date": f"2025-1{m}-26",
            "direction": "outgoing",
            "amount": "100000.00",
            "currency": "LSL",
            "counterparty_country": "mu",
            "purpose": "services",
        }
        for m in (0, 1)
    ],
}


def run(fx: dict[str, Any] = BASE) -> dict[str, Any]:
    return three_way_match(
        tin=fx["tin"],
        suppliers=fx["suppliers"],
        supplier_invoices=fx["supplier_invoices"],
        bank_payments=fx["bank_payments"],
        wht_lines=fx["wht_lines"],
        vat_returns=fx["vat_returns"],
        residence_certificates=fx["residence_certificates"],
        central_bank_payments=fx["central_bank_payments"],
        rates=RATES,
        materiality=Decimal("50000.00"),
    )


def outside(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["counterparty"]: f for f in result["flags"] if f["code"] == CODE}


def explanations(result: dict[str, Any], ref: str) -> list[str]:
    return next(r for r in result["rows"] if r["counterparty"] == ref)["explanations_tested"]


class OutsideServiceAccountsTests(unittest.TestCase):
    def test_bank_narration_raises_a_question_for_a_cma_payment(self) -> None:
        result = run()
        flag = outside(result)["F-ZA-P"]
        # 200,000 paid: 10% withholding + 15% imported-services VAT.
        self.assertEqual(flag["amount"]["amount"], "50000.00")
        self.assertIn("the bank narration", flag["reason"])
        self.assertIn("Ask the taxpayer what the payments were for", flag["reason"])
        self.assertEqual(
            explanations(result, "F-ZA-P"),
            [
                "central_bank_purpose_not_reported",
                "bank_narration_describes_services",
                "cma_payment_not_in_central_bank_data",
            ],
        )
        self.assertIn(
            {
                "code": "explain_foreign_service_payments",
                "counterparty": "F-ZA-P",
                "year": "window",
                "evidence_ids": flag["evidence_ids"],
            },
            result["questions"],
        )
        self.assertIsNone(result["decision"])

    def test_central_bank_purpose_alone_raises_a_question(self) -> None:
        result = run()
        flag = outside(result)["F-MU-C"]
        self.assertIn("the Central Bank report", flag["reason"])
        self.assertNotIn("the bank narration", flag["reason"])
        self.assertIn("cb:CB-MU-0", flag["evidence_ids"])
        self.assertIn("bank:FX-MU-2-0", flag["evidence_ids"])
        self.assertIn("ledger:L-F-MU-C-0", flag["evidence_ids"])
        self.assertEqual(
            explanations(result, "F-MU-C"),
            ["central_bank_purpose_services", "bank_narration_not_services"],
        )

    def test_no_description_of_services_means_no_question(self) -> None:
        fx = copy.deepcopy(BASE)
        for report in fx["central_bank_payments"]:
            report["purpose"] = "goods"
        result = run(fx)
        self.assertNotIn("F-MU-C", outside(result))
        self.assertEqual(
            explanations(result, "F-MU-C"),
            ["central_bank_purpose_other", "bank_narration_not_services"],
        )
        # A report without a purpose, or one that does not match a payment, says nothing.
        for report in fx["central_bank_payments"]:
            del report["purpose"]
        self.assertNotIn("F-MU-C", outside(run(fx)))
        for report in fx["central_bank_payments"]:
            report["purpose"] = "services"
            report["value_date"] = "2025-09-01"
        result = run(fx)
        self.assertNotIn("F-MU-C", outside(result))
        self.assertEqual(explanations(result, "F-MU-C")[0], "central_bank_purpose_not_reported")

    def test_goods_loans_and_exempt_payees_are_not_asked(self) -> None:
        fx = copy.deepcopy(BASE)
        for p in fx["bank_payments"]:
            if p["counterparty_account_ref"] == "ZA-1":
                p["narration"] = "Purchase of stock"
        self.assertNotIn("F-ZA-P", outside(run(fx)))
        fx = copy.deepcopy(BASE)
        fx["suppliers"][0]["service_exempt"] = True
        self.assertNotIn("F-ZA-P", outside(run(fx)))
        # Imported goods carry the IMPORT tax code and are evidenced by customs entries.
        fx = copy.deepcopy(BASE)
        for row in fx["supplier_invoices"]:
            row["tax_code"] = "IMPORT"
        flag = outside(run(fx))["F-ZA-P"]
        self.assertFalse([e for e in flag["evidence_ids"] if e.startswith("ledger:")])

    def test_service_bookings_keep_the_existing_match(self) -> None:
        fx = copy.deepcopy(BASE)
        for row in fx["supplier_invoices"]:
            row["category"] = "management_fees"
        result = run(fx)
        self.assertEqual(outside(result), {})
        self.assertIn("wht_declared_below_expected", {f["code"] for f in result["flags"]})

    def test_declared_withholding_and_vat_reduce_the_question(self) -> None:
        fx = copy.deepcopy(BASE)
        fx["wht_lines"] = [
            {
                "line_id": "W-ZA-0",
                "payee_ref": "F-ZA-P",
                "period": "2025-10",
                "payment_type": "service_fee",
                "gross_amount": "200000.00",
                "wht_amount": "20000.00",
                "treaty_relief_claimed": False,
            }
        ]
        fx["central_bank_payments"] = []  # only the parent is asked, so it takes any surplus
        flag = outside(run(fx))["F-ZA-P"]
        self.assertEqual(flag["amount"]["amount"], "30000.00")  # VAT only
        self.assertIn("withholding_declared_for_payee", explanations(run(fx), "F-ZA-P"))
        # VAT declared beyond booked services covers part of it, then all of it.
        fx["vat_returns"][0]["imported_services_vat_declared"] = "10000.00"
        flag = outside(run(fx))["F-ZA-P"]
        self.assertEqual(flag["amount"]["amount"], "20000.00")
        self.assertEqual(flag["vat_declared_beyond_booked_services"]["amount"], "10000.00")
        fx["vat_returns"][0]["imported_services_vat_declared"] = "80000.00"
        self.assertNotIn("F-ZA-P", outside(run(fx)))

    def test_wording_is_neutral_and_routes_stay_unverified(self) -> None:
        result = run()
        text = json.dumps(result).lower()
        for word in ACCUSATORY:
            self.assertNotIn(word, text)
        route = load_rule_register().route_for(CODE)
        self.assertEqual(route["route_status"], "candidate_unverified")
        self.assertEqual(
            sorted(c["rule_id"] for c in route["candidates"]),
            ["CAND-IMPORTED-SERVICES-VAT", "CAND-WHT-FOREIGN"],
        )
        self.assertFalse(route["eligible_for_assessment"])


class CentralBankPurposeContractTests(unittest.TestCase):
    def test_purpose_is_optional_and_checked(self) -> None:
        contract = contract_for("central_bank", "cross_border_payment")
        record = {k: v for k, v in BASE["central_bank_payments"][0].items() if k != "purpose"}
        validate_record(contract, record)
        validate_record(contract, {**record, "purpose": "services"})
        with self.assertRaises(SourceContractError):
            validate_record(contract, {**record, "purpose": "Services; DROP"})


if __name__ == "__main__":
    unittest.main()
