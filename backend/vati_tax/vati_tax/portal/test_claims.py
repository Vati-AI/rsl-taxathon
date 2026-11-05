"""Behaviour tests for the TAX-043/TAX-044 refund claim form rules (no Frappe needed)."""

import datetime
import unittest
import uuid
from decimal import Decimal

from vati_tax.portal import claims

TODAY = datetime.date(2026, 11, 4)
EVIDENCE = str(uuid.uuid4())


def invoice(**overrides):
    row = {
        "invoice_number": "INV-0912",
        "invoice_date": "2026-09-12",
        "supplier_name": "Synthetic Supplies",
        "net_amount": "100000.00",
        "vat_amount": "15000.00",
        "gross_amount": "115000.00",
        "invoice_evidence_id": EVIDENCE,
    }
    row.update(overrides)
    return row


def form(**overrides):
    values = {
        "idempotency_key": "tax043-" + uuid.uuid4().hex,
        "period": "2026-09",
        "claimed_amount": "15000.00",
        "claim_reason": "excess_input_vat",
        "explanation": None,
        "contact_email": "accounts@example.test",
        "evidence_ids": [EVIDENCE],
        "declaration": True,
        "period_output_vat": "0.00",
        "period_input_vat": "15000.00",
        "invoices": [invoice()],
    }
    values.update(overrides)
    return values


def codes(**overrides) -> set[tuple[str, str]]:
    with unittest.TestCase().assertRaises(claims.ClaimInputError) as ctx:
        claims.validate_claim(form(**overrides), TODAY)
    return {(e.field, e.code) for e in ctx.exception.errors}


class ValidateClaimTests(unittest.TestCase):
    def test_valid_form_is_normalised(self):
        request = claims.validate_claim(
            form(claimed_amount=" 15000.5 ", contact_email="  ", explanation="  "), TODAY
        )
        self.assertEqual(request.claimed_amount, Decimal("15000.50"))
        self.assertIsNone(request.contact_email)
        self.assertIsNone(request.explanation)
        self.assertEqual(request.evidence_ids, (EVIDENCE,))

    def test_evidence_ids_accept_a_json_string_and_drop_duplicates(self):
        request = claims.validate_claim(
            form(evidence_ids=f'["{EVIDENCE.upper()}", "{EVIDENCE}"]'), TODAY
        )
        self.assertEqual(request.evidence_ids, (EVIDENCE,))

    def test_every_problem_is_reported_at_once(self):
        self.assertEqual(
            codes(
                idempotency_key="short",
                period="",
                claimed_amount="",
                claim_reason="",
                evidence_ids=[],
                declaration=False,
            ),
            {
                ("idempotency_key", "invalid_idempotency_key"),
                ("period", "required"),
                ("claimed_amount", "required"),
                ("claim_reason", "invalid_choice"),
                ("evidence_ids", "evidence_required"),
                ("declaration", "declaration_required"),
                # The schedule's invoice copy was not uploaded for this claim.
                ("invoices.0.invoice_evidence_id", "not_uploaded"),
            },
        )

    def test_missing_idempotency_key_is_refused(self):
        self.assertEqual(
            codes(idempotency_key=None), {("idempotency_key", "invalid_idempotency_key")}
        )

    def test_periods_are_months_not_in_the_future(self):
        for value, code in [
            ("2026-13", "invalid_period"),
            ("2026-Q3", "invalid_period"),  # quarters are not offered in the portal
            ("09/2026", "invalid_period"),
            ("1999-12", "invalid_period"),
            ("2026-12", "future_period"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(codes(period=value), {("period", code)})
        claims.validate_claim(form(period="2026-11"), TODAY)  # the current month is allowed

    def test_amounts_are_strict_and_positive(self):
        for value, code in [
            ("0", "zero_amount"),
            ("0.00", "zero_amount"),
            ("-5", "negative_amount"),
            ("12,50", "invalid_amount"),
            ("1e5", "invalid_amount"),
            ("15000.123", "invalid_amount"),
            ("abc", "invalid_amount"),
            ("1000000000000", "amount_out_of_range"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(codes(claimed_amount=value), {("claimed_amount", code)})

    def test_other_reason_needs_an_explanation(self):
        self.assertEqual(
            codes(claim_reason="other"), {("explanation", "explanation_required")}
        )  # fmt: skip
        claims.validate_claim(form(claim_reason="other", explanation="Synthetic reason."), TODAY)
        self.assertEqual(
            codes(explanation="x" * (claims.MAX_EXPLANATION + 1)), {("explanation", "too_long")}
        )

    def test_contact_email_is_optional_but_must_be_valid(self):
        claims.validate_claim(form(contact_email=None), TODAY)
        self.assertEqual(codes(contact_email="not-an-email"), {("contact_email", "invalid_email")})

    def test_evidence_references_are_checked(self):
        self.assertIn(("evidence_ids", "invalid_uuid"), codes(evidence_ids=["not-a-uuid"]))
        too_many = [str(uuid.uuid4()) for _ in range(claims.MAX_DOCUMENTS + 1)]
        self.assertIn(("evidence_ids", "too_many"), codes(evidence_ids=too_many))

    def test_declaration_must_be_confirmed(self):
        for value in (False, 0, "0", None, "yes"):
            with self.subTest(value=value):
                self.assertEqual(
                    codes(declaration=value), {("declaration", "declaration_required")}
                )
        for value in (True, 1, "1", "true"):
            claims.validate_claim(form(declaration=value), TODAY)

    def test_messages_never_echo_submitted_values(self):
        with self.assertRaises(claims.ClaimInputError) as ctx:
            claims.validate_claim(
                form(contact_email="secret-value@", claimed_amount="secret-amount"), TODAY
            )
        for error in ctx.exception.errors:
            self.assertNotIn("secret", error.message)


class ScheduleFormTests(unittest.TestCase):
    """TAX-044: period figures and the purchase-invoice schedule."""

    def test_valid_schedule_and_figures_are_parsed(self):
        request = claims.validate_claim(form(), TODAY)
        self.assertEqual(request.period_input_vat, Decimal("15000.00"))
        self.assertEqual(request.period_output_vat, Decimal("0.00"))
        self.assertEqual(request.invoices[0].vat_amount, Decimal("15000.00"))

    def test_period_figures_are_required_but_may_be_zero(self):
        self.assertEqual(
            codes(period_output_vat="", period_input_vat="1e5"),
            {("period_output_vat", "required"), ("period_input_vat", "invalid_amount")},
        )
        claims.validate_claim(form(period_output_vat="0", period_input_vat="0"), TODAY)

    def test_schedule_errors_name_the_row_and_field(self):
        self.assertEqual(codes(invoices=[]), {("invoices", "required")})
        self.assertEqual(
            codes(invoices=[invoice(gross_amount="1.00")]),
            {("invoices.0.gross_amount", "invoice_total_mismatch")},
        )
        self.assertEqual(
            codes(invoices=[invoice(invoice_date="2026-10-01")]),
            {("invoices.0.invoice_date", "after_period")},
        )

    def test_row_documents_must_be_uploaded_for_this_claim(self):
        other = str(uuid.uuid4())
        self.assertEqual(
            codes(invoices=[invoice(invoice_evidence_id=other)]),
            {("invoices.0.invoice_evidence_id", "not_uploaded")},
        )
        self.assertEqual(
            codes(invoices=[invoice(contract_evidence_id=other)]),
            {("invoices.0.contract_evidence_id", "not_uploaded")},
        )

    def test_schedule_accepts_a_json_string(self):
        import json

        request = claims.validate_claim(form(invoices=json.dumps([invoice()])), TODAY)
        self.assertEqual(len(request.invoices), 1)


class SameRequestTests(unittest.TestCase):
    def setUp(self):
        self.request = claims.validate_claim(form(), TODAY)
        self.stored = {
            "submission_type": "refund_claim",
            "period": "2026-09",
            "claimed_amount": 15000.0,  # Frappe reads Currency columns as floats
            "claim_reason": "excess_input_vat",
            "explanation": None,
            "contact_email": "accounts@example.test",
            "evidence_ids": f'["{EVIDENCE}"]',
            "period_output_vat": 0.0,
            "period_input_vat": 15000.0,
            # As Frappe reads child rows back: floats, date objects, 0 for empty paid.
            "invoices": [
                {
                    **invoice(),
                    "invoice_date": datetime.date(2026, 9, 12),
                    "net_amount": 100000.0,
                    "vat_amount": 15000.0,
                    "gross_amount": 115000.0,
                    "amount_paid": 0.0,
                    "supplier_tin": None,
                    "contract_evidence_id": None,
                }
            ],
        }

    def test_identical_request_is_a_replay(self):
        self.assertTrue(claims.same_request(self.request, self.stored))

    def test_any_changed_answer_is_a_conflict(self):
        for field, value in [
            ("period", "2026-08"),
            ("claimed_amount", 15000.01),
            ("claim_reason", "zero_rated_supplies"),
            ("explanation", "Changed"),
            ("contact_email", "other@example.test"),
            ("evidence_ids", f'["{uuid.uuid4()}"]'),
            ("submission_type", "vat_return"),
            ("period_output_vat", 1.0),
            ("period_input_vat", 14999.99),
            ("invoices", []),
        ]:
            with self.subTest(field=field):
                self.assertFalse(claims.same_request(self.request, {**self.stored, field: value}))


if __name__ == "__main__":
    unittest.main()
