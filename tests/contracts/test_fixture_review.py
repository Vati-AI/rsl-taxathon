"""Behaviour tests for the officer review fixture operations (TAX-042).

The fixture is what the auditor UI is developed and reviewed against, so it must
answer in the contract's shapes and refuse what TAX-046 refuses.
"""

import datetime
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

import contract  # noqa: E402
import fixture_api  # noqa: E402
import fixture_review  # noqa: E402

SPEC = contract.load_spec()
DATA = fixture_api.load_fixture()
TENANT = DATA["principal"]["tenant_id"]
NOW = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=datetime.UTC)
SYN = {c["display_id"]: c for c in DATA["cases"]}
AUDITOR = "80000000-0000-4000-8000-000000000001"
SUPERVISOR = "80000000-0000-4000-8000-000000000003"


class Client:
    def __init__(self) -> None:
        self.store = fixture_review.ReviewStore(now=NOW)

    def call(self, method, path, role=None, body=None, key=None):
        headers = {"X-Vati-Tenant-ID": TENANT, "X-Correlation-ID": str(uuid.uuid4())}
        if role:
            headers["X-Vati-Fixture-Role"] = role
        if method == "POST":
            headers["Idempotency-Key"] = key or "fixture-test-" + uuid.uuid4().hex
        status, out_headers, payload = fixture_api.handle(
            method, path, headers, {}, DATA, SPEC, body=body, review=self.store
        )
        self.last = (method, path, headers, body, status, out_headers, payload)
        return status, out_headers, payload

    def conforms(self, operation):
        method, path, headers, body, status, out_headers, payload = self.last
        example = {
            "name": f"fixture {path}",
            "operation_id": operation,
            "synthetic": True,
            "request": {"method": method, "path": path, "headers": headers, "body": body},
            "response": {"status": status, "headers": out_headers, "body": payload},
        }
        return contract.check_example(SPEC, example)

    def review(self, display_id, role=None):
        case = SYN[display_id]["case_id"]
        status, _, payload = self.call("GET", f"/api/v1/cases/{case}/review", role)
        assert status == 200, payload
        return payload["data"]

    def propose(self, display_id, role=None, key=None, **overrides):
        case = SYN[display_id]
        state = self.review(display_id, role)
        body = {
            "outcome": "continue_investigation",
            "rationale": "Synthetic rationale long enough to satisfy the twenty character minimum.",
            "rating": 3,
            "expected_case_status": self.store.jube_status[case["case_id"]],
            "expected_version": state["case_version"],
            "evidence_ids": [],
            "finding_ids": [],
        }
        body.update(overrides)
        return self.call("POST", f"/api/v1/cases/{case['case_id']}/decisions", role, body, key)

    def decide(self, display_id, decision_id, action, role="supervisor", **overrides):
        case = SYN[display_id]["case_id"]
        body = {"action": action, "expected_version": self.review(display_id, role)["case_version"]}
        body.update(overrides)
        path = f"/api/v1/cases/{case}/decisions/{decision_id}/review"
        return self.call("POST", path, role, body)


def field_code(payload):
    return payload["error"]["field_errors"][0]["code"]


class ReviewStateTests(unittest.TestCase):
    def setUp(self):
        self.c = Client()

    def test_review_state_and_findings_conform_for_every_role(self):
        for role in ("auditor", "specialist", "supervisor"):
            with self.subTest(role=role):
                self.c.review("SYN-001", role)
                self.assertEqual(self.c.conforms("getCaseReview"), [])
        case = SYN["SYN-003"]["case_id"]
        status, headers, payload = self.c.call("GET", f"/api/v1/cases/{case}/findings")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Vati-Mode"], "fixture")
        self.assertEqual(self.c.conforms("listFindings"), [])
        self.assertEqual(payload["data"][0]["proposed_action"]["type"], "specialist_review")

    def test_viewer_actions_follow_role_and_separation_of_duties(self):
        self.assertEqual(
            self.c.review("SYN-001")["viewer"]["allowed_actions"],
            ["decision.propose", "specialist.refer", "evidence_request.authorise"],
        )
        self.assertEqual(
            self.c.review("SYN-001", "specialist")["viewer"]["allowed_actions"],
            ["decision.propose"],
        )
        self.assertIn(
            "decision.approve", self.c.review("SYN-001", "supervisor")["viewer"]["allowed_actions"]
        )
        # No open proposal on SYN-003: nothing to approve.
        self.assertNotIn(
            "decision.approve", self.c.review("SYN-003", "supervisor")["viewer"]["allowed_actions"]
        )

    def test_unknown_fixture_role_is_refused(self):
        case = SYN["SYN-001"]["case_id"]
        status, _, payload = self.c.call("GET", f"/api/v1/cases/{case}/review", "director")
        self.assertEqual((status, payload["error"]["code"]), (400, "invalid_request"))


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.c = Client()

    def test_referral_needs_the_refer_permission(self):
        finding = ["70000000-0000-4000-8000-000000000301"]
        status, _, payload = self.c.propose(
            "SYN-003", "specialist", outcome="refer_to_specialist", finding_ids=finding
        )
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))
        status, _, payload = self.c.propose(
            "SYN-003", outcome="refer_to_specialist", finding_ids=finding
        )
        self.assertEqual(status, 201)
        self.assertEqual(self.c.conforms("proposeDecision"), [])
        self.assertEqual(payload["data"]["proposed_by"], AUDITOR)
        self.assertEqual(payload["data"]["jube_sync"]["state"], "not_started")
        state = self.c.review("SYN-003")
        self.assertEqual(state["case_version"], SYN["SYN-003"]["version"] + 1)
        self.assertEqual(state["audit_events"][-1]["action"], "decision.proposed")

    def test_replay_conflict_and_one_open_decision(self):
        key = "fixture-test-replay-0001"
        ask = {"outcome": "request_further_evidence"}
        first = self.c.propose("SYN-002", key=key, **ask)
        self.assertEqual(first[0], 201)
        version = SYN["SYN-002"]["version"]
        again = self.c.propose("SYN-002", key=key, expected_version=version, **ask)
        self.assertEqual(again[1]["Idempotent-Replayed"], "true")
        self.assertEqual(again[2]["data"]["decision_id"], first[2]["data"]["decision_id"])
        status, _, payload = self.c.propose(
            "SYN-002", key=key, expected_version=version, rating=5, **ask
        )
        self.assertEqual((status, payload["error"]["code"]), (422, "idempotency_conflict"))
        status, _, payload = self.c.propose("SYN-002", "supervisor", **ask)
        self.assertEqual((status, field_code(payload)), (409, "decision_pending"))

    def test_stale_version_reports_the_current_version(self):
        status, _, payload = self.c.propose("SYN-007", expected_version=1)
        self.assertEqual(status, 201)
        withdraw = self.c.decide("SYN-007", payload["data"]["decision_id"], "withdraw", "auditor")
        self.assertEqual(withdraw[0], 200)
        status, _, payload = self.c.propose("SYN-007", expected_version=1)
        self.assertEqual((status, payload["error"]["code"]), (409, "version_conflict"))
        self.assertEqual(field_code(payload), "stale_case_version")
        self.assertEqual(payload["error"]["current_version"], 3)

    def test_closing_needs_a_citation_and_status_must_match(self):
        status, _, payload = self.c.propose("SYN-003", outcome="close_no_further_action")
        self.assertEqual((status, field_code(payload)), (422, "citation_required"))
        status, _, payload = self.c.propose("SYN-003", expected_case_status="INVESTIGATING")
        self.assertEqual((status, field_code(payload)), (409, "case_state_changed"))
        status, _, payload = self.c.propose("SYN-002")  # already INVESTIGATING
        self.assertEqual((status, field_code(payload)), (422, "no_change"))

    def test_invalid_body_is_rejected_by_the_contract(self):
        status, _, payload = self.c.propose("SYN-003", outcome="approve_refund")
        self.assertEqual((status, payload["error"]["code"]), (400, "invalid_request"))
        status, _, payload = self.c.propose("SYN-003", rationale="too short")
        self.assertEqual((status, payload["error"]["code"]), (400, "invalid_request"))


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.c = Client()
        self.seeded = "d0000000-0000-4000-8000-000000000001"

    def test_proposer_and_auditor_cannot_approve(self):
        status, _, payload = self.c.decide("SYN-001", self.seeded, "approve", "auditor")
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))
        status, _, payload = self.c.propose("SYN-003", "supervisor")
        own = payload["data"]["decision_id"]
        status, _, payload = self.c.decide("SYN-003", own, "approve", "supervisor")
        self.assertEqual((status, field_code(payload)), (403, "self_approval"))

    def test_approval_syncs_once_and_moves_the_case(self):
        status, _, payload = self.c.decide("SYN-001", self.seeded, "approve")
        self.assertEqual(status, 200)
        self.assertEqual(self.c.conforms("reviewDecision"), [])
        data = payload["data"]
        self.assertEqual((data["status"], data["reviewed_by"]), ("approved", SUPERVISOR))
        self.assertEqual(data["jube_sync"]["state"], "synced")
        self.assertEqual(data["jube_sync"]["status_after"], "RESOLVED")
        case = SYN["SYN-001"]["case_id"]
        _, _, got = self.c.call("GET", f"/api/v1/cases/{case}")
        self.assertEqual(got["data"]["status"], "RESOLVED")
        status, _, payload = self.c.decide("SYN-001", self.seeded, "approve")
        self.assertEqual((status, field_code(payload)), (409, "not_pending"))
        actions = [e["action"] for e in self.c.review("SYN-001")["audit_events"]]
        self.assertEqual(actions, ["decision.proposed", "decision.approved", "decision.executed"])

    def test_expired_proposal_and_reject_reason(self):
        expired = "d0000000-0000-4000-8000-000000000005"
        status, _, payload = self.c.decide("SYN-005", expired, "approve")
        self.assertEqual((status, field_code(payload)), (409, "proposal_expired"))
        status, _, payload = self.c.decide("SYN-005", expired, "reject")
        self.assertEqual((status, field_code(payload)), (422, "too_short"))
        status, _, payload = self.c.decide(
            "SYN-005", expired, "reject", reason="Too old to approve; the case needs a fresh look."
        )
        self.assertEqual((status, payload["data"]["status"]), (200, "rejected"))


class EvidenceRequestTests(unittest.TestCase):
    def setUp(self):
        self.c = Client()
        self.case = SYN["SYN-002"]["case_id"]

    def body(self, **overrides):
        body = {
            "expected_version": self.c.review("SYN-002")["case_version"],
            "finding_ids": ["70000000-0000-4000-8000-000000000201"],
            "items": [{"document_type": "contract", "description": "Signed service agreement."}],
            "due_date": "2026-11-25",
            "message_to_taxpayer": "Please provide the signed management services agreement.",
        }
        body.update(overrides)
        return body

    def request(self, role=None, **overrides):
        path = f"/api/v1/cases/{self.case}/request-evidence"
        return self.c.call("POST", path, role, self.body(**overrides))

    def test_specialist_cannot_authorise_and_auditor_can(self):
        status, _, payload = self.request("specialist")
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))
        status, _, payload = self.request()
        self.assertEqual(status, 202)
        self.assertEqual(self.c.conforms("requestEvidence"), [])
        self.assertEqual(payload["data"]["status"], "recorded")
        self.assertEqual(payload["data"]["authorised_by"], AUDITOR)

    def test_due_dates_and_findings_are_checked(self):
        status, _, payload = self.request(due_date="2026-11-04")
        self.assertEqual((status, field_code(payload)), (422, "due_date_past"))
        status, _, payload = self.request(due_date="2027-06-01")
        self.assertEqual((status, field_code(payload)), (422, "due_date_too_far"))
        other_case_finding = ["70000000-0000-4000-8000-000000000301"]
        status, _, payload = self.request(finding_ids=other_case_finding)
        self.assertEqual((status, field_code(payload)), (422, "not_found"))


if __name__ == "__main__":
    unittest.main()
