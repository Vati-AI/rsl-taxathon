"""Frappe-free tests for the taxpayer's live submission view (TAX-092)."""

from __future__ import annotations

import re
import unittest

from vati_tax.portal import submissions

BASE = {
    "submission_id": "VSUB-0001",
    "reference": "REF-2026-000001",
    "submission_type": "refund_claim",
    "period": "2026-09",
    "currency": "LSL",
    "status": "under_review",
    "submitted_at": "2026-11-05T10:00:00",
    "status_at": None,
    "amount": "15000.00",
    "taxpayer_name": "Synthetic Mountain Wholesale",
}
KEYS = {
    "id",
    "reference",
    "taxpayer_name",
    "tax_type",
    "period",
    "amount",
    "currency",
    "status",
    "submitted_at",
    "next_step",
    "steps",
}
# Words that would reveal internal case, risk or staff detail to a taxpayer.
INTERNAL = re.compile(r"risk|lane|score|jube|case|finding|fraud|suspic|officer'?s name", re.I)


def view(**overrides):
    return submissions.view(**{**BASE, **overrides})


class SubmissionViewTests(unittest.TestCase):
    def test_view_is_an_allowlist_of_taxpayer_fields(self):
        item = view(status="under_review")
        self.assertEqual(set(item), KEYS)
        self.assertEqual(item["tax_type"], "VAT refund claim")
        self.assertEqual(item["amount"], "15000.00")
        self.assertEqual(item["steps"][0], {"label": "Submitted", "state": "complete",
                                            "at": "2026-11-05T10:00:00"})  # fmt: skip

    def test_every_status_has_plain_guidance_without_internal_words(self):
        for status in submissions.STATUSES:
            with self.subTest(status=status):
                item = view(status=status, status_at="2026-11-06T08:00:00.000000Z")
                self.assertEqual(item["status"], status)
                text = " ".join([item["next_step"], *(s["label"] for s in item["steps"])])
                self.assertNotRegex(text, INTERNAL)
                currents = [s for s in item["steps"] if s["state"] == "current"]
                self.assertLessEqual(len(currents), 1)

    def test_mirrored_review_statuses_show_their_progress(self):
        asked = view(status="evidence_requested", status_at="2026-11-06T08:00:00.000000Z")
        self.assertEqual(asked["steps"][1], {"label": "Evidence requested", "state": "current",
                                             "at": "2026-11-06T08:00:00.000000Z"})  # fmt: skip
        self.assertIn("Evidence requests", asked["next_step"])
        done = view(status="completed", status_at="2026-11-07T08:00:00.000000Z")
        self.assertTrue(all(s["state"] == "complete" for s in done["steps"]))
        self.assertEqual(done["steps"][-1]["at"], "2026-11-07T08:00:00.000000Z")

    def test_an_unknown_status_is_never_shown_raw(self):
        item = view(status="INVESTIGATING")
        self.assertEqual(item["status"], "under_review")
        self.assertNotIn("INVESTIGATING", str(item))

    def test_missing_values_have_safe_defaults(self):
        item = view(reference=None, submission_type=None, amount=None, taxpayer_name=None,
                    submitted_at=None, status="draft")  # fmt: skip
        self.assertEqual(item["reference"], "VSUB-0001")
        self.assertEqual(item["tax_type"], "Submission")
        self.assertIsNone(item["amount"])
        self.assertEqual(item["steps"][0]["label"], "Draft saved")

    def test_newest_first_puts_unsent_drafts_by_creation_time(self):
        old = view(submission_id="A", submitted_at="2026-10-01T09:00:00")
        new = view(submission_id="B", submitted_at="2026-11-01T09:00:00")
        draft = view(submission_id="C", submitted_at=None, status="draft")
        ordered = submissions.newest_first([old, draft, new], {"C": "2026-10-15T09:00:00"})
        self.assertEqual([i["id"] for i in ordered], ["B", "C", "A"])


if __name__ == "__main__":
    unittest.main()
