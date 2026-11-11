"""Case attention fields in the fixture API (TAX-147): queue views count real records."""

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

import contract  # noqa: E402
import fixture_api  # noqa: E402
import fixture_review  # noqa: E402

SPEC = contract.load_spec()
TENANT = fixture_api.load_fixture()["principal"]["tenant_id"]
CORRELATION = "c0000000-0000-4000-8000-0000000001a7"
HEADERS = {
    "Authorization": "Bearer synthetic",
    "X-Vati-Tenant-ID": TENANT,
    "X-Correlation-ID": CORRELATION,
}


def case_id(n: int) -> str:
    return f"40000000-0000-4000-8000-{n:012d}"


class CaseAttentionTests(unittest.TestCase):
    def setUp(self) -> None:
        # A fresh review store per test: its state is what the counts come from.
        self.store = fixture_review.ReviewStore()
        self.data = fixture_api.load_fixture()

    def get(self, path: str):
        return fixture_api.handle(
            "GET", path, dict(HEADERS), {}, self.data, SPEC, review=self.store
        )

    def test_list_reports_attention_from_decisions_and_evidence_requests(self) -> None:
        status, headers, body = self.get("/api/v1/cases")
        self.assertEqual(status, 200)
        by_id = {c["case_id"]: c["attention"] for c in body["data"]}
        # SYN-004 has a sent evidence request three days past due.
        self.assertTrue(by_id[case_id(4)]["overdue"])
        self.assertEqual(by_id[case_id(4)]["outstanding_evidence_requests"], 1)
        # SYN-002's request is due in nine days: outstanding, not overdue.
        two = by_id[case_id(2)]
        self.assertFalse(two["overdue"])
        due = datetime.date.fromisoformat(two["evidence_due_date"])
        self.assertGreater(due, datetime.date.today())
        # Seeded proposals wait for approval (case_review.json decisions).
        waiting = {c for c, a in by_id.items() if a["decision_awaiting_approval"]}
        seeded = {d["case_id"] for d in self.store.review["decisions"]}
        self.assertEqual(waiting, seeded)
        example = {
            "name": "fixture case list with attention",
            "operation_id": "listCases",
            "synthetic": True,
            "request": {"method": "GET", "path": "/api/v1/cases", "headers": HEADERS},
            "response": {"status": status, "headers": headers, "body": body},
        }
        self.assertEqual(contract.check_example(SPEC, example), [])

    def test_get_case_carries_the_same_attention(self) -> None:
        _, _, listed = self.get("/api/v1/cases")
        status, _, body = self.get(f"/api/v1/cases/{case_id(4)}")
        self.assertEqual(status, 200)
        expected = next(c for c in listed["data"] if c["case_id"] == case_id(4))["attention"]
        self.assertEqual(body["data"]["attention"], expected)


if __name__ == "__main__":
    unittest.main()
