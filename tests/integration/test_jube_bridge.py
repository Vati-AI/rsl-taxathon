"""Frappe to Jube bridge against the real adapters and a Jube fake (TAX-039).

Runs the pieces the Frappe bridge wires together, without Frappe: the bridge's
declared facts (``vati_tax.bridge.rules``), the risk invoker and declared payload
(``integration/jube/risk_invoke.py``), the TAX-034 ``JubeCaseAdapter`` and the
bridge's projection and ordering rules. The Jube fake extends the TAX-034 one with
the invoke route (locked source: ``POST /api/Invoke/EntityAnalysisModel/{guid}``,
which archives the invocation that ``CreateFromCaseKeyValue`` later needs).
The Frappe side (hooks, outbox handler, DocTypes, permissions) is covered by
``backend/vati_tax/vati_tax/bridge/test_bridge_bench.py`` on a site.
"""

from __future__ import annotations

import sys
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
sys.path.insert(0, str(ROOT / "integration" / "jube"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import case_lifecycle as cl  # noqa: E402
import risk_invoke as ri  # noqa: E402
import tax_risk  # noqa: E402
import test_jube_case_lifecycle as jube_fixture  # noqa: E402
from vati_tax.bridge import rules  # noqa: E402
from vati_tax.ids.canonical import MappingConflict  # noqa: E402

TENANT = jube_fixture.TENANT
TAXPAYER = "80015243-dd34-4e97-b085-7356ffdd042f"
MODEL_GUID = "90000000-0000-4000-8000-000000000001"
RULESET = tax_risk.load_ruleset()
CORR = jube_fixture.CORR
T0 = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
OPS = {">": Decimal.__gt__, ">=": Decimal.__ge__, "<": Decimal.__lt__, "<=": Decimal.__le__}


class FakeJubeWithRisk(jube_fixture.FakeJube):
    """TAX-034 fake plus the model invoke route, evaluated with the reviewed rules."""

    def __init__(self) -> None:
        super().__init__()
        self.invocations: list[dict[str, Any]] = []
        self.invoke_status = 200

    def __call__(self, method: str, path: str, body: Any = None) -> cl.Response:
        if method == "POST" and path == f"/api/Invoke/EntityAnalysisModel/{MODEL_GUID}":
            self.calls.append((method, path))
            if self.fail:
                outcome = self.fail.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            if self.invoke_status != 200:
                return cl.Response(self.invoke_status, {"message": "internal detail"})
            self.invocations.append(dict(body))
            self.archived.add(body["case_id"])
            return cl.Response(200, {"activation": {name: {} for name in self.fired(body)}})
        return super().__call__(method, path, body)

    @staticmethod
    def fired(payload: dict[str, Any]) -> list[str]:
        names = {f["name"]: f["jube_name"] for f in RULESET["features"]}

        def holds(cond: dict[str, Any] | None) -> bool:
            if cond is None:
                return True
            value = Decimal(payload[names[cond["feature"]]])
            return bool(OPS[cond["operator"]](value, Decimal(str(cond["value"]))))

        return [
            tax_risk.jube_rule_name(r).replace(" ", "_")
            for r in RULESET["rules"]
            if holds(r.get("applies_when")) and holds(r["condition"])
        ]


def make() -> tuple[ri.DeclaredRiskEvaluator, cl.JubeCaseAdapter, FakeJubeWithRisk]:
    jube = FakeJubeWithRisk()
    adapter, _, _ = jube_fixture.make(jube)
    config = ri.RiskInvokerConfig(
        base_url="http://127.0.0.1:55501", api_key="r" * 40, model_guid=MODEL_GUID
    )
    return ri.DeclaredRiskEvaluator(ri.RiskInvoker(config, jube), RULESET), adapter, jube


def facts(refund: str = "15000.00", input_vat: str = "21000.00", *treatments: str):
    return rules.declared_facts(
        refund_claimed=Decimal(refund),
        input_vat_claimed=Decimal(input_vat),
        imported_treatments=list(treatments),
    )


def send(evaluator: ri.DeclaredRiskEvaluator, adapter: cl.JubeCaseAdapter, case_id: str,
         declared: dict[str, str]) -> rules.Observation:  # fmt: skip
    """What the outbox handler does for one case.registered event."""
    evaluator.invoke_declared(
        case_id=case_id,
        taxpayer_id=TAXPAYER,
        period_end="2025-12-31",
        facts=declared,
        correlation_id=CORR,
    )
    adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
    case = adapter.get_case(TENANT, case_id, correlation_id=CORR)
    seen = rules.projection(
        case.jube_case_id, case.status, case.escalated, case.closed_state == "closed"
    )
    return rules.observe(TENANT, case_id, seen, T0)


class DeclaredPayloadTests(unittest.TestCase):
    def test_payload_uses_the_ruleset_entry_and_jube_names(self):
        case_id = str(uuid.uuid4())
        payload = ri.declared_payload(
            RULESET, case_id=case_id, taxpayer_id=TAXPAYER, period_end="2025-12-31",
            facts=facts(),
        )  # fmt: skip
        self.assertEqual(payload["case_id"], case_id)
        self.assertEqual(payload["reference_date"], "2025-12-31T00:00:00Z")
        self.assertEqual(payload["facts_basis"], "declaration")
        for feature in RULESET["features"]:
            self.assertIsInstance(payload[feature["jube_name"]], str)
        self.assertFalse(any(isinstance(v, float) for v in payload.values()))

    def test_bridge_facts_match_the_ruleset_features_exactly(self):
        self.assertEqual(set(rules.FACT_NAMES), {f["name"] for f in RULESET["features"]})

    def test_ruleset_drift_fails_loudly(self):
        short = dict(facts())
        short.pop("refund_claim")
        extra = {**facts(), "risk_score": "1"}
        for bad in (short, extra):
            with self.subTest(keys=sorted(bad)), self.assertRaises(ri.RiskConfigError):
                ri.declared_payload(RULESET, case_id=str(uuid.uuid4()), taxpayer_id=TAXPAYER,
                                    period_end="2025-12-31", facts=bad)  # fmt: skip
        with self.assertRaises(ri.RiskConfigError):
            ri.declared_payload(RULESET, case_id=str(uuid.uuid4()), taxpayer_id=TAXPAYER,
                                period_end="2025-12-31",
                                facts={**facts(), "consecutive_loss_periods": "1.50"})  # fmt: skip


class SubmitTests(unittest.TestCase):
    def test_a_sent_claim_is_evaluated_then_raised_as_one_jube_case(self):
        evaluator, adapter, jube = make()
        case_id = str(uuid.uuid4())
        seen = send(evaluator, adapter, case_id, facts())
        self.assertEqual(len(jube.invocations), 1)
        self.assertEqual(len(jube.cases), 1)
        self.assertEqual(seen.projection.status, "RECEIVED")
        self.assertEqual(seen.projection.review_status, "under_review")

    def test_declared_figures_route_the_case_through_the_reviewed_rules(self):
        refund = FakeJubeWithRisk.fired(
            ri.declared_payload(
                RULESET,
                case_id=str(uuid.uuid4()),
                taxpayer_id=TAXPAYER,
                period_end="2025-12-31",
                facts=facts("15000.00", "21000.00", "unsure"),
            )  # fmt: skip
        )
        self.assertIn("TR-VAT-REFUND-MISMATCH_v1.0.0", refund)
        self.assertIn("TR-IMPORTED-SERVICE-GAP_v1.0.0", refund)
        quiet = FakeJubeWithRisk.fired(
            ri.declared_payload(
                RULESET,
                case_id=str(uuid.uuid4()),
                taxpayer_id=TAXPAYER,
                period_end="2025-12-31",
                facts=facts("0.00", "0.00"),
            )  # fmt: skip
        )
        self.assertNotIn("TR-VAT-REFUND-MISMATCH_v1.0.0", quiet)

    def test_replaying_the_whole_delivery_creates_no_second_case(self):
        evaluator, adapter, jube = make()
        case_id = str(uuid.uuid4())
        first = send(evaluator, adapter, case_id, facts())
        again = send(evaluator, adapter, case_id, facts())
        self.assertEqual(len(jube.cases), 1)
        self.assertEqual(again.projection, first.projection)
        creates = [c for c in jube.calls if c[1] == "/api/Case/CreateFromCaseKeyValue"]
        self.assertEqual(len(creates), 1)

    def test_case_before_risk_evaluation_is_a_retryable_error(self):
        _, adapter, _ = make()
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.ensure_case(TENANT, str(uuid.uuid4()), correlation_id=CORR)
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.reason, "risk_evaluation_missing")

    def test_invoke_failures_are_classified_without_upstream_text(self):
        evaluator, _, jube = make()
        expectations = {401: False, 403: False, 400: False, 404: True, 429: True, 503: True}
        for status, retryable in expectations.items():
            jube.invoke_status = status
            with self.subTest(status=status), self.assertRaises(cl.JubeAdapterError) as ctx:
                evaluator.invoke_declared(case_id=str(uuid.uuid4()), taxpayer_id=TAXPAYER,
                                          period_end="2025-12-31", facts=facts(),
                                          correlation_id=CORR)  # fmt: skip
            self.assertEqual(ctx.exception.retryable, retryable)
            self.assertNotIn("internal detail", str(ctx.exception))
        jube.invoke_status = 200
        jube.fail.append(cl.TransportError(timed_out=True))
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            evaluator.invoke_declared(case_id=str(uuid.uuid4()), taxpayer_id=TAXPAYER,
                                      period_end="2025-12-31", facts=facts(),
                                      correlation_id=CORR)  # fmt: skip
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.code, "upstream_timeout")

    def test_invoker_refuses_floats_and_payloads_without_a_case(self):
        _, _, jube = make()
        invoker = ri.RiskInvoker(
            ri.RiskInvokerConfig(base_url="http://127.0.0.1:55501", api_key="r" * 40,
                                 model_guid=MODEL_GUID), jube)  # fmt: skip
        with self.assertRaises(cl.JubeAdapterError):
            invoker.invoke({"case_id": str(uuid.uuid4()), "RefundClaim": 1.5}, correlation_id=CORR)
        with self.assertRaises(cl.JubeAdapterError):
            invoker.invoke({"RefundClaim": "1.50"}, correlation_id=CORR)
        self.assertEqual(jube.invocations, [])

    def test_unsafe_invoker_configuration_fails_at_startup(self):
        good = {"base_url": "http://127.0.0.1:55501", "api_key": "r" * 40, "model_guid": MODEL_GUID}
        for change in ({"base_url": "http://jube.example"}, {"api_key": "short"},
                       {"model_guid": "not-a-guid"}):  # fmt: skip
            with self.subTest(change=change), self.assertRaises(ri.RiskConfigError):
                ri.RiskInvokerConfig(**{**good, **change})
        self.assertNotIn("r" * 40, repr(ri.RiskInvokerConfig(**good)))


class MirrorTests(unittest.TestCase):
    def test_jube_progress_maps_to_taxpayer_words_and_hides_internal_states(self):
        evaluator, adapter, jube = make()
        case_id = str(uuid.uuid4())
        send(evaluator, adapter, case_id, facts())
        jube_case = next(c for c in jube.cases.values() if c["caseKeyValue"] == case_id)
        jube_case["rating"] = 2
        adapter.transition(TENANT, case_id, "INVESTIGATING", expected_status="RECEIVED",
                           correlation_id=CORR)  # fmt: skip
        adapter.escalate(TENANT, case_id, expected_status="INVESTIGATING", correlation_id=CORR)
        case = adapter.get_case(TENANT, case_id, correlation_id=CORR)
        seen = rules.projection(case.jube_case_id, case.status, case.escalated, False)
        self.assertTrue(seen.escalated)
        self.assertEqual(seen.review_status, "under_review")  # escalation is invisible
        adapter.transition(TENANT, case_id, "RESOLVED", expected_status="AWAITING_HUMAN",
                           correlation_id=CORR, decision_id=str(uuid.uuid4()))  # fmt: skip
        case = adapter.get_case(TENANT, case_id, correlation_id=CORR)
        done = rules.projection(case.jube_case_id, case.status, case.escalated,
                                case.closed_state == "closed")  # fmt: skip
        self.assertTrue(done.closed)
        self.assertEqual(done.review_status, "completed")

    def test_out_of_order_observations_are_refused(self):
        evaluator, adapter, _ = make()
        case_id = str(uuid.uuid4())
        first = send(evaluator, adapter, case_id, facts())
        stored = rules.Mirror(first.projection.jube_case_id, first.projection, first.observed_at)
        late = rules.observe(TENANT, case_id, first.projection, T0 - timedelta(seconds=30))
        with self.assertRaises(rules.StaleUpdate):
            rules.decide_observation(late, stored, first.projection.jube_case_id)

    def test_an_observation_for_another_jube_case_is_refused(self):
        evaluator, adapter, _ = make()
        case_id = str(uuid.uuid4())
        first = send(evaluator, adapter, case_id, facts())
        other = rules.Projection("999", "RECEIVED", False, False)
        with self.assertRaises(MappingConflict):
            rules.decide_observation(rules.observe(TENANT, case_id, other, T0), None,
                                     first.projection.jube_case_id)  # fmt: skip

    def test_risk_activations_never_reach_the_projection_or_reference(self):
        evaluator, _, _ = make()
        ref = evaluator.invoke_declared(case_id=str(uuid.uuid4()), taxpayer_id=TAXPAYER,
                                        period_end="2025-12-31", facts=facts(),
                                        correlation_id=CORR)  # fmt: skip
        self.assertNotIn("TR-", ref)
        self.assertEqual(set(rules.Projection.__dataclass_fields__),
                         {"jube_case_id", "status", "escalated", "closed"})  # fmt: skip


if __name__ == "__main__":
    unittest.main()
