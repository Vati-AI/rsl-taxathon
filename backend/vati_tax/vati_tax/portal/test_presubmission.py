"""Behaviour and boundary tests for TAX-044 pre-submission reconciliation.

Runs without Frappe. The shared TAX-048 module is imported from the repository
root (``scripts/dev.py check`` runs from there); if it is not importable the
reconciliation tests are skipped and the boundary tests still run.
"""

import ast
import datetime
import json
import pathlib
import sys
import unittest
import uuid
from decimal import Decimal

from vati_tax.portal import presubmission
from vati_tax.submissions import invoices

APP = pathlib.Path(__file__).resolve().parents[1]  # the vati_tax app package
REPO = APP.parents[2]  # repository root in a checkout (not inside the bench container)
STDLIB_ALLOWED = {"re", "collections.abc", "decimal", "typing", "__future__"}
# Words that would turn guidance into an accusation or a decision (acceptance 2).
FORBIDDEN = ("fraud", "abuse", "suspic", "penalt", "evasion", "reject", "denied", "illegal",
             "investigat", "lane", "risk", "related party", "offence")  # fmt: skip

TODAY = datetime.date(2026, 11, 5)
DOC = str(uuid.uuid4())

if str(REPO) not in sys.path:
    sys.path.append(str(REPO))
try:
    import ai.tools.refund_reconciliation as shared_module

    SHARED = True
    SHARED_FILE = pathlib.Path(shared_module.__file__)
except ImportError:  # pragma: no cover - shared module not on the path
    SHARED = False
    SHARED_FILE = REPO / "ai" / "tools" / "refund_reconciliation.py"


def rows(*specs):
    out = []
    for n, spec in enumerate(specs):
        base = {
            "invoice_number": f"INV-{n}",
            "invoice_date": "2026-09-10",
            "supplier_name": "Synthetic Supplies",
            "net_amount": "10000.00",
            "vat_amount": "1500.00",
            "gross_amount": "11500.00",
            "invoice_evidence_id": DOC,
        }
        base.update(spec)
        out.append(base)
    return invoices.parse_schedule(out, period_end=datetime.date(2026, 9, 30), today=TODAY)


class BoundaryTests(unittest.TestCase):
    @unittest.skipUnless(SHARED_FILE.is_file(), "shared module source not available here")
    def test_shared_module_imports_only_the_standard_library(self):
        tree = ast.parse(SHARED_FILE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertLessEqual(imported, STDLIB_ALLOWED, "TAX-044 relies on a stdlib-only module")

    def test_only_the_adapter_reaches_into_ai(self):
        offenders = []
        for path in APP.rglob("*.py"):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            uses_ai = "import ai" in text or "from ai" in text or '"ai.' in text
            if uses_ai and path.name != "presubmission.py":
                offenders.append(str(path.relative_to(APP)))
        self.assertEqual(offenders, [])
        self.assertEqual(presubmission.SHARED_MODULE, "ai.tools.refund_reconciliation")

    def test_lane_proposal_is_never_used(self):
        source = (APP / "portal" / "presubmission.py").read_text(encoding="utf-8")
        self.assertNotIn("propose_refund_lane(", source)


class RecordTests(unittest.TestCase):
    def test_record_maps_rows_payments_and_contracts(self):
        contract = str(uuid.uuid4())
        record = presubmission.build_record(
            Decimal("1500.00"), Decimal("0.00"), Decimal("1500.00"),
            rows({"amount_paid": "11500.00", "contract_evidence_id": contract},
                 {"invoice_number": "X"}),
        )  # fmt: skip
        self.assertEqual(record["return"]["refund_claim"], "1500.00")
        self.assertEqual([i["contract_id"] for i in record["invoices"]], ["contract-0", None])
        self.assertEqual(record["contracts"], [{"id": "contract-0"}])
        self.assertEqual(record["payments"],
                         [{"id": "payment-0", "invoice_id": "invoice-0", "amount": "11500.00"}])  # fmt: skip
        self.assertTrue(all(i["direction"] == "purchase" for i in record["invoices"]))


@unittest.skipUnless(SHARED, "ai.tools.refund_reconciliation is not importable here")
class ReviewTests(unittest.TestCase):
    def test_fully_evidenced_claim_is_complete(self):
        result = presubmission.review(
            Decimal("1500.00"), Decimal("0.00"), Decimal("1500.00"),
            rows({"amount_paid": "11500.00", "contract_evidence_id": str(uuid.uuid4())}),
        )  # fmt: skip
        self.assertTrue(result["complete"])
        self.assertEqual(result["figures"]["explained_amount"], "1500.00")
        self.assertEqual(result["figures"]["unresolved_amount"], "0.00")

    def test_incomplete_evidence_gives_guidance_not_accusations(self):
        # Acceptance 2: missing invoices, payments and contracts, plus an
        # arithmetic mismatch, produce correction prompts only.
        result = presubmission.review(
            Decimal("5000.00"), Decimal("200.00"), Decimal("1500.00"), rows({})
        )
        codes = {p["code"] for p in result["prompts"]}
        self.assertEqual(
            codes,
            {"refund_claim_exceeds_documented_input_vat", "return_arithmetic_mismatch",
             "payment_evidence_missing", "contract_evidence_missing"},
        )  # fmt: skip
        self.assertFalse(result["complete"])
        text = json.dumps(result).lower()
        for word in FORBIDDEN:
            self.assertNotIn(word, text, word)
        missing = next(p for p in result["prompts"]
                       if p["code"] == "refund_claim_exceeds_documented_input_vat")  # fmt: skip
        self.assertIn("LSL 3,700.00", missing["message"])

    def test_indicators_and_lane_never_reach_the_taxpayer(self):
        fake = {
            "calculation_version": "v",
            "claimed_refund": {"amount": "1.00", "currency": "LSL"},
            "gaps": [], "line_items": [],
            "discrepancies": [{"code": "some_new_internal_code", "evidence_ids": []}],
            "indicators": [{"code": "related_party_transaction", "evidence_ids": []}],
            "proposed_lane": "targeted",
        }  # fmt: skip
        view = presubmission.taxpayer_review(fake, [])
        self.assertEqual(set(view), {"calculation_version", "figures", "prompts", "complete"})
        self.assertEqual(view["prompts"], [])
        self.assertNotIn("related_party", json.dumps(view))
        self.assertNotIn("targeted", json.dumps(view))

    def test_every_prompt_template_uses_neutral_wording(self):
        for template in [*presubmission.CLAIM_PROMPTS.values(),
                         *presubmission.INVOICE_PROMPTS.values()]:  # fmt: skip
            for word in FORBIDDEN:
                self.assertNotIn(word, template.lower(), (word, template))


if __name__ == "__main__":
    unittest.main()
