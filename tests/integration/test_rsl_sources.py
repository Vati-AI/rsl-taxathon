"""TAX-111 RSL source contracts and fixture adapter (no network, synthetic data)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.rsl_sources.contracts import (  # noqa: E402
    CONTRACTS,
    SourceContractError,
    contract_for,
    validate_record,
)
from integration.rsl_sources.fixture_source import (  # noqa: E402
    FixtureRslSources,
    SourceAccessError,
    asycuda_entries_from_tax073,
)

TENANT = "SYN-TENANT-RSL"
RETURN = {
    "return_id": "VAT-1",
    "tin": "510000006",
    "period": "2026-09",
    "standard_rated_sales": "100.00",
    "zero_rated_sales": "0.00",
    "exempt_sales": "0.00",
    "output_vat": "15.00",
    "input_vat_claimed": "30.00",
    "imported_services_vat_declared": "0.00",
    "refund_claim": "15.00",
    "filed_on": "2026-10-19",
}
SCORE = {
    "claim_id": "ORA-1",
    "tin": "510000006",
    "score": "0.4200",
    "model_id": "RSL-REFUND-FRAUD-STANDIN",
    "model_version": "fixture-1",
    "owner": "RSL",
}


def sources(**kw):
    return FixtureRslSources(
        TENANT,
        {("etax", "vat_return"): [RETURN], ("rsl_fraud_model", "score"): [SCORE]},
        retrieved_at="2026-11-06T08:00:00Z",
        **kw,
    )


class ContractTests(unittest.TestCase):
    def test_every_contract_is_marked_as_an_assumption(self):
        for contract in CONTRACTS.values():
            self.assertTrue(contract.contract_id.endswith("-assumed"), contract.contract_id)

    def test_float_money_missing_and_unexpected_fields_are_refused(self):
        contract = contract_for("etax", "vat_return")
        for change, code in (
            ({"output_vat": 15.0}, "float_money"),
            ({"output_vat": "15.001"}, "invalid_field"),
            ({"extra": "x"}, "unexpected_field"),
        ):
            with self.subTest(change=change):
                with self.assertRaises(SourceContractError) as ctx:
                    validate_record(contract, {**RETURN, **change})
                self.assertEqual(ctx.exception.code, code)
        missing = dict(RETURN)
        del missing["refund_claim"]
        with self.assertRaises(SourceContractError) as ctx:
            validate_record(contract, missing)
        self.assertEqual(ctx.exception.code, "missing_field")

    def test_rsl_score_stays_rsl_owned(self):
        with self.assertRaises(SourceContractError):
            validate_record(contract_for("rsl_fraud_model", "score"), {**SCORE, "owner": "Vati"})


class FixtureSourceTests(unittest.TestCase):
    def test_envelope_identifies_fixture_and_keeps_references_not_copies(self):
        env = sources().read("etax", "vat_return", tenant_id=TENANT, taxpayer="510000006")
        self.assertTrue(env["fixture"])
        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["contract_id"], "rsl.etax.vat_return.v0-assumed")
        ref = env["references"][0]
        self.assertEqual(ref["record_id"], "VAT-1")
        self.assertEqual(len(ref["record_sha256"]), 64)

    def test_reads_are_scoped_to_tenant_and_taxpayer(self):
        src = sources()
        with self.assertRaises(SourceAccessError):
            src.read("etax", "vat_return", tenant_id="OTHER", taxpayer="510000006")
        other = src.read("etax", "vat_return", tenant_id=TENANT, taxpayer="520000001")
        self.assertEqual(other["records"], [])

    def test_unavailable_source_returns_no_invented_records(self):
        env = sources(unavailable=["rsl_fraud_model"]).read(
            "rsl_fraud_model", "score", tenant_id=TENANT, taxpayer="510000006"
        )
        self.assertEqual(env["status"], "unavailable")
        self.assertEqual(env["records"], [])

    def test_duplicate_source_ids_are_refused(self):
        with self.assertRaises(SourceContractError):
            FixtureRslSources(
                TENANT,
                {("etax", "vat_return"): [RETURN, RETURN]},
                retrieved_at="2026-11-06T08:00:00Z",
            )

    def test_tax073_customs_values_map_to_asycuda_contract(self):
        cases = json.loads((ROOT / "data/synthetic/tax073_customs_records.json").read_text())
        entries = asycuda_entries_from_tax073(cases, "510000006")
        self.assertTrue(entries)
        for entry in entries:
            validate_record(contract_for("asycuda", "entry"), entry)


if __name__ == "__main__":
    unittest.main()
