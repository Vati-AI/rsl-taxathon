"""Behaviour tests for the TAX-044 purchase-invoice schedule rules (no Frappe needed)."""

import datetime
import unittest
import uuid
from decimal import Decimal

from vati_tax.submissions import invoices

TODAY = datetime.date(2026, 11, 5)
PERIOD_END = datetime.date(2026, 9, 30)
DOC = str(uuid.uuid4())


def row(**overrides):
    values = {
        "invoice_number": "INV-0912",
        "invoice_date": "2026-09-12",
        "supplier_name": "Synthetic Supplies",
        "net_amount": "10000.00",
        "vat_amount": "1500.00",
        "gross_amount": "11500.00",
        "invoice_evidence_id": DOC,
    }
    values.update(overrides)
    return values


def codes(rows, **kwargs):
    with unittest.TestCase().assertRaises(invoices.ScheduleError) as ctx:
        invoices.parse_schedule(rows, period_end=PERIOD_END, today=TODAY, **kwargs)
    return {(e.row, e.field, e.code) for e in ctx.exception.errors}


class ScheduleTests(unittest.TestCase):
    def test_valid_rows_are_parsed_exactly(self):
        rows = invoices.parse_schedule(
            [
                row(),
                row(
                    invoice_number="INV-0913",
                    net_amount="200",
                    vat_amount="30",
                    gross_amount="230",
                    amount_paid="230",
                ),
            ],  # fmt: skip
            period_end=PERIOD_END,
            today=TODAY,
        )
        self.assertEqual(rows[0].vat_amount, Decimal("1500.00"))
        self.assertEqual(rows[1].amount_paid, Decimal("230.00"))
        total = invoices.totals(rows)
        self.assertEqual((total.net, total.vat, total.gross),
                         (Decimal("10200.00"), Decimal("1530.00"), Decimal("11730.00")))  # fmt: skip

    def test_net_plus_vat_must_equal_the_total_exactly(self):
        self.assertEqual(
            codes([row(gross_amount="11500.01")]),
            {(0, "gross_amount", "invoice_total_mismatch")},
        )

    def test_amounts_are_strict(self):
        for value, code in [("1e5", "invalid_amount"), ("12,50", "invalid_amount"),
                            ("-1", "negative_amount"), ("1.005", "invalid_amount")]:  # fmt: skip
            with self.subTest(value=value):
                self.assertIn((0, "net_amount", code), codes([row(net_amount=value)]))

    def test_zero_amount_paid_means_not_paid(self):
        rows = invoices.parse_schedule(
            [row(amount_paid="0.00")], period_end=PERIOD_END, today=TODAY
        )
        self.assertIsNone(rows[0].amount_paid)

    def test_zero_vat_invoices_are_allowed(self):
        rows = invoices.parse_schedule(
            [row(vat_amount="0", gross_amount="10000.00")], period_end=PERIOD_END, today=TODAY
        )
        self.assertEqual(rows[0].vat_amount, Decimal("0.00"))

    def test_dates_are_checked_against_today_and_the_period(self):
        self.assertEqual(codes([row(invoice_date="2026-12-01")]),
                         {(0, "invoice_date", "future_date")})  # fmt: skip
        self.assertEqual(codes([row(invoice_date="2026-10-01")]),
                         {(0, "invoice_date", "after_period")})  # fmt: skip
        self.assertEqual(codes([row(invoice_date="12/09/2026")]),
                         {(0, "invoice_date", "invalid_date")})  # fmt: skip

    def test_required_fields_and_every_problem_reported(self):
        found = codes([row(invoice_number="", supplier_name=" ", invoice_date="",
                           invoice_evidence_id="not-a-uuid")])  # fmt: skip
        self.assertEqual(
            {f for _, f, _ in found},
            {"invoice_number", "supplier_name", "invoice_date", "invoice_evidence_id"},
        )

    def test_the_same_invoice_cannot_be_listed_twice(self):
        self.assertIn(
            (1, "invoice_number", "duplicate_invoice"),
            codes([row(), row(invoice_number=" inv-0912 ", supplier_name="SYNTHETIC SUPPLIES")]),
        )

    def test_schedule_size_and_presence(self):
        self.assertEqual(codes([]), {(-1, "invoices", "required")})
        self.assertEqual(
            invoices.parse_schedule([], period_end=None, today=TODAY, required=False), []
        )
        self.assertEqual(codes([row()] * (invoices.MAX_ROWS + 1)), {(-1, "invoices", "too_many")})
        self.assertEqual(codes("nope"), {(-1, "invoices", "invalid_type")})

    def test_evidence_ids_cover_invoices_and_contracts(self):
        contract = str(uuid.uuid4())
        rows = invoices.parse_schedule(
            [row(contract_evidence_id=contract), row(invoice_number="INV-2")],
            period_end=PERIOD_END,
            today=TODAY,
        )
        self.assertEqual(invoices.evidence_ids(rows), [DOC, contract])


if __name__ == "__main__":
    unittest.main()
