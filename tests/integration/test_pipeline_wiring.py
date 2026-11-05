"""TAX-087 pipeline steps through the real adapters, without Frappe or network.

The rules' bundle for a refund claim is mapped by the real TAX-036 ``tax_mapping``
(entities must validate against the locked FtM subset), written by the real TAX-036
ingest, screened by the real TAX-037 matcher, and converted by the real TAX-038 Docling
adapter resuming a task kept outside it (the outbox effect ledger in production). The
transports are ``vati_tax.pipeline.fakes``; ``down`` fakes give the unavailable path.
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
for name in ("jube", "docling", "openaleph", "followthemoney", "yente"):
    sys.path.insert(0, str(ROOT / "integration" / name))

import candidate_review  # noqa: E402
import entity_ingest  # noqa: E402
import extraction_adapter  # noqa: E402
import tax_mapping  # noqa: E402
from vati_tax.integrations import config  # noqa: E402
from vati_tax.outbox import policy as outbox_policy  # noqa: E402
from vati_tax.pipeline import fakes, rules, scenarios  # noqa: E402

TENANT = "10000000-0000-4000-8000-000000000087"
EVIDENCE = "70000000-0000-4000-8000-0000000000a1"
CONF = fakes.FAKE_SERVICES


def scenario_bundle(case: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    """The SYN scenario as the Frappe records the portal stores, mapped by the rules."""
    scenario = next(s for s in scenarios.SCENARIOS if s["case"] == case)
    docs: dict[str, bytes] = {}
    evidence = []

    def add(content: bytes, kind: str, index: int) -> str:
        evidence_id = f"70000000-0000-4000-8000-{index:012d}"
        docs[evidence_id] = content
        evidence.append(
            {"evidence_id": evidence_id, "document_sha256": hashlib.sha256(content).hexdigest(),
             "media_type": "text/csv", "size_bytes": len(content),
             "original_filename": f"{kind}-{index}.csv", "document_type": kind,
             "received_at": "2026-11-05 10:00:00"}
        )  # fmt: skip
        return evidence_id

    contracts = {c: add(scenarios.contract_document(c, s), "contract", i)
                 for i, (c, s) in enumerate(scenario["contracts"].items(), start=1)}  # fmt: skip
    rows = []
    for day, invoice in enumerate(scenario["invoices"], start=1):
        evidence_id = add(scenarios.invoice_document(invoice), "invoice", 100 + day)
        row = scenarios.schedule_row(scenario, day, invoice, evidence_id, contracts[invoice[1]])
        rows.append({**row, "name": f"row{day:04d}", "idx": day})
    taxpayer = {"name": "90000000-0000-4000-8000-000000000087",
                "display_name": f"Synthetic taxpayer {scenario['taxpayer']}",
                "taxpayer_type": "company", "tin": "400000087"}  # fmt: skip
    bundle = rules.claim_bundle(tenant_id=TENANT, taxpayer=taxpayer, currency="LSL",
                                rows=rows, evidence=evidence)  # fmt: skip
    return bundle, docs


class MappingTests(unittest.TestCase):
    def test_both_scenarios_map_to_valid_ftm_entities(self):
        for case, parties, invoices in (("SYN-001", 2, 2), ("SYN-002", 3, 2)):
            bundle, docs = scenario_bundle(case)
            mapped = tax_mapping.map_bundle(bundle)  # validates against the locked subset
            schemata = [e["schema"] for e in mapped.entities]
            self.assertEqual(len(rules.screening_targets(mapped.entities)), parties, case)
            self.assertEqual(schemata.count("UnknownLink"), invoices, case)
            self.assertEqual(len(rules.document_entities(mapped.entities)), len(docs), case)
            self.assertEqual(schemata.count("Payment"), invoices, case)
            # Every invoice amount cites the stored document by its SHA-256.
            for ext in mapped.extensions:
                if ext["kind"] == "invoice":
                    self.assertTrue(ext["evidence"])
                    for ref in ext["evidence"]:
                        self.assertEqual(
                            ref["document_sha256"],
                            hashlib.sha256(docs[ref["evidence_id"]]).hexdigest(),
                        )
            # Mapping again gives the same entities and digests (replay-safe ledger).
            self.assertEqual(tax_mapping.map_bundle(bundle).digests, mapped.digests)


class IngestTests(unittest.TestCase):
    def test_ingest_sends_only_new_entities(self):
        bundle, _ = scenario_bundle("SYN-002")
        mapped = tax_mapping.map_bundle(bundle)
        fake = fakes.FakeOpenAleph()
        ingest = config.openaleph_ingest(CONF, transport=fake)
        self.assertEqual(ingest.collection_id, fakes.FAKE_COLLECTION)
        plan = rules.index_plan(mapped.entities, mapped.digests, {})
        ledger = ingest.ingest(plan.new, mapped.digests, {})
        self.assertEqual(set(ledger), {e["id"] for e in mapped.entities})
        self.assertEqual(fake.writes, 1)
        # A replay with the ledger sends nothing.
        again = rules.index_plan(mapped.entities, mapped.digests, ledger)
        self.assertEqual(again.new, [])
        self.assertEqual(ingest.ingest(again.new, mapped.digests, ledger), ledger)
        self.assertEqual(fake.writes, 1)

    def test_unavailable_openaleph_is_retryable(self):
        bundle, _ = scenario_bundle("SYN-001")
        mapped = tax_mapping.map_bundle(bundle)
        ingest = config.openaleph_ingest(CONF, transport=fakes.FakeOpenAleph(down=True))
        with self.assertRaises(entity_ingest.IngestError) as caught:
            ingest.ingest(mapped.entities, mapped.digests, {})
        failure = rules.classify("openaleph", caught.exception)
        self.assertIsInstance(failure, outbox_policy.RetryableDeliveryError)
        self.assertEqual(failure.code, "openaleph_upstream_unavailable")


class ScreeningTests(unittest.TestCase):
    def test_parties_screened_on_the_indexed_dataset_version(self):
        bundle, _ = scenario_bundle("SYN-002")
        targets = rules.screening_targets(tax_mapping.map_bundle(bundle).entities)
        matcher = config.yente_matcher(CONF, transport=fakes.FakeYente())
        version = matcher.dataset_version()
        self.assertEqual(version.version, fakes.FAKE_DATASET_VERSION)
        sets = matcher.match(targets, retrieved_at="2026-11-05T10:00:00Z")
        counts = {s.query_entity_id: len(s.candidates) for s in sets.values()}
        self.assertEqual(sorted(counts.values()), [0, 0, 1])  # only the group counterparty
        found = next(s for s in sets.values() if s.candidates)
        contract = candidate_review.to_contract(found.candidates[0], None)
        self.assertEqual(contract["review_status"], "unreviewed")  # yente never decides
        self.assertIsNone(contract["reviewed_by"])

    def test_unavailable_yente_is_retryable(self):
        matcher = config.yente_matcher(CONF, transport=fakes.FakeYente(down=True))
        with self.assertRaises(candidate_review.ReviewError) as caught:
            matcher.dataset_version()
        self.assertIsInstance(rules.classify("yente", caught.exception),
                              outbox_policy.RetryableDeliveryError)  # fmt: skip


class DoclingTests(unittest.TestCase):
    def adapter(self, fake: fakes.FakeDocling, recorded: list[dict[str, Any]]) -> Any:
        def sink(**kwargs: Any) -> dict[str, Any]:
            recorded.append(kwargs)
            return {"extraction_id": "80000000-0000-4000-8000-000000000001",
                    "status": kwargs["result"]["status"]}  # fmt: skip

        return config.docling_extraction(CONF, jobs=extraction_adapter.InMemoryJobStore(),
                                         sink=sink, transport=fake)  # fmt: skip

    def test_submit_once_then_resume_the_kept_task(self):
        content = scenarios.invoice_document(scenarios.SCENARIOS[0]["invoices"][0])
        sha = hashlib.sha256(content).hexdigest()
        fake, recorded = fakes.FakeDocling(), []
        adapter = self.adapter(fake, recorded)
        task_id = adapter.submit(evidence_id=EVIDENCE, media_type="text/csv", content=content)
        # The handler keeps task_id durably, then hands it to extract through the job store.
        key = extraction_adapter.job_key(TENANT, EVIDENCE, sha, adapter.config.extractor_version)
        adapter._jobs.put(key, task_id)
        outcome = adapter.extract(tenant_id=TENANT, evidence_id=EVIDENCE, document_sha256=sha,
                                  media_type="text/csv", content=content,
                                  correlation_id="corr-1")  # fmt: skip
        self.assertTrue(outcome.resumed)
        self.assertEqual(fake.submissions, 1)  # no second conversion
        self.assertEqual(outcome.status, "success")
        self.assertEqual(recorded[0]["document_sha256"], sha)
        self.assertTrue(recorded[0]["result"]["items"])

    def test_refused_and_lost_tasks_are_retryable_with_known_reasons(self):
        content = b"synthetic,true\n"
        down = self.adapter(fakes.FakeDocling(down=True), [])
        with self.assertRaises(extraction_adapter.DoclingAdapterError) as refused:
            down.submit(evidence_id=EVIDENCE, media_type="text/csv", content=content)
        self.assertEqual(refused.exception.reason, "upstream_status")  # handler forgets the effect
        self.assertEqual(rules.classify("docling", refused.exception).code,
                         "docling_upstream_status")  # fmt: skip
        fake = fakes.FakeDocling()
        adapter = self.adapter(fake, [])
        task_id = adapter.submit(evidence_id=EVIDENCE, media_type="text/csv", content=content)
        sha = hashlib.sha256(content).hexdigest()
        adapter._jobs.put(extraction_adapter.job_key(TENANT, EVIDENCE, sha,
                                                     adapter.config.extractor_version), task_id)  # fmt: skip
        fake.lose_tasks = True
        with self.assertRaises(extraction_adapter.DoclingAdapterError) as lost:
            adapter.extract(tenant_id=TENANT, evidence_id=EVIDENCE, document_sha256=sha,
                            media_type="text/csv", content=content, correlation_id="c")  # fmt: skip
        self.assertEqual(lost.exception.reason, "task_lost")
        self.assertTrue(rules.classify("docling", lost.exception).retryable)

    def test_unsupported_media_is_refused_before_any_call(self):
        fake = fakes.FakeDocling()
        with self.assertRaises(extraction_adapter.DoclingAdapterError):
            self.adapter(fake, []).submit(evidence_id=EVIDENCE, media_type="text/html",
                                          content=b"x")  # fmt: skip
        self.assertEqual(fake.submissions, 0)


if __name__ == "__main__":
    unittest.main()
