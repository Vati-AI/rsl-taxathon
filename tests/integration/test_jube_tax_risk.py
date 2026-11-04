"""Behaviour tests for the versioned TAX-035 tax-risk rules and their Jube rendering."""

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "jube"))

import tax_risk  # noqa: E402

_spec = importlib.util.spec_from_file_location("jube_rules", ROOT / "scripts" / "jube_rules.py")
assert _spec and _spec.loader
jube_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jube_rules)

RULESET = tax_risk.load_ruleset()
RECORDS = json.loads(tax_risk.RECORDS_PATH.read_text(encoding="utf-8"))
RULE_CODES = {r["signal_code"] for r in RULESET["rules"]} | {
    r["cleared_code"] for r in RULESET["rules"] if r.get("cleared_code")
}
# Independent reviewer-readable expectation table for the five TAX-024 scenarios.
EXPECTED_SIGNALS = {
    "SYN-001": set(),
    "SYN-002": {"TR-VAT-REFUND-MISMATCH", "TR-RELATED-PARTY-FEES", "TR-PERSISTENT-LOSSES"},
    "SYN-003": set(),
    "SYN-004": {"TR-VAT-REFUND-MISMATCH", "TR-IMPORTED-SERVICE-GAP"},
    "SYN-005": set(),
}
EXPECTED_CLEARED = {"SYN-001": {"TR-VAT-REFUND-MISMATCH"}, "SYN-005": {"TR-VAT-REFUND-MISMATCH"}}


class ScenarioOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = tax_risk.evaluate_all(RULESET, RECORDS)

    def test_every_rule_is_evaluated_for_all_five_scenarios(self) -> None:
        self.assertEqual(set(self.results), set(EXPECTED_SIGNALS))
        for case_id, rows in self.results.items():
            self.assertEqual([r["rule_id"] for r in rows], [r["rule_id"] for r in RULESET["rules"]])

    def test_signals_and_non_signals_match_the_expectation_table(self) -> None:
        for case_id, rows in self.results.items():
            with self.subTest(case=case_id):
                signals = {r["rule_id"] for r in rows if r["outcome"] == "signal"}
                cleared = {r["rule_id"] for r in rows if r["outcome"] == "cleared"}
                self.assertEqual(signals, EXPECTED_SIGNALS[case_id])
                self.assertEqual(cleared, EXPECTED_CLEARED.get(case_id, set()))

    def test_codes_and_evidence_match_tax024_expected_findings(self) -> None:
        for record in RECORDS:
            with self.subTest(case=record["case_id"]):
                rows = self.results[record["case_id"]]
                produced = {r["code"]: r["evidence_ids"] for r in rows if r["code"]}
                expected = {
                    f["code"]: f["evidence_ids"]
                    for f in record["expected_findings"]
                    if f["code"] in RULE_CODES
                }
                self.assertEqual(produced, expected)

    def test_no_result_asserts_an_expected_non_finding(self) -> None:
        for record in RECORDS:
            rows = self.results[record["case_id"]]
            produced = {r["code"] for r in rows if r["code"]}
            self.assertFalse(produced & set(record["expected_non_findings"]), record["case_id"])
        for rule in RULESET["rules"]:
            self.assertIn("proven_abuse", rule["never_implies"], rule["rule_id"])

    def test_findings_outside_rule_scope_are_not_fabricated(self) -> None:
        # Classification (TAX-051) and legal-route policy gaps (TAX-052) are not rule signals.
        for record in RECORDS:
            other = {f["code"] for f in record["expected_findings"]} - RULE_CODES
            produced = {r["code"] for r in self.results[record["case_id"]] if r["code"]}
            self.assertFalse(other & produced)
        self.assertEqual(
            {r["outcome"] for r in self.results["SYN-003"]}, {"not_applicable", "no_signal"}
        )

    def test_each_signal_records_version_inputs_evidence_and_provenance(self) -> None:
        for case_id, rows in self.results.items():
            record = next(r for r in RECORDS if r["case_id"] == case_id)
            ids = {d["id"] for d in [record["return"], *record["contracts"], *record["invoices"]]}
            for row in rows:
                self.assertEqual(row["ruleset_version"], "1.0.0")
                self.assertRegex(row["rule_version"], r"^\d+\.\d+\.\d+$")
                self.assertTrue(row["inputs"])
                self.assertTrue(row["input_digest"].startswith("sha256:"))
                self.assertIs(row["synthetic"], True)
                if row["outcome"] in ("signal", "cleared"):
                    self.assertTrue(row["evidence_ids"])
                    self.assertTrue(set(row["evidence_ids"]) <= ids)
                    self.assertTrue(row["explanation"])
                    self.assertNotIn("{", row["explanation"])
                else:
                    self.assertEqual(row["evidence_ids"], [])
                    self.assertIsNone(row["explanation"])

    def test_refund_mismatch_explains_the_unsupported_amount(self) -> None:
        row = next(r for r in self.results["SYN-002"] if r["rule_id"] == "TR-VAT-REFUND-MISMATCH")
        self.assertEqual(row["inputs"]["unsupported_input_vat"], "15000.00")
        self.assertIn("LSL 15000.00", row["explanation"])
        self.assertIn("not a refund decision", row["explanation"])

    def test_evaluation_is_deterministic(self) -> None:
        again = tax_risk.evaluate_all(RULESET, copy.deepcopy(RECORDS))
        self.assertEqual(
            json.dumps(again, sort_keys=True), json.dumps(self.results, sort_keys=True)
        )

    def test_input_changes_change_outcome_and_digest(self) -> None:
        record = copy.deepcopy(RECORDS[0])
        record["return"]["input_vat_claimed"] = record["return"]["refund_claim"] = "15000.01"
        row = tax_risk.evaluate_record(RULESET, record)[0]
        self.assertEqual(
            (row["outcome"], row["inputs"]["unsupported_input_vat"]), ("signal", "0.01")
        )
        original = tax_risk.evaluate_record(RULESET, RECORDS[0])[0]
        self.assertNotEqual(row["input_digest"], original["input_digest"])

    def test_loss_run_must_be_consecutive_and_end_at_the_return_period(self) -> None:
        self.assertEqual(
            tax_risk.consecutive_losses("2025-Q4", ["2025-Q2", "2025-Q3", "2025-Q4"]), 3
        )
        self.assertEqual(
            tax_risk.consecutive_losses("2025-Q4", ["2025-Q1", "2025-Q2", "2025-Q4"]), 1
        )
        self.assertEqual(
            tax_risk.consecutive_losses("2026-Q1", ["2025-Q3", "2025-Q4", "2026-Q1"]), 3
        )
        self.assertEqual(
            tax_risk.consecutive_losses("2025-Q4", ["2025-Q1", "2025-Q2", "2025-Q3"]), 0
        )

    def test_non_synthetic_records_are_refused(self) -> None:
        record = copy.deepcopy(RECORDS[0])
        record["synthetic"] = False
        with self.assertRaises(ValueError):
            tax_risk.evaluate_record(RULESET, record)


class RulesetValidationTests(unittest.TestCase):
    def broken(self, mutate: Any) -> None:
        ruleset = copy.deepcopy(RULESET)
        mutate(ruleset)
        with self.assertRaises(tax_risk.RulesetError):
            tax_risk.validate_ruleset(ruleset)

    def test_rejects_unversioned_duplicate_or_unknown_configuration(self) -> None:
        self.broken(lambda r: r["rules"][0].update(version="1"))
        self.broken(lambda r: r.update(ruleset_version="latest"))
        self.broken(lambda r: r["rules"].append(copy.deepcopy(r["rules"][0])))
        self.broken(lambda r: r["rules"][1]["condition"].update(feature="nope"))
        self.broken(lambda r: r["rules"][1]["condition"].update(operator="~"))
        self.broken(lambda r: r["rules"][1].update(evidence="everything"))
        self.broken(lambda r: r["rules"][0].pop("cleared_condition"))
        self.broken(lambda r: r.update(synthetic=False))
        self.broken(lambda r: r["features"][0].update(jube_name="bad name"))


class JubeRenderingTests(unittest.TestCase):
    def test_scripts_use_documented_dot_notation_and_registered_tokens(self) -> None:
        scripts = {r["rule_id"]: tax_risk.jube_script(RULESET, r) for r in RULESET["rules"]}
        self.assertEqual(
            scripts["TR-VAT-REFUND-MISMATCH"],
            "If Payload.RefundClaim > 0 AND Payload.UnsupportedInputVat > 0 Then\nReturn True\nEnd If",
        )
        self.assertEqual(
            scripts["TR-PERSISTENT-LOSSES"],
            "If Payload.ConsecutiveLossPeriods >= 3 Then\nReturn True\nEnd If",
        )

    def test_payload_carries_every_feature_as_decimal_text(self) -> None:
        payload = tax_risk.jube_payload(RULESET, RECORDS[1])
        self.assertEqual(payload["case_id"], "SYN-002")
        self.assertEqual(payload["UnsupportedInputVat"], "15000.00")
        self.assertEqual(payload["ConsecutiveLossPeriods"], "3")
        for feature in RULESET["features"]:
            self.assertIsInstance(payload[feature["jube_name"]], str)

    def test_rule_names_carry_rule_versions(self) -> None:
        body = jube_rules.rule_body(RULESET, 7, RULESET["rules"][0], 0)
        self.assertEqual(body["name"], "TR-VAT-REFUND-MISMATCH v1.0.0")
        self.assertFalse(body["enableCaseWorkflow"], "case creation belongs to TAX-034")
        self.assertEqual(body["ruleScriptTypeId"], 2)


class FakeJube:
    """Records calls; models only the routes jube_rules uses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.models: list[dict[str, Any]] = []
        self.xpaths: list[dict[str, Any]] = []
        self.gateways: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []
        self.fired: dict[str, set[str]] = {}

    def __call__(self, method: str, path: str, body: Any = None, token: Any = None):
        self.calls.append((method, path))
        if path == "/api/EntityAnalysisModel/":
            if method == "POST":
                self.models.append({**body, "id": 9, "guid": "g-9"})
            return 200, self.models
        collections = {
            "RequestXPath": self.xpaths,
            "GatewayRule": self.gateways,
            "ActivationRule": self.rules,
        }
        for key, items in collections.items():
            if f"/api/EntityAnalysisModel{key}/" in path:
                if method == "GET":
                    return 200, items
                if method == "POST":
                    items.append({**body, "id": len(items) + 1})
                if method == "PUT":
                    items[:] = [body if i["id"] == body["id"] else i for i in items]
                return 200, None
        if path.startswith("/api/Invoke/EntityAnalysisModel/"):
            return 200, {
                "Activation": {name: {} for name in self.fired.get(body["case_id"], set())}
            }
        raise AssertionError(f"unexpected {method} {path}")


class JubeApplyTests(unittest.TestCase):
    def test_apply_is_idempotent_and_updates_a_changed_rule_in_place(self) -> None:
        jube = FakeJube()
        first = jube_rules.apply(RULESET, jube, "t")
        self.assertIn("model", first["created"])
        self.assertEqual(len(jube.rules), 4)
        self.assertEqual(len(jube.xpaths), len(RULESET["features"]))
        second = jube_rules.apply(RULESET, jube, "t")
        self.assertEqual((second["created"], second["updated"]), ([], []))

        bumped = copy.deepcopy(RULESET)
        bumped["rules"][2]["version"] = "1.1.0"
        bumped["rules"][2]["condition"]["value"] = "4"
        third = jube_rules.apply(bumped, jube, "t")
        self.assertEqual(third["updated"], ["TR-PERSISTENT-LOSSES v1.1.0"])
        self.assertEqual(
            len(jube.rules), 4, "a version bump updates the Jube rule, keeping history"
        )

    def test_verify_compares_live_activations_with_the_evaluator(self) -> None:
        jube = FakeJube()
        # Observed on the live stack: activation keys replace spaces with underscores.
        names = {
            r["rule_id"]: tax_risk.jube_rule_name(r).replace(" ", "_") for r in RULESET["rules"]
        }
        jube.fired = {c: {names[r] for r in rules} for c, rules in EXPECTED_SIGNALS.items()}
        self.assertTrue(jube_rules.verify(RULESET, jube, "k", "g-9", wait_s=0)["match"])
        jube.fired["SYN-001"] = {names["TR-RELATED-PARTY-FEES"]}
        self.assertFalse(jube_rules.verify(RULESET, jube, "k", "g-9", wait_s=0)["match"])


if __name__ == "__main__":
    unittest.main()
