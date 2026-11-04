"""Behaviour tests for the TAX-045 evidence response rules (no Frappe needed)."""

import datetime
import json
import unittest
import uuid

from vati_tax.responses import rules

E1, E2, E3 = (str(uuid.uuid4()) for _ in range(3))
ITEMS = json.dumps(
    [
        {"document_type": "invoice", "description": "Tax invoices for September 2026 purchases"},
        {"document_type": "bank_statement", "description": "Bank statement showing the payments"},
    ]
)
# Internal values that must never reach the taxpayer.
INTERNAL = {
    "case_id": "c0ffee00-0000-4000-8000-00000000c45e",
    "finding_ids": json.dumps(["f1nd1ng0-0000-4000-8000-0000000000f1"]),
    "authorised_by": "internal-officer@example.test",
    "authoriser_role": "supervisor",
    "case_version": 7,
    "registration_key": "internal-registration-key",
    "request_digest": "internal-digest",
}


def request(status="recorded", **extra):
    return {
        "name": "a1b2c3d4-0000-4000-8000-000000000001",
        "tenant_id": "t",
        "taxpayer_id": "p",
        "status": status,
        "due_date": datetime.date(2026, 11, 30),
        "items": ITEMS,
        "message_to_taxpayer": "Please send the invoices behind your September claim.",
        **INTERNAL,
        **extra,
    }


def answer(**overrides):
    body = {
        "idempotency_key": "response-" + uuid.uuid4().hex,
        "items": [
            {"item_index": 0, "evidence_ids": [E1, E2]},
            {"item_index": 1, "evidence_ids": [], "unavailable_reason": "Bank closed the account."},
        ],
        "message": "Synthetic: two invoices attached.",
    }
    body.update(overrides)
    return body


def codes(body):
    with unittest.TestCase().assertRaises(rules.ResponseInputError) as ctx:
        rules.build_response(body, rules.requested_items(ITEMS))
    return {(e.field, e.code) for e in ctx.exception.errors}


class TaxpayerViewTests(unittest.TestCase):
    def test_only_allowlisted_fields_are_shown(self):
        view = rules.taxpayer_view(request())
        assert view is not None
        self.assertEqual(
            set(view),
            {"request_id", "status", "due_date", "message_to_taxpayer", "items", "response"},
        )
        text = json.dumps(view)
        for value in INTERNAL.values():
            self.assertNotIn(str(value).strip('[]"'), text)
        self.assertEqual(view["due_date"], "2026-11-30")
        self.assertEqual([i["document_type"] for i in view["items"]], ["invoice", "bank_statement"])

    def test_internal_statuses_map_to_taxpayer_statuses(self):
        expected = {
            "recorded": "awaiting_response",
            "sent": "awaiting_response",
            "responded": "response_sent",
            "withdrawn": "no_longer_needed",
        }
        for internal, shown in expected.items():
            with self.subTest(internal=internal):
                view = rules.taxpayer_view(request(internal))
                assert view is not None
                self.assertEqual(view["status"], shown)

    def test_an_unknown_status_is_never_shown(self):
        self.assertIsNone(rules.taxpayer_view(request("under_internal_review")))

    def test_response_is_shown_only_once_sent_with_document_names(self):
        stored = {
            "items": json.dumps(
                [
                    {"item_index": 0, "evidence_ids": [E1], "unavailable_reason": None},
                    {"item_index": 1, "evidence_ids": [], "unavailable_reason": "Closed."},
                ]
            ),
            "message": "Attached.",
            "responded_at": datetime.datetime(2026, 11, 5, 9, 30),
        }
        view = rules.taxpayer_view(request("responded"), stored, {E1: "invoice.pdf"})
        assert view is not None and view["response"] is not None
        self.assertEqual(view["response"]["items"][0]["documents"][0]["name"], "invoice.pdf")
        self.assertEqual(view["response"]["items"][1]["unavailable_reason"], "Closed.")
        open_view = rules.taxpayer_view(request("sent"), stored)
        assert open_view is not None
        self.assertIsNone(open_view["response"])

    def test_malformed_stored_items_are_skipped(self):
        self.assertEqual(rules.requested_items("not json"), [])
        self.assertEqual(rules.requested_items(json.dumps([{"document_type": 1}])), [])


class BuildResponseTests(unittest.TestCase):
    def test_valid_answers_are_normalised_in_item_order(self):
        body = answer()
        body["items"] = list(reversed(body["items"]))
        built = rules.build_response(body, rules.requested_items(ITEMS))
        self.assertEqual([a.item_index for a in built.items], [0, 1])
        self.assertEqual(built.evidence_ids, (E1, E2))
        self.assertEqual(built.items[1].unavailable_reason, "Bank closed the account.")

    def test_every_requested_item_needs_documents_or_a_reason(self):
        self.assertEqual(
            codes(answer(items=[{"item_index": 0, "evidence_ids": [E1]}])),
            {("items.1", "answer_required")},
        )
        self.assertEqual(
            codes(
                answer(
                    items=[
                        {"item_index": 0, "evidence_ids": []},
                        {"item_index": 1, "evidence_ids": [E3]},
                    ]
                )
            ),  # fmt: skip
            {("items.0", "answer_required")},
        )

    def test_answers_must_match_the_exact_request(self):
        extra = answer()
        extra["items"].append({"item_index": 2, "evidence_ids": [E3]})
        self.assertIn(("items", "unknown_item"), codes(extra))
        dup = answer()
        dup["items"].append({"item_index": 0, "evidence_ids": [E3]})
        self.assertIn(("items.0", "duplicate_item"), codes(dup))
        self.assertIn(("items", "invalid_type"), codes(answer(items="nope")))

    def test_a_document_is_attached_to_one_item_only(self):
        body = answer(
            items=[
                {"item_index": 0, "evidence_ids": [E1]},
                {"item_index": 1, "evidence_ids": [E1]},
            ]
        )
        self.assertIn(("items.1", "evidence_reused"), codes(body))

    def test_references_keys_and_lengths_are_checked(self):
        bad = answer(items=[{"item_index": 0, "evidence_ids": ["not-a-uuid"]},
                            {"item_index": 1, "unavailable_reason": "x"}])  # fmt: skip
        self.assertIn(("items.0", "invalid_uuid"), codes(bad))
        self.assertIn(
            ("idempotency_key", "invalid_idempotency_key"), codes(answer(idempotency_key="short"))
        )
        self.assertIn(("message", "too_long"), codes(answer(message="x" * (rules.MAX_MESSAGE + 1))))
        long_reason = answer()
        long_reason["items"][1]["unavailable_reason"] = "x" * (rules.MAX_REASON + 1)
        self.assertIn(("items.1", "too_long"), codes(long_reason))

    def test_digest_is_stable_and_changes_with_the_answers(self):
        body = answer()
        first = rules.build_response(body, rules.requested_items(ITEMS)).digest("r")
        again = rules.build_response(body, rules.requested_items(ITEMS)).digest("r")
        self.assertEqual(first, again)
        other = answer(idempotency_key=body["idempotency_key"], message="Changed.")
        self.assertNotEqual(
            first, rules.build_response(other, rules.requested_items(ITEMS)).digest("r")
        )
        self.assertNotEqual(
            first, rules.build_response(body, rules.requested_items(ITEMS)).digest("s")
        )

    def test_messages_never_echo_submitted_values(self):
        body = answer(items=[{"item_index": 0, "evidence_ids": ["secret-value"]},
                             {"item_index": 1}])  # fmt: skip
        with self.assertRaises(rules.ResponseInputError) as ctx:
            rules.build_response(body, rules.requested_items(ITEMS))
        for error in ctx.exception.errors:
            self.assertNotIn("secret", error.message)


if __name__ == "__main__":
    unittest.main()
