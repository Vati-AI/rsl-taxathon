"""TAX-086 case findings recorded from the real TAX-035 rules and TAX-049 review.

Runs the Frappe-free findings engine (``vati_tax.findings``) on the live producers'
SYN-002 output and checks the recorded records against the Vati contract checker
(integration/api/contract.py), the ADR-026 labelling words and the TAX-070
feedback loop's provenance parser. The engine is bound to its memory repository;
backend/vati_tax/vati_tax/findings/test_findings_bench.py covers the DocTypes.
"""

from __future__ import annotations

import copy
import datetime
import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
sys.path.insert(0, str(ROOT / "integration" / "jube"))
sys.path.insert(0, str(ROOT / "integration" / "followthemoney"))
sys.path.insert(0, str(ROOT / "integration" / "api"))

import contract  # noqa: E402
import scheme_graph as sg  # noqa: E402
import tax_risk  # noqa: E402
from vati_tax.findings import engine, rules  # noqa: E402
from vati_tax.findings.engine import CaseRef, MemoryRepository  # noqa: E402
from vati_tax.identity import policy  # noqa: E402

from ai.tools.related_party import review_related_party  # noqa: E402
from ai.workflows import outcome_feedback  # noqa: E402

SPEC = contract.load_spec()
SCHEMAS = SPEC["components"]["schemas"]
RECORDS = {r["case_id"]: r for r in json.loads(tax_risk.RECORDS_PATH.read_text())}
REGISTRY = json.loads((ROOT / "ai/workflows/playbook_registry.synthetic.json").read_text())
EXAMPLES = {e["name"]: e for e in contract.load_examples(contract.HTTP_EXAMPLES)}
TENANT = "10000000-0000-4000-8000-000000000001"
TAXPAYER = "80015243-dd34-4e97-b085-7356ffdd042f"
CASE = "40000000-0000-4000-8000-000000000002"
NOW = datetime.datetime(2026, 11, 6, 10, 0, tzinfo=datetime.UTC)
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def scope(role: policy.Role, user: str) -> policy.TenantScope:
    return policy.TenantScope(
        user=user, tenant_id=TENANT, roles=frozenset({role}), taxpayer_ids=frozenset()
    )


SERVICE = scope(policy.SERVICE, "pipeline@example.test")
AUDITOR = scope(policy.AUDITOR, "auditor@example.test")


def ctx(s: policy.TenantScope) -> engine.Context:
    return engine.Context.build(s, None, NOW)


def source_ids(record: dict[str, Any]) -> list[str]:
    ids = [record["return"]["id"]]
    for key in ("contracts", "invoices", "payments", "documents"):
        ids.extend(item["id"] for item in record.get(key, []))
    return ids


def setup(case_key: str) -> tuple[MemoryRepository, dict[str, str]]:
    repo = MemoryRepository()
    repo.cases[CASE] = CaseRef(TENANT, CASE, TAXPAYER)
    evidence = {}
    for source in source_ids(RECORDS[case_key]):
        evidence_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"vati-synthetic:{source}"))
        evidence[source] = evidence_id
        repo.evidence[evidence_id] = (TENANT, TAXPAYER, rules.sha256_text(source))
    return repo, evidence


def stamp(name: str) -> dict[str, Any]:
    entry = next(e for e in REGISTRY["entries"] if e["kind"] == "playbook" and e["name"] == name)
    return {
        "kind": "playbook",
        "name": entry["name"],
        "version": entry["version"],
        "content_digest": entry["content_digest"],
        "registry_version": REGISTRY["registry_version"],
    }


def provenance() -> dict[str, Any]:
    finding = EXAMPLES["list_findings_imported_services_gap"]["response"]["body"]["data"][0]
    return copy.deepcopy(finding["analysis_provenance"])


def contract_errors(value: Any, name: str) -> list[str]:
    return contract.validate(value, SCHEMAS[name], SPEC, name)


class RuleSignalFindingTests(unittest.TestCase):
    def test_every_syn002_tax035_signal_is_a_contract_valid_finding(self) -> None:
        repo, evidence = setup("SYN-002")
        ruleset = tax_risk.load_ruleset()
        rows = tax_risk.evaluate_record(ruleset, RECORDS["SYN-002"])
        views = [
            engine.record_rule_signal(
                ctx(SERVICE), repo, case_id=CASE, signal=row, evidence=evidence,
                currency=ruleset["currency"],
            )
            for row in rows
        ]  # fmt: skip
        recorded = [v for v in views if v]
        self.assertEqual(
            {v["rule"]["rule_id"] for v in recorded},
            {"TR-VAT-REFUND-MISMATCH", "TR-RELATED-PARTY-FEES", "TR-PERSISTENT-LOSSES"},
        )
        for view in engine.list_findings(ctx(AUDITOR), repo, case_id=CASE):
            with self.subTest(rule=view["rule"]["rule_id"]):
                self.assertEqual(contract_errors(engine.contract_finding(view), "Finding"), [])
                self.assertEqual(view["rule"]["ruleset_version"], ruleset["ruleset_version"])
        replay = [
            engine.record_rule_signal(ctx(SERVICE), repo, case_id=CASE, signal=r, evidence=evidence)
            for r in rows
        ]
        self.assertTrue(all(v["replayed"] for v in replay if v))
        self.assertEqual(len(repo.findings), 3)

    def test_rule_text_for_all_scenarios_records_without_labels(self) -> None:
        ruleset = tax_risk.load_ruleset()
        for case_key, rows in tax_risk.evaluate_all(ruleset, list(RECORDS.values())).items():
            repo, evidence = setup(case_key)
            for row in rows:
                with self.subTest(case=case_key, rule=row["rule_id"]):
                    engine.record_rule_signal(
                        ctx(SERVICE), repo, case_id=CASE, signal=row, evidence=evidence
                    )


class PlaybookFindingTests(unittest.TestCase):
    def record(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        repo, evidence = setup("SYN-002")
        self.repo = repo
        return engine.record_playbook_result(
            ctx(SERVICE), repo, case_id=CASE, result=result, stamp=stamp("related_party"),
            run_id="run-syn-002", evidence=evidence, analysis_provenance=provenance(),
        )  # fmt: skip

    def check(self, views: list[dict[str, Any]]) -> None:
        self.assertTrue(views)
        for view in views:
            with self.subTest(code=view["code"]):
                self.assertEqual(contract_errors(engine.contract_finding(view), "Finding"), [])

    def test_syn002_related_party_review_records_contract_valid_findings(self) -> None:
        review = review_related_party(RECORDS["SYN-002"])
        views = self.record(review)
        self.check(views)
        codes = {v["code"] for v in views}
        self.assertLessEqual(
            {"related_party_transaction", "service_delivery_evidence_missing"}, codes
        )

    @unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed; runs in the CI ai-smoke job")
    def test_syn002_playbook_proposal_records_contract_valid_findings(self) -> None:
        from ai.workflows.related_party_playbook import propose_officer_review

        proposal = propose_officer_review(review_related_party(RECORDS["SYN-002"]))
        assert proposal is not None
        self.check(self.record(proposal))

    def test_feedback_is_contract_valid_and_parsed_by_the_tax070_loop(self) -> None:
        review = review_related_party(RECORDS["SYN-002"])
        views = self.record(review)
        repo = self.repo
        ruleset = tax_risk.load_ruleset()
        _, evidence = setup("SYN-002")
        rule = engine.record_rule_signal(
            ctx(SERVICE), repo, case_id=CASE,
            signal=tax_risk.evaluate_record(ruleset, RECORDS["SYN-002"])[0], evidence=evidence,
        )  # fmt: skip
        assert rule
        body = {
            "verdict": "modified",
            "reason_code": "missing_context",
            "reason": "Delivery records may be kept by the group office.",
            "modified_explanation": "No delivery record for the group services is linked yet.",
        }
        for finding_id in (views[0]["finding_id"], rule["finding_id"]):
            fb = engine.record_feedback(
                ctx(AUDITOR), repo, case_id=CASE, finding_id=finding_id, body=body,
                idempotency_key="synthetic-" + uuid.uuid4().hex,
            )  # fmt: skip
            shaped = engine.contract_feedback(fb, officer_id="80000000-0000-4000-8000-000000000001")
            self.assertEqual(contract_errors(shaped, "FindingFeedback"), [])
        self.assertEqual(contract_errors(body, "FindingFeedbackRequest"), [])
        report = engine.feedback_for_review(ctx(SERVICE), repo)
        kinds = []
        for item in report["items"]:
            self.assertEqual(contract_errors(item["finding"], "FindingSnapshot"), [])
            for entry in item["provenance"]:
                key, _ = outcome_feedback._component(entry)
                kinds.append(key[0])
        self.assertEqual(sorted(kinds), ["playbook", "rule"])


class ProvenanceValidatorTests(unittest.TestCase):
    """``rules.validate_provenance`` agrees with the contract checker."""

    def provenances(self) -> list[dict[str, Any]]:
        found = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("analysis_provenance"), dict):
                    found.append(node["analysis_provenance"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk([e["response"]["body"] for e in EXAMPLES.values()])
        return found

    def test_every_contract_example_provenance_is_accepted(self) -> None:
        examples = self.provenances()
        self.assertGreaterEqual(len(examples), 3)
        for value in examples:
            self.assertEqual(contract_errors(value, "AnalysisProvenance"), [])
            self.assertEqual(rules.validate_provenance(value), value)

    def test_broken_provenance_is_refused_by_both(self) -> None:
        mutations = [
            (("provider",), "openai"),
            (("role",), "planner"),
            (("task",), "Case Briefing"),
            (("prompt_version",), "has spaces"),
            (("settings", "temperature"), 0.2),
            (("settings", "nested"), {"a": 1}),
            (("fallback_level",), 4),
            (("fallback_level_name",), "cloud"),
            (("fell_back",), "yes"),
            (("attempts",), []),
            (("attempts", 0, "outcome"), "Rate Limited"),
            (("attempts", 0, "http_status"), 99),
            (("attempts", 0, "extra"), 1),
            (("latency_ms",), -1),
            (("token_usage", "total"), 1.5),
            (("validation", "status"), "skipped"),
            (("validation", "reason"), "Bad Reason"),
            (("router_version",), ""),
            (("unknown",), 1),
        ]
        for path, value in mutations:
            with self.subTest(path=path):
                broken = provenance()
                node: Any = broken
                for part in path[:-1]:
                    node = node[part]
                node[path[-1]] = value
                self.assertTrue(contract_errors(broken, "AnalysisProvenance"))
                with self.assertRaises(rules.InvalidFinding):
                    rules.validate_provenance(broken)
        for missing in sorted(SCHEMAS["AnalysisProvenance"]["required"]):
            with self.subTest(missing=missing):
                broken = provenance()
                del broken[missing]
                self.assertTrue(contract_errors(broken, "AnalysisProvenance"))
                with self.assertRaises(rules.InvalidFinding):
                    rules.validate_provenance(broken)


class SharedVocabularyTests(unittest.TestCase):
    def test_labelling_words_match_adr026(self) -> None:
        self.assertEqual(rules.FORBIDDEN_TERMS, sg.FORBIDDEN_TERMS)
        for text in ("RED-FLAG", "high-risk supplier", "Respect for asterisk shellfish exports"):
            self.assertEqual(rules.labelling_terms(text), sg.labelling_terms(text))

    def test_enums_match_the_contract(self) -> None:
        finding = SCHEMAS["Finding"]["properties"]
        self.assertEqual(list(rules.KINDS), finding["kind"]["enum"])
        self.assertEqual(list(rules.CATEGORIES), finding["category"]["enum"])
        self.assertEqual(list(rules.PRODUCERS), finding["producer"]["enum"])
        self.assertEqual(list(rules.CONFIDENCE), finding["confidence"]["enum"])
        self.assertEqual(
            list(rules.ACTIONS), SCHEMAS["ProposedAction"]["properties"]["type"]["enum"]
        )
        self.assertEqual(list(rules.VERDICTS), SCHEMAS["FindingFeedbackVerdict"]["enum"])
        self.assertEqual(list(rules.REASON_CODES), SCHEMAS["FindingFeedbackReasonCode"]["enum"])
        self.assertEqual(list(rules.PROVIDERS), SCHEMAS["ModelProvider"]["enum"])
        self.assertEqual(
            list(rules.REVIEWABLE_PRODUCERS),
            SCHEMAS["FindingSnapshot"]["properties"]["producer"]["enum"],
        )


if __name__ == "__main__":
    unittest.main()
