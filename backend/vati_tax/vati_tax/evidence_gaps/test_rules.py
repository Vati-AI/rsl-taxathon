"""Frappe-free tests for gap-only evidence requests (TAX-127, ADR-050).

The main test runs the TAX-118 synthetic taxpayers through the real TAX-112 line match
(``ai.evaluation.assurance_pipeline``) and checks that every proposal asks about exactly
the unmatched lines and lists every other line as already held by RSL.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest
from typing import Any

from vati_tax.evidence_gaps import rules
from vati_tax.responses import rules as responses

REPO = pathlib.Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))
try:
    from ai.evaluation.assurance_pipeline import assess_taxpayer, build_sources
    from ai.evaluation.assurance_population import generate
except ImportError:  # pragma: no cover - ai/ is not on the path
    generate = None  # type: ignore[assignment]

PARAMS = {
    "n_taxpayers": 12,
    "n_dev": 4,
    "issue_counts": {"fake_invoices": 2, "untaxed_imported_services": 1,
                     "disguised_employment": 1, "related_party_loss": 1, "policy_gap": 1,
                     "mixed": 0},
}  # fmt: skip
ASKED = {rules.UNMATCHED, rules.SUPPLIER_GAP}
# Words that would reveal Vati's internal assessment to the taxpayer.
INTERNAL = ("score", "risk", "lane", "finding", "fraud", "suspicious", "officer", "supplier_gap",
            "unmatched", "authenticity", "disputed")  # fmt: skip


def line(line_id: str, kind: str = "domestic", **extra: Any) -> dict[str, Any]:
    return {"line_id": line_id, "kind": kind, "supplier_ref": "510000014",
            "invoice_number": f"INV-{line_id}", "invoice_date": "2026-09-14",
            "net_amount": "1000.00", "vat_amount": "150.00", **extra}  # fmt: skip


CLAIM = {
    "claim_id": "ORA-2026-000731",
    "period": "2026-09",
    "lines": [line("L1"), line("L2"), line("L3"), line("E1", "import"), line("E2", "import")],
}
MATCH = [
    {"line_id": "L1", "status": "matched"},
    {"line_id": "L2", "status": "supplier_gap"},
    {"line_id": "L3", "status": "unintegrated_supplier"},
    {"line_id": "E1", "status": "matched"},
    {"line_id": "E2", "status": "unmatched"},
]


@unittest.skipIf(generate is None, "ai/ is not on the path")
class SyntheticTaxpayerTests(unittest.TestCase):
    cases: list[tuple[dict[str, Any], list[dict[str, Any]]]]
    """TAX-118 synthetic taxpayers through the real TAX-112 match."""

    @classmethod
    def setUpClass(cls) -> None:
        world, _key = generate(13, PARAMS)
        sources = build_sources(world)
        tenant = str(world["tenant_id"])
        cls.cases = []
        for tin in sorted(world["submissions"]):
            result = assess_taxpayer(world, sources, tin)
            claims = {
                c["claim_id"]: c
                for c in sources.read("online_refunds", "claim", tenant_id=tenant,
                                      taxpayer=tin)["records"]
            }  # fmt: skip
            for claim_result in result["claims"]:
                # The lane proposal stores exactly these three fields per line.
                match = [{"line_id": x["line_id"], "status": x["status"],
                          "treatment": x["treatment"]}
                         for x in claim_result["match"]["lines"]]  # fmt: skip
                cls.cases.append((claims[claim_result["claim_id"]], match))

    def test_requests_contain_only_the_unmatched_lines(self) -> None:
        asked_any = held_only = 0
        for claim, match in self.cases:
            status = {m["line_id"]: m["status"] for m in match}
            try:
                proposal = rules.build_proposal(claim, match)
            except rules.GapProposalError as exc:
                self.assertEqual(exc.code, "too_many_unmatched_lines")
                self.assertGreater(sum(s in ASKED for s in status.values()), rules.MAX_ITEMS)
                continue
            asked = {i["line"]["line_id"] for i in proposal["items"]}
            held = {h["line_id"] for h in proposal["already_held"]}
            self.assertEqual(asked, {lid for lid, s in status.items() if s in ASKED})
            self.assertEqual(held, {lid for lid, s in status.items() if s not in ASKED})
            self.assertEqual(asked | held, {str(x["line_id"]) for x in claim["lines"]})
            for item in proposal["items"]:
                self.assertEqual(item["answer_options"], ["confirm", "dispute", "attach_proof"])
            text = json.dumps(proposal["items"] + proposal["already_held"]).lower()
            for word in INTERNAL:
                self.assertNotIn(word, text)
            asked_any += bool(asked)
            held_only += not asked
        # The population exercises both: claims with gaps and claims fully evidenced.
        self.assertGreater(asked_any, 0)
        self.assertGreater(held_only, 0)


class ProposalTests(unittest.TestCase):
    def test_lines_held_by_rsl_are_shown_not_asked(self) -> None:
        proposal = rules.build_proposal(CLAIM, MATCH)
        held = {h["line_id"]: h["held_by"] for h in proposal["already_held"]}
        self.assertEqual(held, {"L1": "lekuka", "L3": "online_refunds_upload", "E1": "asycuda"})
        items = {i["line"]["line_id"]: i for i in proposal["items"]}
        self.assertEqual(set(items), {"L2", "E2"})
        self.assertEqual(items["L2"]["line"]["gap"], "supplier_not_declared")
        self.assertEqual(items["L2"]["document_type"], "invoice")
        self.assertEqual(items["E2"]["line"]["gap"], "customs_entry_not_matched")
        self.assertEqual(items["E2"]["document_type"], "import_declaration")
        self.assertIn("INV-E2", items["E2"]["description"])
        self.assertIn("LSL 150.00", items["L2"]["description"])
        self.assertEqual(proposal["counts"], {"lines": 5, "already_held": 3, "asked": 2})
        self.assertEqual(rules.request_items(proposal)[0],
                         {"document_type": "invoice",
                          "description": items["L2"]["description"]})  # fmt: skip
        self.assertEqual([x["index"] for x in rules.line_items(proposal)], [0, 1])

    def test_digest_pins_what_the_officer_reviewed(self) -> None:
        first = rules.build_proposal(CLAIM, MATCH)
        self.assertEqual(
            first["proposal_digest"], rules.build_proposal(CLAIM, MATCH)["proposal_digest"]
        )
        moved = copy.deepcopy(MATCH)
        moved[1]["status"] = "matched"
        self.assertNotEqual(first["proposal_digest"],
                            rules.build_proposal(CLAIM, moved)["proposal_digest"])  # fmt: skip

    def test_out_of_date_or_bad_inputs_give_no_proposal(self) -> None:
        cases = {
            "line_match_out_of_date": MATCH[:-1],
            "unknown_match_status": [*MATCH[:-1], {"line_id": "E2", "status": "rejected"}],
        }
        for code, match in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(rules.GapProposalError) as caught:
                    rules.build_proposal(CLAIM, match)
                self.assertEqual(caught.exception.code, code)
        many = {"claim_id": "C", "lines": [line(f"L{n}") for n in range(21)]}
        with self.assertRaises(rules.GapProposalError) as caught:
            rules.build_proposal(many, [{"line_id": f"L{n}", "status": "unmatched"}
                                        for n in range(21)])  # fmt: skip
        self.assertEqual(caught.exception.code, "too_many_unmatched_lines")

    def test_nothing_outside_the_allowlist_reaches_the_taxpayer(self) -> None:
        proposal = rules.build_proposal(CLAIM, MATCH)
        bad = copy.deepcopy(proposal)
        bad["items"][0]["line"]["risk_score"] = "0.91"
        with self.assertRaises(rules.GapProposalError):
            rules.check_taxpayer_safe(bad)
        bad = copy.deepcopy(proposal)
        bad["already_held"][0]["finding_id"] = "x"
        with self.assertRaises(rules.GapProposalError):
            rules.check_taxpayer_safe(bad)


def stored_request(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "11111111-2222-4333-8444-555555555555",
        "status": "recorded",
        "due_date": "2026-11-26",
        "message_to_taxpayer": "Please answer the lines below.",
        "items": json.dumps(rules.request_items(proposal)),
        "line_items": json.dumps(rules.line_items(proposal)),
        "already_held": json.dumps(proposal["already_held"]),
        "finding_ids": json.dumps(["f1"]),
        "case_id": "case",
        "authorised_by": "officer@example.test",
    }


class ResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = stored_request(rules.build_proposal(CLAIM, MATCH))
        self.items = responses.requested_items(self.request["items"], self.request["line_items"])

    def answer(self, *answers: dict[str, Any]) -> Any:
        return responses.build_response(
            {"idempotency_key": "synthetic-response-0001", "items": list(answers)}, self.items
        )

    def test_taxpayer_view_shows_lines_held_and_options_only(self) -> None:
        view = responses.taxpayer_view(self.request)
        assert view is not None
        self.assertEqual([i["line"]["line_id"] for i in view["items"]], ["L2", "E2"])
        self.assertEqual(view["items"][0]["answer_options"],
                         ["confirm", "dispute", "attach_proof"])  # fmt: skip
        self.assertEqual({h["line_id"] for h in view["already_held"]}, {"L1", "L3", "E1"})
        text = json.dumps(view)
        for internal in ("finding_ids", "case_id", "authorised_by", "officer@example.test"):
            self.assertNotIn(internal, text)

    def test_line_answers(self) -> None:
        doc = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        ok = self.answer({"item_index": 0, "answer": "confirm"},
                         {"item_index": 1, "answer": "attach_proof", "evidence_ids": [doc]})  # fmt: skip
        self.assertEqual([a.answer for a in ok.items], ["confirm", "attach_proof"])
        self.assertEqual(ok.items[0].as_dict()["answer"], "confirm")
        disputed = self.answer({"item_index": 0, "answer": "dispute",
                                "unavailable_reason": "This invoice was cancelled."},
                               {"item_index": 1, "answer": "confirm"})  # fmt: skip
        self.assertEqual(disputed.items[0].answer, "dispute")
        bad = {
            "reason_required": [{"item_index": 0, "answer": "dispute"},
                                {"item_index": 1, "answer": "confirm"}],
            "document_required": [{"item_index": 0, "answer": "attach_proof"},
                                  {"item_index": 1, "answer": "confirm"}],
            "answer_required": [{"item_index": 0, "answer": "pay"},
                                {"item_index": 1, "answer": "confirm"}],
        }  # fmt: skip
        for code, answers in bad.items():
            with self.subTest(code=code):
                with self.assertRaises(responses.ResponseInputError) as caught:
                    self.answer(*answers)
                self.assertEqual(caught.exception.errors[0].code, code)
        with self.assertRaises(responses.ResponseInputError) as caught:
            self.answer({"item_index": 0, "answer": "confirm"})  # line 1 unanswered
        self.assertEqual(caught.exception.errors[0].field, "items.1")

    def test_ordinary_requests_are_unchanged(self) -> None:
        plain = responses.requested_items(json.dumps([{"document_type": "invoice",
                                                       "description": "Supplier invoices."}]))  # fmt: skip
        self.assertIsNone(plain[0].line)
        answer = responses.build_response(
            {"idempotency_key": "synthetic-response-0002",
             "items": [{"item_index": 0, "unavailable_reason": "Lost in a fire."}]}, plain
        )  # fmt: skip
        self.assertNotIn("answer", answer.items[0].as_dict())
        with self.assertRaises(responses.ResponseInputError) as caught:
            responses.build_response(
                {"idempotency_key": "synthetic-response-0003",
                 "items": [{"item_index": 0, "answer": "confirm"}]}, plain
            )  # fmt: skip
        self.assertEqual(caught.exception.errors[0].code, "unknown_answer")


if __name__ == "__main__":
    unittest.main()
