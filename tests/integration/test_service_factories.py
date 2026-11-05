"""Site-config factories for the integration/ adapters (TAX-085). No network.

``vati_tax.integrations.config`` builds the real adapters from the ``vati_services``
site config that ``scripts/frappe_dev.py init`` writes. The adapter directories are put
on the path the way the bench's ``vati_shared.pth`` does.
"""

from __future__ import annotations

import datetime
import sys
import unittest
import urllib.error
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for name in ("jube", "docling", "openaleph", "followthemoney", "yente"):
    sys.path.insert(0, str(ROOT / "integration" / name))

import candidate_review  # noqa: E402
import case_lifecycle as cl  # noqa: E402
import entity_ingest  # noqa: E402
import extraction_adapter  # noqa: E402
import risk_invoke  # noqa: E402
import test_jube_case_lifecycle as jube_fixture  # noqa: E402
from vati_tax.decisions import engine  # noqa: E402
from vati_tax.identity import policy  # noqa: E402
from vati_tax.integrations import config  # noqa: E402

TENANT = jube_fixture.TENANT
ADAPTER_KEY = "case-adapter-" + "a" * 32
RISK_KEY = "risk-invoker-" + "b" * 32
DOCLING_KEY = "docling-" + "c" * 32
ALEPH_KEY = "aleph-" + "d" * 32
MODEL_GUID = "50000000-0000-4000-8000-000000000001"


def services(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "jube": {
            "base_url": "http://host.docker.internal:55501",
            "case_adapter_api_key": ADAPTER_KEY,
            "vati_tenant_id": TENANT,
            "workflow_guid": jube_fixture.WORKFLOW_GUID,
            "workflow_version": jube_fixture.WORKFLOW.version,
            "status_guids": dict(jube_fixture.STATUS_GUIDS),
            "risk_invoker_api_key": RISK_KEY,
            "risk_model_guid": MODEL_GUID,
        },
        "docling": {"base_url": "http://host.docker.internal:55001", "api_key": DOCLING_KEY},
        "openaleph": {
            "base_url": "http://host.docker.internal:55580",
            "api_key": ALEPH_KEY,
            "collection_id": "3",
        },
        "yente": {"base_url": "http://host.docker.internal:55800", "dataset": "vati_synthetic"},
    }
    value.update(overrides)
    return {config.SERVICES_KEY: value}


class LinkTable:
    """``ids.mapping.record_link`` / ``case_service_links`` as plain callables."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record_link(self, **row: Any) -> dict[str, Any]:
        self.rows.append(row)
        return row

    def case_service_links(self, *, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
        return [r for r in self.rows if (r["tenant_id"], r["case_id"]) == (tenant_id, case_id)]


def lifecycle(conf: dict[str, Any], links: LinkTable | None = None, transport: Any = None) -> Any:
    links = links or LinkTable()
    return config.case_lifecycle(
        conf,
        record_link=links.record_link,
        case_service_links=links.case_service_links,
        transport=transport,
    )


class NotConfiguredTests(unittest.TestCase):
    def assert_not_configured(self, fn: Any, reason: str) -> config.ServiceNotConfigured:
        with self.assertRaises(config.ServiceNotConfigured) as caught:
            fn()
        error = caught.exception
        self.assertEqual(
            (error.code, error.http_status, error.retryable), ("upstream_unavailable", 503, True)
        )
        self.assertEqual(error.reason, reason)
        self.assertEqual(error.field_errors[0]["code"], reason)
        for secret in (ADAPTER_KEY, RISK_KEY, DOCLING_KEY, ALEPH_KEY):
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error.as_error("corr")))
        return error

    def test_missing_jube_section_is_jube_not_configured_as_before(self):
        # Same reason and field the decision store used before TAX-085.
        for conf in ({}, {config.SERVICES_KEY: {}}, {config.SERVICES_KEY: {"jube": {}}}):
            with self.subTest(conf=conf):
                error = self.assert_not_configured(lambda: lifecycle(conf), "jube_not_configured")
                self.assertEqual(error.field_errors[0]["field"], "case_id")

    def test_unusable_jube_values_are_not_configured_not_guessed(self):
        jube = services()[config.SERVICES_KEY]["jube"]
        for change in (
            {"case_adapter_api_key": ""},
            {"case_adapter_api_key": "short"},
            {"base_url": "http://jube.example.test"},
            {"vati_tenant_id": "not-a-uuid"},
            {"status_guids": {}},
            {"status_guids": ["not", "a", "mapping"]},
        ):
            with self.subTest(change=change):
                conf = services(jube={**jube, **change})
                self.assert_not_configured(lambda: lifecycle(conf), "jube_not_configured")

    def test_status_guids_for_another_workflow_version_are_refused(self):
        jube = services()[config.SERVICES_KEY]["jube"]
        conf = services(jube={**jube, "workflow_version": "0.0.1"})
        self.assert_not_configured(lambda: lifecycle(conf), "jube_workflow_mismatch")

    def test_each_other_service_reports_its_own_missing_configuration(self):
        jube = services()[config.SERVICES_KEY]["jube"]
        no_risk = services(jube={k: v for k, v in jube.items() if k != "risk_model_guid"})
        cases = [
            (lambda: config.risk_evaluator(no_risk), "jube_risk_not_configured"),
            (lambda: config.docling_extraction({}, jobs=None, sink=None), "docling_not_configured"),
            (
                lambda: config.docling_extraction(
                    services(docling={"base_url": "http://127.0.0.1:55001", "api_key": "x"}),
                    jobs=None,
                    sink=None,
                ),
                "docling_not_configured",
            ),
            (lambda: config.openaleph_ingest({}), "openaleph_not_configured"),
            (
                lambda: config.openaleph_ingest(
                    services(openaleph={"base_url": "http://a", "api_key": "k", "collection_id": 0})
                ),
                "openaleph_not_configured",
            ),
            (lambda: config.yente_matcher({}), "yente_not_configured"),
            (
                lambda: config.yente_matcher(services(yente={"base_url": "http://203.0.113.5"})),
                "yente_not_configured",
            ),
        ]
        for fn, reason in cases:
            with self.subTest(reason=reason):
                self.assert_not_configured(fn, reason)

    def test_adapter_code_missing_from_the_bench_path_is_reported(self):
        with self.assertRaises(config.ServiceNotConfigured) as caught:
            config.adapter_module("no_such_vati_adapter", "jube")
        self.assertEqual(caught.exception.reason, "adapter_not_installed")

    def test_configured_reports_presence_only(self):
        report = config.configured(services())
        self.assertEqual(set(report.values()), {True})
        self.assertEqual(config.configured({}), dict.fromkeys(report, False))
        self.assertNotIn(ADAPTER_KEY, repr(report))


class RealAdapterTests(unittest.TestCase):
    def test_case_lifecycle_is_the_real_jube_adapter_on_the_service_link_store(self):
        links = LinkTable()
        adapter = lifecycle(services(), links, transport=jube_fixture.FakeJube())
        self.assertIsInstance(adapter, cl.JubeCaseAdapter)
        self.assertIsInstance(adapter.links, cl.MappingLinkStore)
        self.assertEqual(adapter.config.base_url, "http://host.docker.internal:55501")
        self.assertNotIn(ADAPTER_KEY, repr(adapter.config))

        case_id = str(uuid.uuid4())
        adapter._send.archived.add(case_id)
        adapter.ensure_case(TENANT, case_id, correlation_id=jube_fixture.CORR)
        self.assertEqual(
            [(r["service"], r["external_type"]) for r in links.rows], [("jube", "case")]
        )
        case = adapter.get_case(TENANT, case_id, correlation_id=jube_fixture.CORR)
        self.assertEqual((case.status, case.status_authority), ("RECEIVED", "jube"))

    def test_a_decision_runs_through_the_factory_built_adapter(self):
        """The factory's adapter satisfies decisions.engine.CaseLifecycle end to end."""
        jube = jube_fixture.FakeJube()
        adapter = lifecycle(services(), transport=jube)
        case_id = str(uuid.uuid4())
        jube.archived.add(case_id)
        corr = jube_fixture.CORR
        adapter.ensure_case(TENANT, case_id, correlation_id=corr)
        adapter.transition(
            TENANT,
            case_id,
            "AWAITING_HUMAN",
            expected_status="RECEIVED",
            correlation_id=corr,
            rating=2,
        )
        repo = engine.MemoryRepository()
        repo.cases[(TENANT, case_id)] = engine.CaseRef(TENANT, case_id, str(uuid.uuid4()))
        now = datetime.datetime(2026, 11, 5, 10, 0, tzinfo=datetime.UTC)

        def ctx(user: str, role: policy.Role) -> engine.Context:
            scope = policy.TenantScope(
                user=user, tenant_id=TENANT, roles=frozenset({role}), taxpayer_ids=frozenset()
            )
            return engine.Context.build(scope, None, now)

        proposed = engine.propose(
            ctx("auditor@example.test", policy.AUDITOR),
            repo,
            adapter,
            case_id=case_id,
            body={
                "outcome": "continue_investigation",
                "rationale": "Import declarations need comparing with the invoices first.",
                "rating": 2,
                "expected_case_status": "AWAITING_HUMAN",
                "expected_version": 1,
            },
            idempotency_key="synthetic-" + uuid.uuid4().hex,
        )
        approved = engine.review(
            ctx("supervisor@example.test", policy.SUPERVISOR),
            repo,
            adapter,
            action="approve",
            decision_id=proposed["decision_id"],
            body={"expected_version": 2},
            idempotency_key="synthetic-" + uuid.uuid4().hex,
        )
        self.assertEqual(approved["jube_sync"]["state"], "synced")
        self.assertEqual(approved["jube_sync"]["status_after"], "INVESTIGATING")

    def test_unreachable_jube_is_upstream_unavailable_for_the_decision(self):
        jube = jube_fixture.FakeJube()
        jube.fail.extend([cl.TransportError(timed_out=False)] * 3)
        adapter = lifecycle(services(), transport=jube)
        adapter._sleep = lambda _s: None
        with self.assertRaises(cl.JubeAdapterError) as caught:
            adapter.ensure_case(TENANT, str(uuid.uuid4()), correlation_id=jube_fixture.CORR)
        self.assertEqual(
            (caught.exception.code, caught.exception.retryable), ("upstream_unavailable", True)
        )

    def test_risk_evaluator_is_the_declared_risk_evaluator(self):
        evaluator = config.risk_evaluator(services())
        self.assertIsInstance(evaluator, risk_invoke.DeclaredRiskEvaluator)
        self.assertEqual(evaluator.invoker.config.model_guid, MODEL_GUID)
        self.assertNotIn(RISK_KEY, repr(evaluator.invoker.config))

    def test_docling_adapter_takes_the_callers_job_store_and_sink(self):
        jobs = extraction_adapter.InMemoryJobStore()
        sink = object()
        adapter = config.docling_extraction(services(), jobs=jobs, sink=sink)
        self.assertIsInstance(adapter, extraction_adapter.DoclingExtractionAdapter)
        self.assertEqual(adapter.config.base_url, "http://host.docker.internal:55001")
        self.assertIs(adapter._jobs, jobs)

    def test_openaleph_unreachable_is_a_retryable_ingest_error(self):
        def down(method: str, path: str, body: Any = None) -> Any:
            raise urllib.error.URLError("connection refused")

        ingest = config.openaleph_ingest(services(), transport=down)
        self.assertEqual(ingest.collection_id, 3)
        entity = {"id": "synthetic-entity-1", "schema": "Company", "properties": {}}
        with self.assertRaises(entity_ingest.IngestError) as caught:
            ingest.ingest([entity], {"synthetic-entity-1": "d1"}, {})
        self.assertEqual(
            (caught.exception.code, caught.exception.retryable), ("upstream_unavailable", True)
        )
        # The real transport accepts the container's host name for local development.
        self.assertTrue(callable(config.openaleph_ingest(services()).transport))

    def test_yente_unreachable_is_a_retryable_review_error(self):
        def down(method: str, path: str, body: Any = None) -> Any:
            raise TimeoutError

        matcher = config.yente_matcher(services(), transport=down)
        self.assertEqual(matcher.dataset, "vati_synthetic")
        with self.assertRaises(candidate_review.ReviewError) as caught:
            matcher.dataset_version()
        self.assertEqual(
            (caught.exception.code, caught.exception.retryable), ("upstream_unavailable", True)
        )
        self.assertTrue(callable(config.yente_matcher(services()).transport))


class LocalHostRuleTests(unittest.TestCase):
    """Plain HTTP stays on the developer's machine: loopback or host.docker.internal only."""

    def test_lookalike_hosts_are_still_refused(self):
        for url in ("http://host.docker.internal.example.test:55501", "http://10.0.0.5:55501"):
            with self.subTest(url=url):
                jube = services()[config.SERVICES_KEY]["jube"]
                with self.assertRaises(config.ServiceNotConfigured):
                    lifecycle(services(jube={**jube, "base_url": url}))
                with self.assertRaises(risk_invoke.RiskConfigError):
                    risk_invoke.RiskInvokerConfig(url, RISK_KEY, MODEL_GUID)
                with self.assertRaises(ValueError):
                    entity_ingest.http_transport(url, ALEPH_KEY)
                with self.assertRaises(ValueError):
                    candidate_review.http_transport(url)
                with self.assertRaises(extraction_adapter.ConfigError):
                    extraction_adapter.AdapterConfig.build(base_url=url, api_key=DOCLING_KEY)


if __name__ == "__main__":
    unittest.main()
