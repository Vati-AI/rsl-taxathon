"""Behaviour tests for the taxpayer portal boundary (no Frappe needed)."""

import unittest
from urllib.parse import parse_qs, urlsplit

from vati_tax.portal.access import login_location, portal_access, portal_path
from vati_tax.portal.fixtures import FIXTURE_META, find_submission, list_submissions


def redirect_target(location: str) -> str:
    return parse_qs(urlsplit(location).query)["redirect-to"][0]


class PortalAccessTests(unittest.TestCase):
    def test_guest_is_sent_to_login_with_return_path(self):
        decision = portal_access("Guest", "/taxpayer/submissions/SUB-SYN-0001")
        self.assertEqual(decision.outcome, "login")
        assert decision.location is not None
        self.assertTrue(decision.location.startswith("/login?"))
        self.assertEqual(redirect_target(decision.location), "/taxpayer/submissions/SUB-SYN-0001")

    def test_missing_session_is_treated_as_guest(self):
        self.assertEqual(portal_access(None, "/taxpayer").outcome, "login")
        self.assertEqual(portal_access("", "/taxpayer").outcome, "login")

    def test_authenticated_user_is_allowed(self):
        decision = portal_access("taxpayer.a@example.test", "/taxpayer/submissions")
        self.assertEqual(decision.outcome, "allow")
        self.assertIsNone(decision.location)

    def test_redirect_target_cannot_leave_the_portal(self):
        for hostile in [
            "https://evil.example/taxpayer",
            "//evil.example/taxpayer",
            "/desk",
            "/taxpayer/../desk",
            "/taxpayerX",
            "\\\\evil.example",
            "javascript:alert(1)",
        ]:
            with self.subTest(hostile=hostile):
                self.assertEqual(portal_path(hostile), "/taxpayer")
                self.assertEqual(redirect_target(login_location(hostile)), "/taxpayer")

    def test_query_and_fragment_are_dropped(self):
        self.assertEqual(portal_path("/taxpayer/submissions?x=1#y"), "/taxpayer/submissions")


class FixtureTests(unittest.TestCase):
    def test_fixtures_are_labelled_synthetic(self):
        self.assertEqual(FIXTURE_META, {"mode": "fixture", "synthetic": True})

    def test_list_is_newest_first(self):
        dates = [item["submitted_at"] for item in list_submissions()]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_unknown_submission_is_missing(self):
        self.assertIsNone(find_submission("SUB-SYN-9999"))
        self.assertIsNotNone(find_submission("SUB-SYN-0001"))

    def test_taxpayer_payload_carries_no_risk_or_case_fields(self):
        forbidden = {"risk", "risk_score", "case_id", "expected", "scenario"}
        for item in list_submissions():
            self.assertFalse(forbidden & set(item), item["id"])


if __name__ == "__main__":
    unittest.main()
