"""Behaviour tests for the FtM tax entity mapping and OpenAleph entity ingest (TAX-036).

Entities are checked against integration/followthemoney/ftm_schema_subset.v1.json, which
scripts/ftm_schema_subset.py derives from the locked FollowTheMoney source. The OpenAleph
side uses a fake that models the locked ``POST /api/2/collections/{id}/_bulk`` route
(204 on success, IDs signed per collection, 403 without WRITE).
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "followthemoney"))
sys.path.insert(0, str(ROOT / "integration" / "openaleph"))

import entity_ingest as ei  # noqa: E402
import tax_mapping as tm  # noqa: E402

TENANT = "10000000-0000-4000-8000-000000000001"
OTHER_TENANT = "10000000-0000-4000-8000-000000000002"
EVIDENCE = "50000000-0000-4000-8000-000000000001"
REGISTRY = "50000000-0000-4000-8000-000000000002"
SHA = hashlib.sha256(b"synthetic invoice pdf").hexdigest()
REGISTRY_SHA = hashlib.sha256(b"synthetic registry extract").hexdigest()


def ref(
    evidence_id: str = EVIDENCE, sha: str = SHA, page: int | None = 1, record: str | None = None
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "document_sha256": sha,
        "locator": {"page": page, "record": record},
    }


def registry_row(row: str) -> dict[str, Any]:
    return ref(REGISTRY, REGISTRY_SHA, None, row)


def src(kind: str, rid: str) -> dict[str, str]:
    return {"system": "synthetic_fixture", "record_type": kind, "record_id": rid}


def stake(oid: str, owner: str, pct: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": oid,
        "owner_id": owner,
        "asset_id": "TP-1",
        "percentage": pct,
        "ownership_type": "direct",
        "start_date": "2024-01-01",
        "source": src("ownership", oid),
        **extra,
    }


def bundle() -> dict[str, Any]:
    return {
        "version": "tax_entity_bundle.v1",
        "tenant_id": TENANT,
        "evidence": [
            {
                "evidence_id": EVIDENCE,
                "document_sha256": SHA,
                "file_name": "inv-001.pdf",
                "media_type": "application/pdf",
                "size": 2048,
                "received_on": "2026-01-10",
            },
            {
                "evidence_id": REGISTRY,
                "document_sha256": REGISTRY_SHA,
                "file_name": "registry.csv",
                "media_type": "text/csv",
                "size": 512,
            },
        ],
        "parties": [
            {
                "id": "TP-1",
                "kind": "company",
                "name": "Synthetic Trading (Pty) Ltd",
                "role": "taxpayer",
                "tax_number": "SYN-TIN-001",
                "country": "ls",
                "source": src("taxpayer", "TP-1"),
                "evidence": [registry_row("row-1")],
            },
            {
                "id": "SUP-1",
                "kind": "company",
                "name": "Synthetic Supplier Ltd",
                "source": src("counterparty", "SUP-1"),
            },
            {
                "id": "P-1",
                "kind": "person",
                "name": "Synthetic Director One",
                "first_name": "Synthetic",
                "last_name": "Director",
                "role": "owner",
                "source": src("person", "P-1"),
                "evidence": [registry_row("row-2")],
            },
            {
                "id": "P-2",
                "kind": "person",
                "name": "Synthetic Director Two",
                "role": "owner",
                "source": src("person", "P-2"),
            },
        ],
        "ownership": [
            stake("OWN-1", "P-1", "60", evidence=[registry_row("row-3")]),
            stake("OWN-2", "P-2", "40"),
        ],
        "contracts": [
            {
                "id": "CTR-1",
                "title": "Logistics services",
                "buyer_id": "TP-1",
                "supplier_id": "SUP-1",
                "related_party": False,
                "date": "2025-12-01",
                "source": src("contract", "CTR-1"),
            },
        ],
        "invoices": [
            {
                "id": "INV-1",
                "direction": "purchase",
                "supplier_id": "SUP-1",
                "buyer_id": "TP-1",
                "contract_id": "CTR-1",
                "issue_date": "2026-01-05",
                "currency": "LSL",
                "net_amount": "1000.00",
                "vat_amount": "150.00",
                "gross_amount": "1150.00",
                "source": src("invoice", "INV-1"),
                "evidence": [ref(page=2)],
            },
        ],
        "payments": [
            {
                "id": "PAY-1",
                "invoice_id": "INV-1",
                "payer_id": "TP-1",
                "beneficiary_id": "SUP-1",
                "amount": "1150.00",
                "currency": "LSL",
                "date": "2026-01-20",
                "source": src("payment", "PAY-1"),
            },
        ],
    }


def second_payment(data: dict[str, Any]) -> None:
    data["payments"].append(
        {**data["payments"][0], "id": "PAY-2", "source": src("payment", "PAY-2")}
    )


def by_schema(result: tm.MappingResult, schema: str) -> list[dict[str, Any]]:
    return [e for e in result.entities if e["schema"] == schema]


class SchemaSubsetTests(unittest.TestCase):
    def test_subset_records_locked_source(self) -> None:
        lock = json.loads((ROOT / "integration/upstreams.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(tm.SUBSET["source"]["commit"], lock["followthemoney"]["commit"])

    def test_every_range_schema_is_described(self) -> None:
        for schema in tm.SUBSET["schemata"].values():
            for prop in schema["properties"].values():
                if prop["type"] == "entity":
                    self.assertIn(prop["range"], tm.SUBSET["range_schemata"])


class MappingTests(unittest.TestCase):
    def test_maps_each_record_kind_to_supported_schemata(self) -> None:
        result = tm.map_bundle(bundle())
        counts = {s: len(by_schema(result, s)) for s in tm.SUBSET["schemata"]}
        expected = {
            "Company": 2,
            "Person": 2,
            "LegalEntity": 0,
            "Ownership": 2,
            "Contract": 1,
            "ContractAward": 1,
            "UnknownLink": 1,
            "Payment": 1,
            "Document": 2,
            # TAX-074 scheme graph schemata; a TAX-036 bundle never emits them.
            "Directorship": 0,
            "Representation": 0,
            "Project": 0,
            "ProjectParticipant": 0,
        }
        self.assertEqual(counts, expected)
        self.assertEqual(result.conflicts, [])

    def test_relationships_point_at_the_right_parties(self) -> None:
        result = tm.map_bundle(bundle())
        taxpayer = tm.entity_id(TENANT, "party", "TP-1")
        supplier = tm.entity_id(TENANT, "party", "SUP-1")
        invoice = by_schema(result, "UnknownLink")[0]["properties"]
        self.assertEqual((invoice["subject"], invoice["object"]), ([supplier], [taxpayer]))
        self.assertEqual(invoice["role"], ["Purchase invoice"])
        payment = by_schema(result, "Payment")[0]["properties"]
        self.assertEqual((payment["payer"], payment["beneficiary"]), ([taxpayer], [supplier]))
        self.assertEqual(payment["contract"], [tm.entity_id(TENANT, "contract", "CTR-1")])
        self.assertEqual(
            by_schema(result, "ContractAward")[0]["properties"]["supplier"], [supplier]
        )

    def test_tax_amounts_and_provenance_live_in_the_extension(self) -> None:
        result = tm.map_bundle(bundle())
        inv_id = tm.entity_id(TENANT, "invoice", "INV-1")
        ext = result.extension(inv_id)
        self.assertEqual(ext["version"], "vati_tax_extension.v1")
        self.assertEqual(ext["tax"]["vat_amount"], "150.00")
        self.assertEqual(ext["source"], src("invoice", "INV-1"))
        self.assertEqual(ext["evidence"], [ref(page=2)])
        doc_id = tm.entity_id(TENANT, "evidence", EVIDENCE)
        self.assertEqual(result.entity(inv_id)["properties"]["proof"], [doc_id])
        # contentHash is a checksum: OpenAleph strips it from non-admin bulk writes.
        self.assertNotIn("contentHash", result.entity(doc_id)["properties"])
        self.assertEqual(result.extension(doc_id)["tax"]["document_sha256"], SHA)

    def test_ids_are_tenant_scoped(self) -> None:
        other = bundle()
        other["tenant_id"] = OTHER_TENANT
        ids_a = {e["id"] for e in tm.map_bundle(bundle()).entities}
        ids_b = {e["id"] for e in tm.map_bundle(other).entities}
        self.assertFalse(ids_a & ids_b)

    def test_synthetic_scenarios_map_and_validate(self) -> None:
        path = ROOT / "data/synthetic/tax024_records.json"
        for record in json.loads(path.read_text(encoding="utf-8")):
            with self.subTest(case=record["case_id"]):
                result = tm.map_bundle(tm.bundle_from_tax024(record, TENANT))
                self.assertEqual(len(by_schema(result, "UnknownLink")), len(record["invoices"]))
                self.assertEqual(len(by_schema(result, "Payment")), len(record["payments"]))
                related = [
                    x
                    for x in result.extensions
                    if x["kind"] == "contract" and x["tax"]["related_party"]
                ]
                self.assertEqual(len(related), sum(c["related_party"] for c in record["contracts"]))

    def test_non_synthetic_record_is_refused(self) -> None:
        with self.assertRaises(tm.MappingError):
            tm.bundle_from_tax024({"synthetic": False}, TENANT)


class ProvenanceValidationTests(unittest.TestCase):
    def assert_rejected(self, data: dict[str, Any], reason: str) -> tm.MappingError:
        with self.assertRaises(tm.MappingError) as ctx:
            tm.map_bundle(data)
        self.assertEqual(ctx.exception.reason, reason)
        self.assertEqual(ctx.exception.code, "invalid_domain_input")
        return ctx.exception

    def test_hash_that_differs_from_the_stored_original_is_rejected(self) -> None:
        data = bundle()
        data["invoices"][0]["evidence"] = [ref(sha="0" * 64)]
        err = self.assert_rejected(data, "provenance_hash_mismatch")
        self.assertEqual(err.field, "invoices[0].evidence[0].document_sha256")

    def test_citation_of_unknown_evidence_is_rejected(self) -> None:
        data = bundle()
        data["payments"][0]["evidence"] = [ref("50000000-0000-4000-8000-000000000099")]
        self.assert_rejected(data, "unknown_evidence")

    def test_citation_without_locator_is_rejected(self) -> None:
        data = bundle()
        data["invoices"][0]["evidence"] = [ref(page=None, record=None)]
        self.assert_rejected(data, "missing_locator")

    def test_record_without_source_is_rejected(self) -> None:
        data = bundle()
        del data["contracts"][0]["source"]
        self.assert_rejected(data, "missing_provenance")

    def test_person_cannot_be_an_owned_asset(self) -> None:
        data = bundle()
        data["ownership"][0]["asset_id"] = "P-2"
        self.assert_rejected(data, "invalid_ownership_asset")

    def test_unknown_party_and_bad_amount_are_rejected(self) -> None:
        data = bundle()
        data["payments"][0]["payer_id"] = "NOPE"
        self.assert_rejected(data, "unknown_party")
        data = bundle()
        data["invoices"][0]["vat_amount"] = "150"
        self.assert_rejected(data, "invalid_amount")

    def test_duplicate_record_ids_are_rejected(self) -> None:
        data = bundle()
        data["invoices"].append(copy.deepcopy(data["invoices"][0]))
        self.assert_rejected(data, "duplicate")

    def test_validator_catches_unsupported_properties_and_ranges(self) -> None:
        entities = copy.deepcopy(tm.map_bundle(bundle()).entities)
        invoice = next(e for e in entities if e["schema"] == "UnknownLink")
        invoice["properties"]["vatAmount"] = ["150.00"]
        with self.assertRaisesRegex(tm.EntityValidationError, "no property vatAmount"):
            tm.validate_entities(entities)
        entities = copy.deepcopy(tm.map_bundle(bundle()).entities)
        ownership = next(e for e in entities if e["schema"] == "Ownership")
        ownership["properties"]["asset"] = [tm.entity_id(TENANT, "party", "P-2")]
        with self.assertRaisesRegex(tm.EntityValidationError, "Person is not a Asset"):
            tm.validate_entities(entities)
        entities = copy.deepcopy(tm.map_bundle(bundle()).entities)
        entities[0]["schema"] = "Invoice"
        with self.assertRaisesRegex(tm.EntityValidationError, "not allowed"):
            tm.validate_entities(entities)


class OwnershipConflictTests(unittest.TestCase):
    def test_stakes_above_100_percent_are_flagged_not_resolved(self) -> None:
        data = bundle()
        data["ownership"][1]["percentage"] = "55"
        result = tm.map_bundle(data)
        self.assertEqual(len(by_schema(result, "Ownership")), 2)  # both kept
        (conflict,) = result.conflicts
        self.assertEqual(conflict["reason"], "ownership_exceeds_100_percent")
        self.assertEqual(conflict["total_percentage"], "115")
        self.assertEqual(conflict["record_ids"], ["OWN-1", "OWN-2"])
        for eid in conflict["entity_ids"]:
            self.assertEqual(result.extension(eid)["conflict_ids"], [conflict["id"]])

    def test_disagreeing_records_for_the_same_stake_are_flagged(self) -> None:
        data = bundle()
        data["ownership"][1] = stake("OWN-1B", "P-1", "75")
        reasons = sorted(c["reason"] for c in tm.map_bundle(data).conflicts)
        self.assertEqual(
            reasons, ["disagreeing_ownership_records", "ownership_exceeds_100_percent"]
        )

    def test_successive_periods_and_beneficial_stakes_do_not_conflict(self) -> None:
        data = bundle()
        data["ownership"][0]["end_date"] = "2024-12-31"
        data["ownership"][1]["end_date"] = "2024-12-31"
        data["ownership"].append(stake("OWN-3", "P-2", "60", start_date="2025-01-01"))
        data["ownership"].append(
            stake("OWN-4", "P-1", "60", ownership_type="beneficial", start_date=None)
        )
        self.assertEqual(tm.map_bundle(data).conflicts, [])


class FakeOpenAleph:
    """Models ``POST /api/2/collections/{id}/_bulk`` from the locked source."""

    def __init__(self) -> None:
        self.collections: dict[int, dict[str, dict[str, Any]]] = {7: {}}
        self.posts: list[int] = []
        self.fail: list[int] = []

    def __call__(self, method: str, path: str, body: Any = None) -> ei.Response:
        assert method == "POST" and path.endswith("/_bulk"), (method, path)
        collection = int(path.split("/")[4])
        if self.fail:
            return ei.Response(self.fail.pop(0))
        if collection not in self.collections:
            return ei.Response(403)
        self.posts.append(len(body))
        for entity in body:
            self.collections[collection][f"{entity['id']}.{collection}"] = entity
        return ei.Response(204)


class RepeatedIngestTests(unittest.TestCase):
    def test_mapping_is_deterministic(self) -> None:
        first, second = tm.map_bundle(bundle()), tm.map_bundle(bundle())
        self.assertEqual(first.entities, second.entities)
        self.assertEqual(first.digests, second.digests)
        shuffled = bundle()
        shuffled["parties"].reverse()
        shuffled["ownership"].reverse()
        self.assertEqual(tm.map_bundle(shuffled).digests, first.digests)

    def test_second_ingest_sends_nothing(self) -> None:
        fake = FakeOpenAleph()
        result = tm.map_bundle(bundle())
        ledger = ei.ingest(fake, 7, result.entities, result.digests, {})
        self.assertEqual(fake.posts, [len(result.entities)])
        again = tm.map_bundle(bundle())
        self.assertEqual(ei.ingest(fake, 7, again.entities, again.digests, ledger), ledger)
        self.assertEqual(fake.posts, [len(result.entities)])
        self.assertEqual(len(fake.collections[7]), len(result.entities))

    def test_new_records_are_added_without_resending_old_ones(self) -> None:
        fake = FakeOpenAleph()
        result = tm.map_bundle(bundle())
        ledger = ei.ingest(fake, 7, result.entities, result.digests, {})
        data = bundle()
        second_payment(data)
        grown = tm.map_bundle(data)
        ei.ingest(fake, 7, grown.entities, grown.digests, ledger)
        self.assertEqual(fake.posts[-1], 1)

    def test_changed_entity_is_refused_and_nothing_is_sent(self) -> None:
        fake = FakeOpenAleph()
        result = tm.map_bundle(bundle())
        ledger = ei.ingest(fake, 7, result.entities, result.digests, {})
        data = bundle()
        data["invoices"][0]["vat_amount"] = "160.00"
        second_payment(data)
        changed = tm.map_bundle(data)
        with self.assertRaises(ei.IngestError) as ctx:
            ei.ingest(fake, 7, changed.entities, changed.digests, ledger)
        self.assertEqual(ctx.exception.code, "idempotency_conflict")
        self.assertEqual(ctx.exception.entity_ids, [tm.entity_id(TENANT, "invoice", "INV-1")])
        self.assertEqual(len(fake.posts), 1)

    def test_failed_write_leaves_ledger_unchanged_and_is_retryable(self) -> None:
        fake = FakeOpenAleph()
        fake.fail = [503]
        result = tm.map_bundle(bundle())
        with self.assertRaises(ei.IngestError) as ctx:
            ei.ingest(fake, 7, result.entities, result.digests, {})
        self.assertEqual(
            (ctx.exception.code, ctx.exception.retryable), ("upstream_unavailable", True)
        )
        ledger = ei.ingest(fake, 7, result.entities, result.digests, {})
        self.assertEqual(set(ledger), {e["id"] for e in result.entities})

    def test_collection_without_write_access_is_forbidden(self) -> None:
        result = tm.map_bundle(bundle())
        with self.assertRaises(ei.IngestError) as ctx:
            ei.ingest(FakeOpenAleph(), 8, result.entities, result.digests, {})
        self.assertEqual((ctx.exception.code, ctx.exception.retryable), ("forbidden", False))


if __name__ == "__main__":
    unittest.main()
