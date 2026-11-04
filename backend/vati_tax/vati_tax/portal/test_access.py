"""Behaviour tests for the taxpayer portal boundary (no Frappe needed)."""

import unittest
from urllib.parse import parse_qs, urlsplit

from vati_tax.portal.access import login_location, portal_access, portal_path
from vati_tax.portal.fixtures import (
    FIXTURE_META,
    FIXTURE_OWNERS,
    FIXTURE_TENANT_ID,
    find_submission,
    list_submissions,
)

OTHER_TENANT = "20000000-0000-4000-8000-000000000002"
TAXPAYER_A = FIXTURE_OWNERS["SUB-SYN-0001"]
TAXPAYER_B = FIXTURE_OWNERS["SUB-SYN-0002"]
ALL_OWNERS = [(FIXTURE_TENANT_ID, owner) for owner in FIXTURE_OWNERS.values()]


def redirect_target(location: str) -> str:
    return parse_qs(urlsplit(location).query)["redirect-to"][0]


class PortalAccessTests(unittest.TestCase):
    def test_guest_is_sent_to_login_with_return_path(self):
        decision = portal_access("Guest", "/taxpayer/submissions/SUB-SYN-0001", is_taxpayer=False)
        self.assertEqual(decision.outcome, "login")
        assert decision.location is not None
        self.assertTrue(decision.location.startswith("/login?"))
        self.assertEqual(redirect_target(decision.location), "/taxpayer/submissions/SUB-SYN-0001")

    def test_missing_session_is_treated_as_guest(self):
        self.assertEqual(portal_access(None, "/taxpayer", is_taxpayer=False).outcome, "login")
        self.assertEqual(portal_access("", "/taxpayer", is_taxpayer=False).outcome, "login")

    def test_taxpayer_member_is_allowed(self):
        decision = portal_access(
            "taxpayer.a@example.test", "/taxpayer/submissions", is_taxpayer=True
        )
        self.assertEqual(decision.outcome, "allow")
        self.assertIsNone(decision.location)

    def test_authenticated_non_taxpayer_is_denied(self):
        decision = portal_access("officer@example.test", "/taxpayer/submissions", is_taxpayer=False)
        self.assertEqual(decision.outcome, "deny")
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
        dates = [item["submitted_at"] for item in list_submissions(ALL_OWNERS)]
        self.assertEqual(len(dates), len(FIXTURE_OWNERS))
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_unknown_submission_is_missing(self):
        self.assertIsNone(find_submission("SUB-SYN-9999", ALL_OWNERS))
        self.assertIsNotNone(find_submission("SUB-SYN-0001", ALL_OWNERS))

    def test_taxpayer_sees_only_own_submissions(self):
        mine = list_submissions([(FIXTURE_TENANT_ID, TAXPAYER_A)])
        self.assertEqual([item["id"] for item in mine], ["SUB-SYN-0001"])

    def test_other_taxpayers_submission_is_concealed(self):
        self.assertIsNone(find_submission("SUB-SYN-0002", [(FIXTURE_TENANT_ID, TAXPAYER_A)]))
        self.assertIsNotNone(find_submission("SUB-SYN-0002", [(FIXTURE_TENANT_ID, TAXPAYER_B)]))

    def test_same_taxpayer_id_in_another_tenant_sees_nothing(self):
        self.assertEqual(list_submissions([(OTHER_TENANT, TAXPAYER_A)]), [])
        self.assertIsNone(find_submission("SUB-SYN-0001", [(OTHER_TENANT, TAXPAYER_A)]))

    def test_no_membership_sees_nothing(self):
        self.assertEqual(list_submissions([]), [])

    def test_taxpayer_payload_carries_no_risk_or_case_fields(self):
        forbidden = {"risk", "risk_score", "case_id", "expected", "scenario"}
        for item in list_submissions(ALL_OWNERS):
            self.assertFalse(forbidden & set(item), item["id"])


if __name__ == "__main__":
    unittest.main()
