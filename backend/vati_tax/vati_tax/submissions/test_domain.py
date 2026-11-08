"""Frappe-free tests for the TAX-031 submission rules."""

from __future__ import annotations

import datetime
import unittest
from decimal import Decimal

from vati_tax.submissions import domain

TODAY = datetime.date(2026, 11, 4)


class MoneyTests(unittest.TestCase):
    def test_accepts_exact_two_decimal_amounts(self):
        cases = {
            "15000": "15000.00",
            "15000.5": "15000.50",
            " 0.10 ": "0.10",
            15000: "15000.00",
            15000.1: "15000.10",
            Decimal("72666.670000000"): "72666.67",
            "999999999999.99": "999999999999.99",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                amount = domain.parse_money(value, "amount")
                self.assertIsInstance(amount, Decimal)
                self.assertEqual(domain.format_money(amount), expected)

    def test_rejects_malformed_amounts(self):
        for value in [
            "abc", "1e5", "1E5", "12,50", "12 500.00", "1.234", "+5", "007", "0x10",
            "NaN", "Infinity", float("nan"), float("inf"), 0.1 + 0.2, 1e20, True, [], {},
            Decimal("NaN"), Decimal("1.001"),
        ]:  # fmt: skip
            with self.subTest(value=value), self.assertRaises(domain.DomainError) as ctx:
                domain.parse_money(value, "amount")
            self.assertIn(ctx.exception.reason, {"invalid_amount", "amount_out_of_range"})

    def test_missing_negative_zero_and_out_of_range(self):
        for value, reason, kwargs in [
            (None, "required", {}),
            ("", "required", {}),
            ("-1.00", "negative_amount", {}),
            ("0.00", "zero_amount", {"allow_zero": False}),
            ("1000000000000", "amount_out_of_range", {}),
            ("-1000000000000", "amount_out_of_range", {"allow_negative": True}),
        ]:
            with self.subTest(value=value), self.assertRaises(domain.DomainError) as ctx:
                domain.parse_money(value, "amount", **kwargs)
            self.assertEqual(ctx.exception.reason, reason)
        self.assertEqual(domain.parse_money("-5", "net", allow_negative=True), Decimal("-5.00"))
        self.assertEqual(str(domain.parse_money("-0.00", "net", allow_negative=True)), "0.00")

    def test_currency_is_lsl_only(self):
        self.assertEqual(domain.validate_currency(None), "LSL")
        self.assertEqual(domain.validate_currency("LSL"), "LSL")
        for value in ("ZAR", "lsl", "USD"):
            with self.subTest(value=value), self.assertRaises(domain.DomainError):
                domain.validate_currency(value)


class PeriodTests(unittest.TestCase):
    def test_month_and_quarter(self):
        month = domain.parse_period("2026-09", TODAY)
        self.assertEqual((month.kind, month.start, month.end),
                         ("month", datetime.date(2026, 9, 1), datetime.date(2026, 9, 30)))  # fmt: skip
        quarter = domain.parse_period("2025-Q4", TODAY)
        self.assertEqual((quarter.start, quarter.end),
                         (datetime.date(2025, 10, 1), datetime.date(2025, 12, 31)))  # fmt: skip
        leap = domain.parse_period("2024-02", TODAY)
        self.assertEqual(leap.end, datetime.date(2024, 2, 29))
        self.assertEqual(domain.parse_period("2026-11", TODAY).label, "2026-11")

    def test_rejects_invalid_periods(self):
        for value in ["2026-13", "2026-00", "2026-Q5", "2026-Q0", "26-09", "2026/09", "2026-9",
                      "2026-q1", "1999-12", "", None, 202609]:  # fmt: skip
            with self.subTest(value=value), self.assertRaises(domain.DomainError) as ctx:
                domain.parse_period(value, TODAY)
            self.assertEqual(ctx.exception.reason, "invalid_period")

    def test_rejects_future_periods(self):
        for value in ("2026-12", "2027-Q1", "2027-01"):
            with self.subTest(value=value), self.assertRaises(domain.DomainError) as ctx:
                domain.parse_period(value, TODAY)
            self.assertEqual(ctx.exception.reason, "future_period")


class VatArithmeticTests(unittest.TestCase):
    def test_refund_cannot_exceed_declared_position(self):
        out, inp = Decimal("0.00"), Decimal("15000.00")
        self.assertEqual(domain.net_vat(out, inp), Decimal("-15000.00"))
        domain.validate_refund_against_return(Decimal("15000.00"), out, inp)
        with self.assertRaises(domain.DomainError) as ctx:
            domain.validate_refund_against_return(Decimal("15000.01"), out, inp)
        self.assertEqual(ctx.exception.reason, "exceeds_declared_position")
        with self.assertRaises(domain.DomainError):
            domain.validate_refund_against_return(
                Decimal("1.00"), Decimal("500.00"), Decimal("100.00")
            )


class IdentifierTests(unittest.TestCase):
    def test_tin_mask_and_validation(self):
        self.assertEqual(domain.mask_tin("123456789"), "******789")
        self.assertEqual(domain.mask_tin("123456"), "****456")
        self.assertIsNone(domain.validate_tin(""))
        for value in ("12345", "12345A789", "1234567890123456"):
            with self.subTest(value=value), self.assertRaises(domain.DomainError):
                domain.validate_tin(value)

    def test_evidence_ids(self):
        a = "80015243-dd34-4e97-b085-7356ffdd042f"
        self.assertEqual(domain.evidence_ids(f'["{a.upper()}", "{a}"]'), [a])
        self.assertEqual(domain.evidence_ids(None), [])
        for value in ("not json", '["x"]', '{"a": 1}', [a] * 0 + ["1"]):
            with self.subTest(value=value), self.assertRaises(domain.DomainError):
                domain.evidence_ids(value)

    def test_country_must_be_foreign(self):
        self.assertEqual(domain.validate_country("za"), "ZA")
        for value in ("LS", "ZAF", "", None):
            with self.subTest(value=value), self.assertRaises(domain.DomainError):
                domain.validate_country(value)

    def test_scoped_key_separates_tenants(self):
        self.assertNotEqual(domain.scoped_key("t1", "k"), domain.scoped_key("t2", "k"))
        with self.assertRaises(domain.DomainError):
            domain.validate_idempotency_key("short")


class StatusAndWriteTests(unittest.TestCase):
    def test_transitions_by_actor(self):
        domain.check_transition("draft", "submitted", "taxpayer")
        domain.check_transition("submitted", "correction_needed", "auditor")
        domain.check_transition("submitted", "correction_needed", "operator")
        denied = [
            ("draft", "submitted", "auditor"),
            ("draft", "submitted", "service"),
            ("submitted", "correction_needed", "taxpayer"),
            ("submitted", "correction_needed", "service"),
            ("submitted", "correction_needed", "specialist"),
            ("submitted", "draft", "taxpayer"),
            ("submitted", "withdrawn", "operator"),
            ("withdrawn", "draft", "operator"),
        ]
        for before, after, actor in denied:
            with self.subTest(before=before, after=after, actor=actor):
                with self.assertRaises(domain.TransitionDenied):
                    domain.check_transition(before, after, actor)

    def test_rsl_filed_claims_are_created_and_sent_by_the_intake_only(self):
        # TAX-122 (ADR-043): the taxpayer filed it in RSL's system; RSL's intake records it.
        rsl = domain.SOURCE_RSL_ONLINE_REFUNDS
        domain.check_create("service", rsl, "refund_claim")
        domain.check_create("operator", rsl, "refund_claim")
        domain.check_transition("draft", "submitted", "service", rsl)
        domain.check_transition("submitted", "correction_needed", "auditor", rsl)
        for actor, source, kind in [
            ("taxpayer", rsl, "refund_claim"),
            ("auditor", rsl, "refund_claim"),
            ("service", rsl, "vat_return"),
            ("service", "portal", "refund_claim"),
            ("auditor", "portal", "refund_claim"),
        ]:
            with self.subTest(actor=actor, source=source, kind=kind):
                with self.assertRaises(domain.TransitionDenied):
                    domain.check_create(actor, source, kind)
        domain.check_create("taxpayer", "portal", "refund_claim")
        for before, after, actor in [
            ("draft", "submitted", "taxpayer"),
            ("draft", "submitted", "auditor"),
            ("submitted", "correction_needed", "service"),
            ("draft", "withdrawn", "service"),
        ]:
            with self.subTest(before=before, after=after, actor=actor):
                with self.assertRaises(domain.TransitionDenied):
                    domain.check_transition(before, after, actor, rsl)
        self.assertFalse(domain.declaration_required(rsl))
        self.assertTrue(domain.declaration_required("portal"))
        self.assertTrue(domain.declaration_required(None))

    def test_field_writes_by_actor(self):
        writable = {"taxpayer": frozenset({"declaration"}), "service": frozenset({"case_id"})}
        server = frozenset({"version"})
        domain.check_writes({"declaration"}, "taxpayer", writable, server)
        domain.check_writes({"case_id", "declaration"}, "operator", writable, server)
        for changed, actor in [
            ({"case_id"}, "taxpayer"),
            ({"declaration"}, "service"),
            ({"declaration"}, "auditor"),
            ({"version"}, "operator"),
            ({"declaration"}, "none"),
        ]:
            with self.subTest(changed=changed, actor=actor), self.assertRaises(domain.WriteDenied):
                domain.check_writes(changed, actor, writable, server)

    def test_normalise_ignores_representation_only_changes(self):
        n = domain.normalise
        self.assertEqual(n(15000.0), n("15000.00"))
        self.assertEqual(n(Decimal("15000.000000000")), n("15000"))
        self.assertEqual(n(datetime.date(2026, 9, 1)), n("2026-09-01"))
        self.assertEqual(
            n(datetime.datetime(2026, 11, 4, 10, 0, 0, 5)), n("2026-11-04 10:00:00.000005")
        )
        self.assertEqual(n('["a"]'), n(["a"]))
        self.assertEqual(n(""), n(None))
        self.assertNotEqual(n("15000.01"), n(15000.0))


if __name__ == "__main__":
    unittest.main()
