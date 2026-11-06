"""TAX-112 to TAX-116 deterministic assurance checks on synthetic fixtures (model-free)."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ai.tools.foreign_payments import three_way_match
from ai.tools.idms_match import match_claim_lines
from ai.tools.refund_triage import add_working_days, load_policy, triage_claim
from ai.tools.related_party_signals import related_party_signals
from ai.tools.source_recompute import match_lines_to_books, recompute
from ai.workflows.rule_register import RuleRegisterError, load_rule_register

ROOT = Path(__file__).resolve().parents[2]
SYN006 = json.loads((ROOT / "data/synthetic/tax112_syn006.json").read_text())
SAFT = json.loads((ROOT / "data/synthetic/tax113_saft_groups.json").read_text())
FOREIGN = json.loads((ROOT / "data/synthetic/tax115_foreign_payments.json").read_text())


def match(fx=SYN006, **kw):
    return match_claim_lines(
        fx["claim"],
        idms_invoices=fx["idms_invoices"],
        idms_available=True,
        integrated_tins=fx["integrated_tins"],
        registered_tins=fx["registered_tins"],
        asycuda_entries=fx["asycuda_entries"],
        documents=fx["documents"],
        **kw,
    )


class IdmsMatchTests(unittest.TestCase):
    def test_syn006_lines_are_classified_as_expected(self):
        result = match()
        got = {line["line_id"]: line["status"] for line in result["lines"]}
        self.assertEqual(got, SYN006["expected_classification"])
        self.assertEqual(
            {k: v["amount"] for k, v in result["vat_by_status"].items()},
            SYN006["expected_vat_by_status"],
        )

    def test_expected_findings_and_non_findings(self):
        lines = {line["line_id"]: line for line in match()["lines"]}
        for f in SYN006["expected_findings"]:
            line = lines[f["line_id"]]
            self.assertIn(f["code"], line["questions"] + line["notes"])
        self.assertEqual(lines["L04"]["questions"], [])
        self.assertTrue(
            any(n.startswith("tin_probable_typing_slip") for n in lines["L06"]["notes"])
        )
        self.assertEqual(lines["L06"]["questions"], [])

    def test_hybrid_mode_never_denies_and_hard_rule_only_relabels(self):
        hybrid = match()
        hard = match(hard_rule_mode=True)
        self.assertIsNone(hybrid["decision"])
        self.assertIsNone(hard["decision"])
        for a, b in zip(hybrid["lines"], hard["lines"]):
            self.assertEqual(a["status"], b["status"])
            if a["status"] in {"unmatched", "supplier_gap"}:
                self.assertEqual(a["treatment"], "targeted_question")
                self.assertEqual(b["treatment"], "refusal_candidate_pending_legal")

    def test_names_are_never_used_to_match(self):
        fx = copy.deepcopy(SYN006)
        for line in fx["claim"]["lines"]:
            line["supplier_name"] = "Completely Different Name"
        self.assertEqual(
            [x["status"] for x in match(fx)["lines"]], [x["status"] for x in match()["lines"]]
        )

    def test_idms_unavailable_turns_integrated_lines_into_upload_questions(self):
        fx = SYN006
        result = match_claim_lines(
            fx["claim"],
            idms_invoices=fx["idms_invoices"],
            idms_available=False,
            integrated_tins=fx["integrated_tins"],
            registered_tins=fx["registered_tins"],
            asycuda_entries=fx["asycuda_entries"],
            documents=fx["documents"],
        )
        l01 = next(x for x in result["lines"] if x["line_id"] == "L01")
        self.assertEqual(l01["status"], "unmatched")
        self.assertEqual(l01["questions"], ["upload_invoice_copy"])


class RecomputeTests(unittest.TestCase):
    def run_group(self, index):
        g = SAFT["groups"][index]
        return g, recompute(g["saft"], g["declared_returns"], vat_rate=Decimal(SAFT["vat_rate"]))

    def test_clean_group_has_timing_difference_and_no_finding(self):
        g, r = self.run_group(0)
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["standardised_dates"], g["expected"]["standardised_dates"])
        timing = {
            row["period"]
            for row in r["comparison"]
            if row["line"] == "input_vat_claimed" and row["status"] == "timing_difference"
        }
        self.assertEqual(timing, set(g["expected"]["timing_difference_periods"]))
        self.assertEqual(r["data_issues"], [])
        self.assertIsNone(r["decision"])

    def test_cooked_group_is_found_and_problems_are_flagged_not_fixed(self):
        g, r = self.run_group(1)
        self.assertEqual(
            [(f["code"], f["period"], f["amount"]["amount"]) for f in r["findings"]],
            [(f["code"], f["period"], f["amount"]) for f in g["expected"]["findings"]],
        )
        self.assertEqual(
            r["recomputed"]["2026-08"]["input_vat"]["amount"],
            g["expected"]["recomputed_input_2026_08"],
        )
        codes = {i["code"] for i in r["data_issues"]}
        self.assertTrue(set(g["expected"]["data_issue_codes"]) <= codes)
        for record in g["expected"]["contradicted_records"]:
            self.assertEqual(r["record_quality"][record], "contradicted")
        self.assertIn("taxpayer remains responsible", r["responsibility_notice"])

    def test_claimed_lines_are_found_in_the_books_across_periods_and_typos(self):
        g = SAFT["groups"][0]
        lines = [
            {
                "line_id": "late",
                "supplier_ref": "520000003",
                "invoice_number": "S3",
                "vat_amount": "6000.00",
            },
            {
                "line_id": "typo",
                "supplier_ref": "250000001",
                "invoice_number": "S1",
                "vat_amount": "30000.00",
            },
            {
                "line_id": "absent",
                "supplier_ref": "520000001",
                "invoice_number": "S9",
                "vat_amount": "9000.00",
            },
        ]
        got = match_lines_to_books(lines, g["saft"])
        self.assertEqual(got["late"]["booked_period"], "2026-08")
        self.assertTrue(got["typo"]["in_books"])
        self.assertFalse(got["absent"]["in_books"])

    def test_float_amounts_are_refused(self):
        g = copy.deepcopy(SAFT["groups"][0])
        g["saft"]["supplier_invoices"][0]["vat"] = 30000.0
        with self.assertRaises(ValueError):
            recompute(g["saft"], g["declared_returns"], vat_rate=Decimal("0.15"))


class TriageTests(unittest.TestCase):
    CLAIM = {
        "claim_id": "C1",
        "period": "2026-09",
        "claimed_amount": "100000.00",
        "submitted_on": "2026-10-22",
    }
    REC = {"findings": [], "sales_by_code": {"2026-09": {"STD": "100", "ZERO": "300"}}}

    def lines(self, *statuses, notes=(), questions=()):
        return {
            "idms_available": True,
            "lines": [
                {
                    "line_id": f"L{i}",
                    "status": s,
                    "vat": {"amount": "10000.00"},
                    "notes": list(notes),
                    "questions": list(questions),
                }
                for i, s in enumerate(statuses)
            ],
        }

    def test_fast_lane_with_target_and_full_undisputed_amount(self):
        out = triage_claim(
            self.CLAIM,
            line_match=self.lines("matched"),
            recompute=self.REC,
            rsl_score={"score": "0.10", "model_id": "M", "model_version": "1"},
        )
        self.assertEqual(out["lane"], "fast")
        self.assertEqual(out["undisputed_amount"]["amount"], "100000.00")
        self.assertEqual(out["service_target"]["decision_due"], add_working_days("2026-10-22", 10))
        self.assertIsNone(out["decision"])

    def test_standard_lane_releases_the_undisputed_portion(self):
        out = triage_claim(
            self.CLAIM,
            line_match=self.lines("matched", "supplier_gap", questions=["confirm"]),
            recompute=self.REC,
            rsl_score={"score": "0.10"},
        )
        self.assertEqual(out["lane"], "standard")
        self.assertEqual(out["undisputed_amount"]["amount"], "90000.00")
        self.assertEqual(out["undisputed_status"], "recommendation_for_refund_auditor")

    def test_material_authenticity_gap_goes_to_investigation(self):
        out = triage_claim(
            self.CLAIM,
            line_match=self.lines(
                "unmatched", "unmatched", "unmatched", notes=["supplier_not_registered"]
            ),
            recompute=self.REC,
            rsl_score={"score": "0.10"},
        )
        self.assertEqual(out["lane"], "investigation")
        self.assertIn("case_plan_due", out["service_target"])

    def test_invoice_absent_from_books_is_disputed_once(self):
        out = triage_claim(
            self.CLAIM,
            line_match=self.lines("matched", "unmatched", questions=["upload_invoice_copy"]),
            recompute=self.REC,
            rsl_score={"score": "0.10"},
            books_match={"L0": {"in_books": True}, "L1": {"in_books": False}},
        )
        self.assertEqual(out["disputed_amount"]["amount"], "10000.00")
        self.assertEqual(out["inputs"]["recompute"]["lines_not_in_books"], ["L1"])

    def test_score_alone_never_moves_a_claim_into_investigation(self):
        out = triage_claim(
            self.CLAIM,
            line_match=self.lines("matched"),
            recompute=self.REC,
            rsl_score={"score": "0.99"},
        )
        self.assertEqual(out["lane"], "standard")

    def test_missing_inputs_are_stated_not_assumed_clean(self):
        out = triage_claim(
            self.CLAIM,
            line_match=self.lines("matched"),
            recompute=None,
            rsl_score={"status": "unavailable"},
        )
        self.assertEqual(out["inputs"]["rsl_fraud_score"], "not available")
        self.assertEqual(out["inputs"]["group_risk_rating"], "not available")
        self.assertNotEqual(out["lane"], "fast")

    def test_policy_is_marked_as_a_proposed_prototype_choice(self):
        self.assertIn("verify against the VAT Act", load_policy()["notice"])


class ForeignMatchTests(unittest.TestCase):
    def run_match(self, fx=FOREIGN, **kw):
        return three_way_match(
            tin=fx["tin"],
            suppliers=fx["suppliers"],
            supplier_invoices=fx["supplier_invoices"],
            bank_payments=fx["bank_payments"],
            wht_lines=fx["wht_lines"],
            vat_returns=fx["vat_returns"],
            residence_certificates=fx["residence_certificates"],
            central_bank_payments=fx["central_bank_payments"],
            rates={k: Decimal(v) for k, v in fx["rates"].items()},
            materiality=Decimal(fx["materiality"]),
            **kw,
        )

    def test_framework_exception_journey(self):
        r = self.run_match()
        got = [(f["code"], f["counterparty"], f["amount"]["amount"]) for f in r["flags"]]
        want = [
            (f["code"], f.get("counterparty"), f["amount"]) for f in FOREIGN["expected"]["flags"]
        ]
        self.assertEqual(got, want)
        self.assertIn("M2.4m", r["flags"][0]["reason"])
        self.assertIn("no treaty relief found", r["flags"][0]["reason"])
        self.assertEqual(r["route_recommendation"], FOREIGN["expected"]["route"])
        self.assertIsNone(r["decision"])

    def test_legitimate_explanations_and_cma_caveat(self):
        rows = {row["counterparty"]: row for row in self.run_match()["rows"]}
        self.assertEqual(
            rows["F-MU-ADVISORY"]["explanations_tested"],
            FOREIGN["expected"]["explanations"]["F-MU-ADVISORY"],
        )
        self.assertIn("cma_payment_not_in_central_bank_data", rows["F-ZA-PARENT"]["notes"])
        self.assertEqual(rows["F-GB-IT"]["notes"], [])

    def test_names_do_not_join_counterparties(self):
        fx = copy.deepcopy(FOREIGN)
        for p in fx["bank_payments"]:
            p["counterparty_name"] = "F-ZA-PARENT"
            if p["counterparty_account_ref"] == "SYN-ZA-ACC-1":
                p["counterparty_account_ref"] = "SYN-UNKNOWN"
        r = self.run_match(fx)
        self.assertEqual(len(r["unresolved_payments"]), 12)

    def test_treaty_relief_without_certificate_is_a_question(self):
        fx = copy.deepcopy(FOREIGN)
        fx["residence_certificates"] = []
        codes = {q["code"] for q in self.run_match(fx)["questions"]}
        self.assertIn("provide_residence_certificate_for_treaty_relief", codes)


class RuleRegisterTests(unittest.TestCase):
    def test_every_candidate_is_unverified_and_never_eligible(self):
        reg = load_rule_register()
        self.assertTrue(reg.entries)
        for e in reg.entries:
            self.assertEqual(e["verification_status"], "unverified")
            for code in e["finding_codes"]:
                self.assertFalse(reg.route_for(code)["eligible_for_assessment"])

    def test_interest_free_loan_routes_to_the_arms_length_policy_gap(self):
        route = load_rule_register().route_for("related_party_interest_free_loan")
        self.assertEqual(route["route_status"], "policy_gap")
        self.assertEqual(route["policy_gaps"], ["GAP-ARMS-LENGTH"])

    def test_unknown_code_has_no_candidate(self):
        self.assertEqual(load_rule_register().route_for("nothing")["route_status"], "no_candidate")

    def test_loader_refuses_a_verified_or_reviewed_entry(self):
        doc = json.loads((ROOT / "ai/workflows/rule_register.candidates.json").read_text())
        doc["entries"][0]["verification_status"] = "reviewer_verified"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps(doc))
            with self.assertRaises(RuleRegisterError):
                load_rule_register(candidates=path)

    def test_related_party_triggers_and_explanations(self):
        cit = [
            {
                "cit_return_id": f"CIT-{y}",
                "year": y,
                "revenue": "1000000.00",
                "related_party_fees": str(Decimal("30000") * Decimal("1.35") ** i),
                "taxable_income_sign": "loss" if i else "profit",
            }
            for i, y in enumerate(range(2021, 2026))
        ]
        loan = {
            "document_id": "AGR-1",
            "kind": "loan",
            "related_party": True,
            "counterparty_ref": "F-ZA-PARENT",
            "principal": "5000000.00",
            "annual_interest_rate": "0",
        }
        out = related_party_signals(cit_returns=cit, agreements=[loan], loss_explanations=[])
        self.assertEqual(
            {f["code"] for f in out["findings"]},
            {"related_party_fees_with_persistent_losses", "related_party_interest_free_loan"},
        )
        explained = related_party_signals(
            cit_returns=cit,
            agreements=[],
            loss_explanations=[{"year": 2023, "code": "market_entry"}],
        )
        self.assertEqual(explained["findings"], [])
        self.assertEqual(
            explained["alternative_explanations_tested"][0]["code"], "documented_loss_explanation"
        )


if __name__ == "__main__":
    unittest.main()
