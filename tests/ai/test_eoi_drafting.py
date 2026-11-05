"""TAX-071 EOI evidence-gap and request drafting (draft only; model-free)."""

import ast
import copy
import json
import unittest
from datetime import date
from pathlib import Path

from ai.tools.evidence_search import SearchDenied
from ai.workflows import eoi_drafting as eoi
from ai.workflows.legal_routes import load_registry, parse_registry

ROOT = Path(__file__).resolve().parents[2]
RECORDS = {
    r["case_id"]: r for r in json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
}
TENANT = "tenant-synthetic"
TODAY = date(2026, 11, 5)
REGISTRY = load_registry()
SPECIALIST = {
    "actor_id": "officer-sp",
    "tenant_id": TENANT,
    "roles": ["specialist"],
    "authenticated": True,
}


def draft(record, actor=SPECIALIST, registry=REGISTRY):
    return eoi.draft_eoi_package(
        record, actor=actor, tenant_id=TENANT, registry=registry, as_of=TODAY
    )


def case_ids(record):
    docs = [record["return"], *record["contracts"], *record["invoices"], *record["payments"]]
    return {d["id"] for d in docs}


class EoiDraftingTests(unittest.TestCase):
    def test_syn004_package_cites_domestic_evidence_and_lists_missing_foreign_facts(self) -> None:
        record = RECORDS["SYN-004"]
        package = draft(record)
        self.assertEqual(package["status"], "draft")
        self.assertEqual(package["for_review_by"], "competent_authority")
        self.assertIs(package["requires_human_approval"], True)
        self.assertIsNone(package["approved_by"])
        self.assertEqual(
            package["transmission"], {"enabled": False, "channel": None, "sent_at": None}
        )
        (party,) = package["counterparties"]
        self.assertEqual(party["counterparty_id"], "SYN-FOREIGN-004")
        self.assertIsNone(party["jurisdiction"])
        self.assertEqual(
            set(party["domestic_evidence_ids"]),
            {"INV-SYN-004-02", "CTR-SYN-004-02", "PAY-SYN-004-02"},
        )
        self.assertTrue(set(party["domestic_evidence_ids"]) <= case_ids(record))
        facts = [f["fact"] for f in party["missing_foreign_facts"]]
        self.assertEqual(
            facts,
            [
                "supplier_jurisdiction",
                "supplier_identity_and_registration",
                "supplier_tax_residence",
                "services_performed_and_location",
                "consideration_received",
                "relationship_to_taxpayer",
            ],
        )
        for fact in party["missing_foreign_facts"]:
            self.assertEqual(fact["status"], "missing")
            self.assertTrue(set(fact["relates_to"]) <= case_ids(record))
        (txn,) = party["transactions"]
        self.assertEqual(txn["gross"], {"amount": "50000.00", "currency": "LSL"})

    def test_open_domestic_questions_are_listed_before_any_foreign_request(self) -> None:
        package = draft(RECORDS["SYN-004"])
        topics = {q["topic"] for q in package["domestic_steps_outstanding"]}
        self.assertEqual(topics, {"vat_treatment", "withholding", "service_performance"})
        self.assertTrue(
            all(q["addressed_to"] == "taxpayer" for q in package["domestic_steps_outstanding"])
        )

    def test_legal_basis_is_never_asserted(self) -> None:
        basis = draft(RECORDS["SYN-004"])["legal_basis"]
        self.assertEqual(basis["status"], "no_verified_route")
        self.assertIsNone(basis["instrument"])
        self.assertEqual(basis["authorities"], [])
        doc = json.loads((ROOT / "ai/workflows/legal_route_registry.synthetic.json").read_text())
        doc["entries"].append(
            {
                "authority_id": "SYN-PLACEHOLDER-AUTH-009",
                "version": "2025.1",
                "title": "Synthetic placeholder: information exchange route",
                "effective_from": "2025-01-01",
                "effective_to": None,
                "verification_status": "unverified",
                "reviewer": None,
                "reviewed_on": None,
                "finding_codes": ["eoi_request"],
                "synthetic": True,
            }
        )
        unverified = draft(RECORDS["SYN-004"], registry=parse_registry(doc))["legal_basis"]
        self.assertEqual(unverified["status"], "unverified_route")
        self.assertIsNone(unverified["instrument"])

    def test_known_domestic_facts_are_not_requested_abroad(self) -> None:
        record = copy.deepcopy(RECORDS["SYN-004"])
        record["contracts"][1]["service_location"] = "SYN remote, delivered online"
        record["invoices"][1]["supplier_jurisdiction"] = "ZA"
        (party,) = draft(record)["counterparties"]
        facts = {f["fact"] for f in party["missing_foreign_facts"]}
        self.assertEqual(party["jurisdiction"], "ZA")
        self.assertNotIn("supplier_jurisdiction", facts)
        self.assertNotIn("services_performed_and_location", facts)
        record["invoices"][1]["supplier_jurisdiction"] = "not a country"
        (party,) = draft(record)["counterparties"]
        self.assertIsNone(party["jurisdiction"])

    def test_cases_without_foreign_counterparties_produce_no_package(self) -> None:
        for case_id in ("SYN-001", "SYN-002", "SYN-003", "SYN-005"):
            with self.subTest(case_id=case_id):
                self.assertIsNone(draft(RECORDS[case_id]))

    def test_no_transmission_tool_is_enabled(self) -> None:
        package = draft(RECORDS["SYN-004"])
        with self.assertRaises(eoi.EoiDraftError) as raised:
            eoi.transmit(package)
        self.assertEqual(raised.exception.code, "transmission_not_enabled")
        tree = ast.parse((ROOT / "ai/workflows/eoi_drafting.py").read_text())
        imported = {
            name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
        }
        self.assertFalse(
            imported & {"urllib", "http", "socket", "smtplib", "requests", "httpx", "ftplib"}
        )

    def test_package_makes_no_accusation_or_enforcement_conclusion(self) -> None:
        package = draft(RECORDS["SYN-004"])
        self.assertIsNone(package["enforcement_conclusion"])
        self.assertEqual(
            package["statutory_treatment"]["status"], "unverified_pending_expert_review"
        )
        text = json.dumps(package).lower()
        for word in ("fraud", "evasion", "abuse", "liable", "penalt", "guilty"):
            self.assertNotIn(word, text)

    def test_access_and_scope_are_enforced(self) -> None:
        cases = {
            "actor_not_authenticated": {**SPECIALIST, "authenticated": False},
            "tenant_not_allowed": {**SPECIALIST, "tenant_id": "tenant-other"},
            "evidence_read_not_allowed": {**SPECIALIST, "roles": ["taxpayer"]},
        }
        for code, actor in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(SearchDenied) as raised:
                    draft(RECORDS["SYN-004"], actor=actor)
                self.assertEqual(raised.exception.code, code)
        record = {**copy.deepcopy(RECORDS["SYN-004"]), "tenant_id": "tenant-other"}
        with self.assertRaises(eoi.EoiDraftError) as raised:
            draft(record)
        self.assertEqual(raised.exception.code, "tenant_mismatch")

    def test_package_id_is_deterministic(self) -> None:
        self.assertEqual(draft(RECORDS["SYN-004"]), draft(RECORDS["SYN-004"]))


if __name__ == "__main__":
    unittest.main()
