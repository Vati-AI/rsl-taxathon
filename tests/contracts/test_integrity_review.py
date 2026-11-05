"""getIntegrityReview in the fixture API (TAX-077): access, privacy and wording."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

import contract  # noqa: E402
import fixture_api  # noqa: E402
import fixture_integrity  # noqa: E402

SPEC = contract.load_spec()
DATA = fixture_api.load_fixture()
FIXTURE = fixture_integrity.load_integrity()
TENANT = DATA["principal"]["tenant_id"]
OTHER_TENANT = "10000000-0000-4000-8000-000000000002"
SUPERVISOR = {"officer_id": "80000000-0000-4000-8000-000000000003", "roles": ["supervisor"]}
CORRELATION = "c0000000-0000-4000-8000-0000000000cc"
PATH = "/api/v1/management/integrity"
EXAMPLES = ROOT / "integration" / "api" / "examples" / "http"
ACCUSATORY = (
    "fraud",
    "noncompliant",
    "non-compliant",
    "violation",
    "misconduct",
    "guilty",
    "suspicious",
    "offender",
    "wrongdoing",
    "evasion",
)


def call(query=None, tenant=TENANT, role="supervisor"):
    headers = {"Authorization": "Bearer synthetic", "X-Correlation-ID": CORRELATION}
    if tenant is not None:
        headers["X-Vati-Tenant-ID"] = tenant
    if role is not None:
        headers["X-Vati-Fixture-Role"] = role
    return fixture_api.handle("GET", PATH, headers, query or {}, DATA, SPEC)


def conforms(query, status, headers, body):
    example = {
        "name": "fixture integrity review",
        "operation_id": "getIntegrityReview",
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


def review(fixture=FIXTURE, days=30, **kwargs):
    result = fixture_integrity.integrity_review(TENANT, fixture, days, viewer=SUPERVISOR, **kwargs)
    assert isinstance(result, dict), result
    return result


class AccessTests(unittest.TestCase):
    def test_supervisor_gets_a_conformant_synthetic_review(self):
        for days in ("1", "7", "30", "90", "365"):
            with self.subTest(days=days):
                status, headers, body = call({"window_days": days})
                self.assertEqual(status, 200)
                self.assertEqual(headers[fixture_api.MODE_HEADER], "fixture")
                self.assertEqual(conforms({"window_days": days}, status, headers, body), [])
                self.assertTrue(body["data"]["synthetic"])

    def test_auditor_and_specialist_are_forbidden_without_data(self):
        for role in (None, "auditor", "specialist"):
            with self.subTest(role=role):
                status, headers, body = call(role=role)
                self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))
                self.assertNotIn("data", body)
                self.assertEqual(conforms({}, status, headers, body), [])

    def test_unknown_role_other_tenant_and_missing_tenant_are_refused(self):
        self.assertEqual(call(role="director")[0], 400)
        status, _, body = call(tenant=OTHER_TENANT)
        self.assertEqual((status, body["error"]["code"]), (403, "tenant_scope_denied"))
        status, _, body = call(tenant=None)
        self.assertEqual((status, body["error"]["code"]), (400, "missing_tenant_context"))

    def test_supervisor_without_a_ledger_identity_is_forbidden(self):
        viewer = {"officer_id": "80000000-0000-4000-8000-0000000000ff", "roles": ["supervisor"]}
        self.assertEqual(
            fixture_integrity.integrity_review(TENANT, FIXTURE, 30, viewer=viewer), "forbidden"
        )

    def test_window_outside_1_to_365_is_rejected(self):
        for days in ("0", "366", "abc"):
            with self.subTest(days=days):
                self.assertEqual(call({"window_days": days})[0], 400)


class PrivacyTests(unittest.TestCase):
    def test_no_login_officer_taxpayer_or_case_identifier_is_returned(self):
        text = json.dumps(review())
        for officer in FIXTURE["officers"]:
            self.assertNotIn(officer["login"], text)
            self.assertNotIn(officer["officer_id"], text)
        for event in FIXTURE["ledger_events"]:
            self.assertNotIn(event["actor"], text)
            if event.get("target_actor"):
                self.assertNotIn(event["target_actor"], text)
            if event.get("case_id"):
                self.assertNotIn(event["case_id"], text)
        for item in FIXTURE["policy_gaps"]:
            self.assertNotIn(item["taxpayer_id"], text)
            self.assertNotIn(item["case_id"], text)

    def test_every_shown_cell_meets_the_minimum_group_size(self):
        result = review()
        k = result["min_group_size"]
        taxpayers: dict[str, set[str]] = {}
        for item in FIXTURE["policy_gaps"]:
            if item["gap_review"] == "confirmed_by_reviewer":
                taxpayers.setdefault(item["code"], set()).add(item["taxpayer_id"])
        for row in result["policy_exposure"]["areas"]:
            self.assertGreaterEqual(len(taxpayers[row["key"]]), k, row)
        recorders: dict[str, set[str]] = {}
        officers: dict[str, set[str]] = {}
        for event in FIXTURE["ledger_events"]:
            if event["action"].startswith("override.") and event["occurred_at"] >= result["window"]["since"]:
                control = event["action"].removeprefix("override.")
                recorders.setdefault(control, set()).add(event["actor"])
                officers.setdefault(control, set()).add(event["target_actor"])
        for row in result["override_patterns"]["by_control"]:
            self.assertGreaterEqual(len(recorders[row["key"]]), k, row)
            self.assertGreaterEqual(len(officers[row["key"]]), k, row)

    def test_small_areas_are_combined_and_totals_add_up(self):
        exposure = review()["policy_exposure"]
        shown = sum(r["count"] for r in exposure["areas"]) + exposure["combined"]["count"]
        self.assertEqual(shown, exposure["confirmed_gaps"])
        keys = {r["key"] for r in exposure["areas"]}
        # One taxpayer each: never shown on their own.
        self.assertNotIn("service_classification_evidence_gap", keys)
        self.assertNotIn("consecutive_loss_periods", keys)
        overrides = review()["override_patterns"]
        shown = sum(r["count"] for r in overrides["by_control"]) + overrides["combined"]["count"]
        self.assertEqual(shown, overrides["total_overrides"])
        self.assertNotIn("risk_rating", {r["key"] for r in overrides["by_control"]})

    def test_too_little_data_returns_no_counts(self):
        thin = copy.deepcopy(FIXTURE)
        thin["policy_gaps"] = thin["policy_gaps"][:2]
        exposure = review(thin)["policy_exposure"]
        self.assertEqual(exposure["status"], "insufficient_volume")
        self.assertIsNone(exposure["confirmed_gaps"])
        self.assertEqual((exposure["areas"], exposure["combined"]), ([], None))

    def test_signals_about_the_viewer_are_withheld_and_subjects_never_returned(self):
        signals = review()["review_signals"]
        self.assertEqual(signals["status"], "review_only")
        self.assertEqual(signals["withheld_about_viewer"], 1)
        kinds = {s["kind"]: s["signals"] for s in signals["by_kind"]}
        # Supervisors E and F; supervisor C (the viewer) is withheld.
        self.assertEqual(kinds["repeated_overrides"], 2)
        self.assertEqual(kinds["repeated_separation_of_duties_attempts"], 1)
        self.assertEqual(kinds["decision_overturned"], 1)
        self.assertNotIn("subject_actor", json.dumps(signals))


class WordingTests(unittest.TestCase):
    def test_policy_exposure_is_not_noncompliance_and_signals_are_not_accusations(self):
        result = review()
        self.assertIn("not a finding of noncompliance", result["policy_exposure"]["note"])
        self.assertIn("not a finding against any officer", result["review_signals"]["note"])
        self.assertIn("not a measure of any officer", result["override_patterns"]["note"])

    def test_no_accusatory_or_verdict_wording_in_the_response(self):
        text = json.dumps(review()).lower()
        for word in ACCUSATORY:
            self.assertNotIn(word, text)


class LedgerTests(unittest.TestCase):
    def test_a_tampered_ledger_is_reported_as_broken(self):
        from dataclasses import replace

        entries = fixture_integrity.ledger_entries(TENANT, FIXTURE["ledger_events"])
        entries[0] = replace(entries[0], reason="changed after the fact")
        result = review(entries=entries)
        self.assertEqual(result["ledger"], {"available": True, "intact": False})
        self.assertIn("ledger_integrity", {s["kind"] for s in result["review_signals"]["by_kind"]})

    def test_ledger_unavailable_is_a_listed_gap_not_zeros(self):
        result = review(ledger_available=False)
        self.assertEqual(result["override_patterns"], {"status": "unavailable"})
        self.assertEqual(result["missing_data"][0]["metric"], "integrity")
        status, headers, body = 200, {"X-Correlation-ID": CORRELATION, "X-Vati-Mode": "fixture"}, {"data": result}
        self.assertEqual(conforms({}, status, headers, body), [])

    def test_window_moves_window_figures(self):
        self.assertLess(review(days=7)["policy_exposure"]["confirmed_gaps"] or 0, 14)
        self.assertEqual(review(days=30)["policy_exposure"]["confirmed_gaps"], 14)


class ExampleTests(unittest.TestCase):
    def test_contract_examples_match_the_fixture_responses(self):
        for name, role in (
            ("27_get_integrity_review.json", "supervisor"),
            ("28_error_integrity_review_forbidden.json", "auditor"),
        ):
            with self.subTest(example=name):
                example = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
                headers = {**example["request"]["headers"], "X-Vati-Fixture-Role": role}
                status, _, body = fixture_api.handle("GET", PATH, headers, {}, DATA, SPEC)
                self.assertEqual(status, example["response"]["status"])
                self.assertEqual(body, example["response"]["body"])


if __name__ == "__main__":
    unittest.main()
