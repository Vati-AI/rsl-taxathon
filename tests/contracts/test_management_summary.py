"""getManagementSummary in the fixture API (TAX-060): denominators, missing data, access."""

import copy
import dataclasses
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

import contract  # noqa: E402
import fixture_api  # noqa: E402

SPEC = contract.load_spec()
DATA = fixture_api.load_fixture()
MGMT = fixture_api.load_management()
TENANT = DATA["principal"]["tenant_id"]
OTHER_TENANT = "10000000-0000-4000-8000-000000000002"
SUPERVISOR = {"officer_id": "80000000-0000-4000-8000-000000000003", "roles": ["supervisor"]}
CORRELATION = "c0000000-0000-4000-8000-0000000000bb"
PATH = "/api/v1/management/summary"
EXAMPLES = ROOT / "integration" / "api" / "examples" / "http"


def call(query=None, data=DATA, mgmt=MGMT, tenant=TENANT, role="supervisor"):
    """A request as the fixture principal picked by X-Vati-Fixture-Role (TAX-042)."""
    headers = {"Authorization": "Bearer synthetic", "X-Correlation-ID": CORRELATION}
    if tenant is not None:
        headers["X-Vati-Tenant-ID"] = tenant
    if role is not None:
        headers["X-Vati-Fixture-Role"] = role
    return fixture_api.handle("GET", PATH, headers, query or {}, data, SPEC, management=mgmt)


def conforms(query, status, headers, body):
    example = {
        "name": "fixture management summary",
        "operation_id": "getManagementSummary",
        "synthetic": True,
        "request": {
            "method": "GET",
            "path": PATH,
            "query": query,
            "headers": {"X-Vati-Tenant-ID": TENANT, "X-Correlation-ID": CORRELATION},
        },
        "response": {"status": status, "headers": headers, "body": body},
    }
    return contract.check_example(SPEC, example)


def summary(query=None, **kwargs):
    status, _, body = call(query, **kwargs)
    assert status == 200, body
    return body["data"]


class AccessTests(unittest.TestCase):
    def test_supervisor_gets_a_conformant_synthetic_summary(self):
        for days in ("1", "7", "30", "90", "365"):
            with self.subTest(days=days):
                status, headers, body = call({"window_days": days})
                self.assertEqual(status, 200)
                self.assertEqual(headers[fixture_api.MODE_HEADER], "fixture")
                self.assertEqual(conforms({"window_days": days}, status, headers, body), [])
                self.assertTrue(body["data"]["synthetic"])
                self.assertEqual(body["data"]["window"]["days"], int(days))

    def test_auditor_and_specialist_are_forbidden_without_data(self):
        # No role header is the default auditor principal, as in TAX-042.
        for role in (None, "auditor", "specialist"):
            with self.subTest(role=role):
                status, headers, body = call(role=role)
                self.assertEqual(status, 403)
                self.assertEqual(body["error"]["code"], "forbidden")
                self.assertNotIn("data", body)
                self.assertEqual(conforms({}, status, headers, body), [])

    def test_unknown_fixture_role_is_rejected(self):
        self.assertEqual(call(role="director")[0], 400)

    def test_supervisor_without_a_ledger_identity_is_forbidden(self):
        viewer = {"officer_id": "80000000-0000-4000-8000-0000000000ff", "roles": ["supervisor"]}
        self.assertEqual(fixture_api.management_summary(DATA, MGMT, 30, viewer=viewer), "forbidden")

    def test_other_tenant_and_missing_tenant_are_refused(self):
        status, _, body = call(tenant=OTHER_TENANT)
        self.assertEqual((status, body["error"]["code"]), (403, "tenant_scope_denied"))
        status, _, body = call(tenant=None)
        self.assertEqual((status, body["error"]["code"]), (400, "missing_tenant_context"))

    def test_window_outside_1_to_365_is_rejected(self):
        for days in ("0", "366", "abc", "-1", "07"):
            with self.subTest(days=days):
                self.assertEqual(call({"window_days": days})[0], 400)


class MeasureTests(unittest.TestCase):
    def test_workload_counts_open_cases_from_the_queue_records(self):
        work = summary()["workload"]
        open_cases = [c for c in DATA["cases"] if c["status"] != "RESOLVED"]
        self.assertEqual(work["open_cases"], len(open_cases))
        self.assertEqual(sum(row["count"] for row in work["by_status"]), work["open_cases"])
        assigned = sum(row["open_cases"] for row in work["by_officer"])
        self.assertEqual(assigned + work["unassigned_open_cases"], work["open_cases"])
        self.assertEqual(
            [
                (r["display_name"], r["open_cases"], r["awaiting_officer_review"])
                for r in work["by_officer"]
            ],
            [("Synthetic Officer A", 7, 3), ("Synthetic Officer B", 1, 0)],
        )

    def test_closure_ratios_share_one_denominator_and_exclude_missing_outcomes(self):
        out = summary()["closure_outcomes"]
        self.assertEqual(out["closed_in_window"], 7)
        self.assertEqual(out["without_recorded_outcome"], 1)
        self.assertEqual(out["no_further_action"], {"numerator": 3, "denominator": 6})
        self.assertEqual(out["after_correction"], {"numerator": 3, "denominator": 6})
        self.assertEqual(
            out["no_further_action"]["denominator"],
            out["closed_in_window"] - out["without_recorded_outcome"],
        )

    def test_window_changes_closures_and_ledger_counts_but_not_point_in_time_figures(self):
        week, month, quarter = (summary({"window_days": d}) for d in ("7", "30", "90"))
        closed = [s["closure_outcomes"]["closed_in_window"] for s in (week, month, quarter)]
        self.assertEqual(closed, [5, 7, 8])
        self.assertEqual(
            week["closure_outcomes"]["no_further_action"], {"numerator": 1, "denominator": 4}
        )
        for key in ("workload", "unresolved_evidence"):
            self.assertEqual(week[key], quarter[key])
        self.assertEqual([s["assurance"]["entries"] for s in (week, month, quarter)], [11, 14, 15])

    def test_elapsed_time_uses_median_and_nearest_rank_p90(self):
        stats = fixture_api.duration_stats(
            [1.0, 2.0, 3.0, 4.0, 10.0, 20.0, 30.0, 40.0, 50.0, 100.0]
        )
        self.assertEqual(
            stats,
            {
                "count": 10,
                "excluded": 0,
                "median_hours": "15.0",
                "p90_hours": "50.0",
                "max_hours": "100.0",
            },
        )
        self.assertEqual(fixture_api.duration_stats([5.25])["p90_hours"], "5.3")
        empty = fixture_api.duration_stats([], excluded=2)
        self.assertEqual((empty["count"], empty["excluded"], empty["median_hours"]), (0, 2, None))

    def test_missing_timestamps_are_excluded_and_listed(self):
        body = summary()
        close = body["elapsed"]["time_to_close"]
        self.assertEqual((close["count"], close["excluded"]), (6, 1))
        metrics = {m["metric"] for m in body["missing_data"]}
        self.assertEqual(metrics, {"time_to_close", "closure_outcomes"})

    def test_empty_records_give_zero_denominators_not_rates(self):
        data = copy.deepcopy(DATA)
        data["cases"] = []
        mgmt = copy.deepcopy(MGMT)
        mgmt["closed_cases"] = []
        mgmt["findings"] = []
        mgmt["evidence_requests"] = []
        body = summary(data=data, mgmt=mgmt)
        self.assertEqual(body["workload"]["open_cases"], 0)
        self.assertEqual(
            body["closure_outcomes"]["no_further_action"], {"numerator": 0, "denominator": 0}
        )
        self.assertEqual(body["legal_route_coverage"]["verified_by_reviewer"]["denominator"], 0)
        self.assertIsNone(body["elapsed"]["open_case_age"]["median_hours"])
        self.assertEqual(conforms({}, 200, {"X-Correlation-ID": CORRELATION}, {"data": body}), [])

    def test_evidence_and_legal_routes_are_measured_from_records(self):
        body = summary()
        self.assertEqual(
            body["unresolved_evidence"],
            {
                "outstanding_requests": 3,
                "overdue_requests": 1,
                "cases_with_outstanding_requests": 2,
                "open_cases_awaiting_evidence": 1,
            },
        )
        routes = body["legal_route_coverage"]
        self.assertEqual(routes["verified_by_reviewer"], {"numerator": 3, "denominator": 7})
        self.assertEqual(
            routes["verified_by_reviewer"]["numerator"]
            + routes["unverified"]
            + routes["no_verified_route"],
            routes["verified_by_reviewer"]["denominator"],
        )

    def test_unnamed_officer_is_reported_as_missing_data(self):
        mgmt = copy.deepcopy(MGMT)
        officer_b = "80000000-0000-4000-8000-000000000002"
        mgmt["officers"] = [o for o in mgmt["officers"] if o["officer_id"] != officer_b]
        body = summary(mgmt=mgmt)
        self.assertIn("workload", {m["metric"] for m in body["missing_data"]})


class AssuranceTests(unittest.TestCase):
    def test_signals_about_the_viewer_are_withheld_and_only_counted(self):
        assurance = summary()["assurance"]
        self.assertTrue(assurance["ledger_intact"])
        self.assertEqual(assurance["overrides"], 3)
        self.assertEqual(assurance["refusals"], 2)
        # Officer A's refused self-approvals are visible to supervisor C; C's own three
        # overrides are withheld from C and only counted.
        self.assertEqual(
            assurance["review_signals"],
            [{"kind": "repeated_separation_of_duties_attempts", "count": 1}],
        )
        self.assertEqual(assurance["withheld_about_viewer"], 1)
        self.assertNotIn("officer.a@example.test", json.dumps(assurance))
        self.assertNotIn("supervisor.c@example.test", json.dumps(assurance))

    def test_a_tampered_ledger_is_reported_as_broken(self):
        entries = fixture_api.ledger_entries(TENANT, MGMT["ledger_events"])
        entries[3] = dataclasses.replace(entries[3], reason="edited after the fact")
        body = fixture_api.management_summary(DATA, MGMT, 30, viewer=SUPERVISOR, entries=entries)
        assert isinstance(body, dict)
        self.assertFalse(body["assurance"]["ledger_intact"])
        kinds = [s["kind"] for s in body["assurance"]["review_signals"]]
        self.assertIn("ledger_integrity", kinds)

    def test_ledger_unavailable_is_a_listed_gap_not_zeros(self):
        mgmt = copy.deepcopy(MGMT)
        del mgmt["ledger_events"]
        body = summary(mgmt=mgmt)
        self.assertEqual(body["assurance"]["available"], False)
        self.assertIn("assurance", {m["metric"] for m in body["missing_data"]})
        self.assertEqual(conforms({}, 200, {"X-Correlation-ID": CORRELATION}, {"data": body}), [])


class ExampleTests(unittest.TestCase):
    def test_contract_example_matches_the_fixture_response(self):
        example = json.loads(
            (EXAMPLES / "25_get_management_summary.json").read_text(encoding="utf-8")
        )
        req = example["request"]
        # Contract examples leave out the fixture-only role header; act as the supervisor.
        headers = {**req["headers"], "X-Vati-Fixture-Role": "supervisor"}
        status, _, body = fixture_api.handle(
            "GET", req["path"], headers, req["query"], DATA, SPEC, management=MGMT
        )
        self.assertEqual(status, example["response"]["status"])
        self.assertEqual(body, example["response"]["body"])


if __name__ == "__main__":
    unittest.main()
