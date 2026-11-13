"""Behaviour tests for the case reference card (TAX-133; no Frappe needed)."""

import unittest

from vati_tax.references import rules

TENANT = "00000000-0000-4000-8000-00000000000a"
CASE = "11111111-1111-4111-8111-111111111111"
LINKS = [
    {"service": "jube", "external_type": "case", "external_id": "J-77"},
    {"service": "openaleph", "external_type": "collection", "external_id": "COL-9"},
    {"service": "openaleph", "external_type": "entity", "external_id": "ab" * 32},
    {"service": "online_refunds", "external_type": "claim", "external_id": "ORA-2026-0001"},
    {"service": "audit_system", "external_type": "audit_case", "external_id": "AUD-2026-0042"},
    {"service": "idms", "external_type": "invoice", "external_id": "INV-2"},
    {"service": "etax", "external_type": "vat_return", "external_id": "VR-2026-03"},
    {"service": "idms", "external_type": "invoice", "external_id": "INV-1"},
    {"service": "frappe", "external_type": "Vati Submission", "external_id": "SUB-1"},
]


def build(audience, links=LINKS, tin="******1234"):
    return rules.card(
        tenant_id=TENANT,
        case_id=CASE,
        display_id="CASE-000123",
        tin_masked=tin,
        links=links,
        audience=audience,
    )


class OfficerCardTests(unittest.TestCase):
    def test_officers_see_every_mapped_reference(self):
        card = build(rules.OFFICER)
        self.assertEqual(card["tenant_id"], TENANT)
        self.assertEqual(card["case_id"], CASE)
        self.assertEqual(card["display_id"], "CASE-000123")
        self.assertEqual(card["tin_masked"], "******1234")
        self.assertEqual(card["rsl_claim_id"], "ORA-2026-0001")
        self.assertEqual(card["rsl_audit_case_ref"], "AUD-2026-0042")
        self.assertEqual(card["jube_case_id"], "J-77")
        self.assertEqual(card["openaleph_collection_id"], "COL-9")
        self.assertEqual(
            card["rsl_records"],
            [
                {"system": "etax", "record_type": "vat_return", "reference": "VR-2026-03"},
                {"system": "idms", "record_type": "invoice", "reference": "INV-1"},
                {"system": "idms", "record_type": "invoice", "reference": "INV-2"},
            ],
        )
        self.assertFalse(card["rsl_records_truncated"])

    def test_non_rsl_many_links_stay_off_the_card(self):
        card = build(rules.OFFICER)
        flat = repr(card)
        self.assertNotIn("SUB-1", flat)
        self.assertNotIn("ab" * 32, flat)

    def test_unmapped_references_are_null_not_missing(self):
        card = build(rules.OFFICER, links=[], tin=None)
        for field in rules.SLOTS.values():
            self.assertIn(field, card)
            self.assertIsNone(card[field])
        self.assertIsNone(card["tin_masked"])
        self.assertEqual(card["rsl_records"], [])

    def test_unacknowledged_links_are_skipped(self):
        card = build(
            rules.OFFICER, links=[{"service": "jube", "external_type": "case", "external_id": None}]
        )
        self.assertIsNone(card["jube_case_id"])

    def test_rsl_records_are_capped_and_flagged(self):
        many = [
            {"service": "idms", "external_type": "invoice", "external_id": f"INV-{i:04d}"}
            for i in range(rules.MAX_RSL_RECORDS + 5)
        ]
        card = build(rules.OFFICER, links=many)
        self.assertEqual(len(card["rsl_records"]), rules.MAX_RSL_RECORDS)
        self.assertTrue(card["rsl_records_truncated"])


class TaxpayerCardTests(unittest.TestCase):
    def test_taxpayers_see_their_own_rsl_references_only(self):
        card = build(rules.TAXPAYER)
        self.assertEqual(card["audience"], "taxpayer")
        self.assertEqual(card["rsl_claim_id"], "ORA-2026-0001")
        self.assertEqual(card["rsl_audit_case_ref"], "AUD-2026-0042")
        self.assertEqual(card["tin_masked"], "******1234")

    def test_internal_tool_references_are_hidden_the_same_way_as_absent_ones(self):
        hidden = build(rules.TAXPAYER)
        absent = build(
            rules.TAXPAYER,
            links=[
                link
                for link in LINKS
                if link["service"] not in ("jube", "openaleph", "idms", "etax")
            ],
        )
        self.assertEqual(hidden, absent)
        self.assertIsNone(hidden["jube_case_id"])
        self.assertIsNone(hidden["openaleph_collection_id"])
        self.assertEqual(hidden["rsl_records"], [])


class AudienceTests(unittest.TestCase):
    def test_unknown_audience_is_refused(self):
        with self.assertRaises(rules.UnknownAudience):
            build("service")


if __name__ == "__main__":
    unittest.main()
