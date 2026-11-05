"""TAX-080 governed extension contracts: catalog policy, readiness and registry guard."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.workflows import playbook_extensions as px
from ai.workflows.legal_routes import load_registry as load_legal_registry

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads(px.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
DOMAINS = (
    "paye_reconciliation",
    "withholding_review",
    "treaty_pe_facts",
    "digital_services_review",
)
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def _registry(*entries: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(entries=lambda: entries)


class CatalogPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = copy.deepcopy(CATALOG)
        self.legal = load_legal_registry()

    def domain(self, name: str = "paye_reconciliation") -> dict:
        return next(d for d in self.doc["domains"] if d["name"] == name)

    def test_shipped_catalog_defines_the_four_domains(self) -> None:
        catalog = px.parse_catalog(self.doc)
        self.assertEqual(set(catalog), set(DOMAINS))
        for contract in catalog.values():
            with self.subTest(contract.name):
                self.assertTrue(contract.sources and contract.legal_questions)
                self.assertEqual({f.kind for f in contract.fixtures}, px.REQUIRED_FIXTURE_KINDS)

    def test_every_shipped_domain_is_blocked_before_implementation(self) -> None:
        catalog = px.parse_catalog(self.doc)
        for contract in catalog.values():
            with self.subTest(contract.name):
                blockers = px.readiness(contract, legal_registry=self.legal, authored_fixtures=())
                self.assertIn("legal_review_pending", blockers)
                self.assertIn(f"fixture_not_authored:{contract.fixtures[0].id}", blockers)
                for source in contract.sources:
                    self.assertEqual(
                        f"source_not_in_v1:{source.id}" in blockers, source.v1_status == "not_in_v1"
                    )

    def test_policy_violations_are_refused(self) -> None:
        def drop_fixture_kind(d):
            d["evaluation_fixtures"] = [
                f for f in d["evaluation_fixtures"] if f["kind"] != "legitimate"
            ]

        def self_review(d):
            d["legal_review_gate"].update(reviewed_by=d["proposed_by"], reviewed_on="2026-11-05")

        cases = {
            "not synthetic": lambda doc, d: doc.update(synthetic=False),
            "declared ready": lambda doc, d: d.update(stage="approved"),
            "no sources": lambda doc, d: d.update(required_sources=[]),
            "unknown source system": lambda doc, d: d["required_sources"][0].update(
                system_of_record="web"
            ),
            "no legal gate": lambda doc, d: d.pop("legal_review_gate"),
            "wrong reviewer role": lambda doc, d: d["legal_review_gate"].update(
                reviewer_role="officer"
            ),
            "no legal questions": lambda doc, d: d["legal_review_gate"].update(questions=[]),
            "non-synthetic reviewer": lambda doc, d: d["legal_review_gate"].update(
                reviewed_by="real.person", reviewed_on="2026-11-05"
            ),
            "proposer reviews": lambda doc, d: self_review(d),
            "missing fixture kind": lambda doc, d: drop_fixture_kind(d),
            "no fixtures": lambda doc, d: d.update(evaluation_fixtures=[]),
            "forbidden code": lambda doc, d: d["finding_codes"].append("tax_assessment"),
            "one alternative": lambda doc, d: d.update(alternative_explanations=["only one"]),
            "apply action": lambda doc, d: d["allowed_operations"].append("apply_approved_action"),
            "unknown model role": lambda doc, d: d["model_roles"].append("vision"),
            "not deterministic": lambda doc, d: d["provenance"].remove("deterministic"),
            "category outside contract": lambda doc, d: d.update(finding_category="paye"),
            "tool budget too high": lambda doc, d: d["budgets"].update(max_tool_calls=9),
            "no budgets": lambda doc, d: d.pop("budgets"),
            "interrupt without checkpoint": lambda doc, d: d["allowed_operations"].append(
                "interrupt_for_human"
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                doc = copy.deepcopy(CATALOG)
                mutate(doc, next(d for d in doc["domains"] if d["name"] == "paye_reconciliation"))
                with self.assertRaises(px.CatalogError):
                    px.parse_catalog(doc)

    def test_duplicate_fixture_ids_across_domains_are_refused(self) -> None:
        self.domain("withholding_review")["evaluation_fixtures"][0]["id"] = "TAX080-PAYE-01"
        with self.assertRaises(px.CatalogError):
            px.parse_catalog(self.doc)

    def test_readiness_clears_only_when_every_gate_is_met(self) -> None:
        domain = self.domain("digital_services_review")
        for source in domain["required_sources"]:
            source["v1_status"] = "available"
        domain["legal_review_gate"].update(reviewed_by="SYN-REVIEWER-02", reviewed_on="2026-11-05")
        contract = px.parse_catalog(self.doc)["digital_services_review"]
        fixtures = [f.id for f in contract.fixtures]
        legal = SimpleNamespace(
            entries=(SimpleNamespace(finding_codes=frozenset(contract.finding_codes)),)
        )

        self.assertEqual(
            px.readiness(contract, legal_registry=legal, authored_fixtures=fixtures), ()
        )
        self.assertEqual(
            px.readiness(contract, legal_registry=legal, authored_fixtures=fixtures[1:]),
            (f"fixture_not_authored:{fixtures[0]}",),
        )
        self.assertEqual(
            px.readiness(contract, legal_registry=self.legal, authored_fixtures=fixtures),
            tuple(f"finding_code_not_in_legal_registry:{c}" for c in contract.finding_codes),
        )

    def test_new_contract_category_blocks_until_the_contract_has_it(self) -> None:
        contract = px.parse_catalog(self.doc)["paye_reconciliation"]
        blockers = px.readiness(contract, legal_registry=self.legal, authored_fixtures=())
        self.assertIn("contract_category_pending:paye", blockers)

    def test_check_registry_refuses_a_playbook_for_a_blocked_domain(self) -> None:
        catalog = px.parse_catalog(self.doc)
        registry = _registry(
            SimpleNamespace(kind="playbook", name="withholding_review"),
            SimpleNamespace(kind="playbook", name="refund_lanes"),
            SimpleNamespace(kind="tool", name="paye_reconciliation"),
        )
        violations = px.check_registry(
            registry, catalog, legal_registry=self.legal, authored_fixtures=()
        )
        self.assertEqual(set(violations), {"withholding_review"})
        self.assertIn("legal_review_pending", violations["withholding_review"])

    def test_authored_fixture_ids_read_a_synthetic_fixture_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixtures.json"
            self.assertEqual(px.authored_fixture_ids(path), frozenset())
            path.write_text(json.dumps({"synthetic": True, "fixtures": [{"id": "TAX080-PAYE-01"}]}))
            self.assertEqual(px.authored_fixture_ids(path), {"TAX080-PAYE-01"})
            path.write_text(json.dumps({"synthetic": False, "fixtures": []}))
            with self.assertRaises(px.CatalogError):
                px.authored_fixture_ids(path)

    def test_allowlists_match_the_contract_and_legal_registry(self) -> None:
        spec = json.loads((ROOT / "integration/api/openapi.json").read_text(encoding="utf-8"))
        finding = spec["components"]["schemas"]["Finding"]["properties"]
        self.assertEqual(set(finding["category"]["enum"]), px.CONTRACT_CATEGORIES)
        self.assertLessEqual(
            {"missing_evidence", "contradictory_evidence"}, set(finding["kind"]["enum"])
        )
        # The shipped legal registry holds placeholders only and satisfies no domain code yet.
        registered = {c for e in self.legal.entries for c in e.finding_codes}
        codes = {c for d in px.parse_catalog(self.doc).values() for c in d.finding_codes}
        self.assertFalse(registered & codes)


@unittest.skipUnless(HAS_LANGGRAPH, "LangGraph not installed")
class RegistryIntegrationTests(unittest.TestCase):
    def test_extension_allowlists_are_subsets_of_the_playbook_registry(self) -> None:
        from ai.workflows import playbook_registry as pr

        self.assertLessEqual(px.OPERATIONS, pr.OPERATIONS)
        self.assertNotIn("apply_approved_action", px.OPERATIONS)
        self.assertLessEqual(px.PROVENANCE, pr.PROVENANCE_REQUIREMENTS)
        self.assertFalse(set(px.load_catalog()) & set(pr.PLAYBOOK_BUILDERS))

    def test_shipped_registry_has_no_playbook_for_a_blocked_domain(self) -> None:
        from ai.workflows import playbook_registry as pr

        violations = px.check_registry(
            pr.load_registry(),
            px.load_catalog(),
            legal_registry=load_legal_registry(),
            authored_fixtures=px.authored_fixture_ids(),
        )
        self.assertEqual(violations, {})


if __name__ == "__main__":
    unittest.main()
