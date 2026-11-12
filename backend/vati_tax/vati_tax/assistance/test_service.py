"""Frappe-free tests for the taxpayer-assistance service (TAX-100).

The runtime fakes come from the TAX-102 harness: ``ReferenceAssistant`` answers all
three AI operations from the fixture's expected outcomes, so the service's own rules
(permission context, answer checks, drafts, confirmation) are what is under test.
With LangGraph installed, the real TAX-098 assistant also runs through the service.
"""

from __future__ import annotations

import copy
import datetime
import unittest
from typing import Any

from vati_tax.api_v1 import core
from vati_tax.assistance import service as svc

try:
    from ai.evaluation import taxpayer_assistant_eval as tae
    from ai.evaluation import taxpayer_assistant_fakes as fakes
except ImportError as exc:  # pragma: no cover - the repository path is not importable
    raise unittest.SkipTest(f"TAX-102 harness not importable ({exc})") from None

try:
    from ai.agents import taxpayer_assistant as tax098
except ImportError:  # LangGraph is optional here
    tax098 = None  # type: ignore[assignment]

FX = tae.load_fixtures()
TENANT = FX["tenant_id"]
OWN = FX["taxpayer"]["taxpayer_id"]
OTHER = FX["other_taxpayer"]["taxpayer_id"]
SUBMISSION = FX["submission"]["submission_id"]
REQUEST_ID = FX["evidence_request"]["request_id"]
NOW = datetime.datetime(2026, 11, 12, 9, 0, tzinfo=datetime.UTC)


def reference_runtime() -> Any:
    """The TAX-102 oracle. It names its IDs after the harness case, which the service
    does not send, so the case label comes from the Idempotency-Key here."""
    reference = fakes.ReferenceAssistant(FX)

    def runtime(store: dict[str, Any]) -> Any:
        answer = reference(store)
        return lambda request: answer({**request, "case_id": request["headers"]["Idempotency-Key"]})

    return runtime


class MemoryDrafts:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get(self, tenant_id: str, taxpayer_id: str, draft_id: str) -> dict[str, Any] | None:
        row = self.rows.get(draft_id)
        ok = row and row["tenant_id"] == tenant_id and row["taxpayer_id"] == taxpayer_id
        return copy.deepcopy(row) if ok else None

    def by_key(self, tenant_id: str, taxpayer_id: str, key: str) -> dict[str, Any] | None:
        for row in self.rows.values():
            if (row["tenant_id"], row["taxpayer_id"], row["idempotency_key"]) == (
                tenant_id, taxpayer_id, key):  # fmt: skip
                return copy.deepcopy(row)
        return None

    def save(self, record: dict[str, Any]) -> None:
        self.rows[record["draft_id"]] = copy.deepcopy(record)


class World:
    """A server's records plus fakes for every port."""

    def __init__(self, runtime: Any = "reference", roles: frozenset[str] = frozenset({"taxpayer"})):
        self.store = tae.build_store(FX)
        self.roles = roles
        self.drafts = MemoryDrafts()
        self.responses: list[tuple[str, str, str, dict[str, Any]]] = []
        self.reject: svc.ResponseRejected | None = None
        self.ledger: list[tuple[str, str]] = []
        self.reports: list[str] = []
        self.runtime = reference_runtime() if runtime == "reference" else runtime
        self.service = svc.AssistanceService(
            svc.Ports(
                caller=lambda tenant: svc.Caller(
                    "taxpayer@example.test", tenant, self.roles, frozenset({OWN})
                ),  # fmt: skip
                records=self.records,
                runtime=lambda: self.runtime,
                drafts=self.drafts,
                respond=self.respond,
                ledger=lambda action, outcome, **ids: self.ledger.append((action, outcome)),
                clock=lambda: NOW,
                report=self.reports.append,
            )
        )

    def records(self, tenant_id: str, taxpayer_id: str) -> dict[str, Any]:
        # A careless port that returns everything: the service must still filter.
        return copy.deepcopy(self.store)

    def respond(self, tenant: str, taxpayer: str, request_id: str, body: dict[str, Any]) -> str:
        if self.reject is not None:
            raise self.reject
        self.responses.append((tenant, taxpayer, request_id, copy.deepcopy(body)))
        return "e5000000-0000-4000-8000-000000000100"

    def call(self, method: str, path: str, body: Any = None, key: str = "tax100-key-0000001",
             tenant: str = TENANT) -> core.ApiResponse:  # fmt: skip
        headers = {"X-Vati-Tenant-ID": tenant, "X-Correlation-ID":
                   "c0000000-0000-4000-8100-000000000001"}  # fmt: skip
        if method == "POST":
            headers["Idempotency-Key"] = key
        request = core.ApiRequest(method, path, headers, body=body)
        return core.handle(request, self.service.handlers(), authenticated=True)


def base(taxpayer: str = OWN) -> str:
    return f"/api/v1/taxpayers/{taxpayer}/assistance"


def draft_body(**kw: Any) -> dict[str, Any]:
    return {"request_id": REQUEST_ID, "taxpayer_note": None, **kw}


def confirm_body(version: int = 1, **kw: Any) -> dict[str, Any]:
    items = [{"item_number": n, "evidence_ids": [], "reason_unavailable": "Synthetic reason."}
             for n in range(1, 6)]  # fmt: skip
    items[0] = {"item_number": 1, "evidence_ids": ["50000000-0000-4000-8102-000000000002"],
                "reason_unavailable": None}  # fmt: skip
    return {"expected_version": version, "declaration": True, "items": items,
            "message": "Synthetic: see the documents.", **kw}  # fmt: skip


class PermissionContextTests(unittest.TestCase):
    def test_the_taxpayer_gets_assistance_for_its_own_editable_submission(self) -> None:
        world = World()
        for suffix in ("validation-explanations", "document-reviews"):
            body = {"submission_id": SUBMISSION}
            if suffix == "document-reviews":
                body["evidence_ids"] = []
            result = world.call("POST", f"{base()}/{suffix}", body)
            self.assertEqual(result.status, 200, result.body)
            self.assertEqual(result.body["data"]["taxpayer_id"], OWN)
        self.assertEqual([a for a, _ in world.ledger], ["assistance.explainValidationResults",
                                                        "assistance.reviewSubmissionDocuments"])  # fmt: skip

    def test_staff_and_services_are_forbidden(self) -> None:
        for roles in ({"auditor"}, {"supervisor"}, {"service"}, {"taxpayer", "auditor"}):
            world = World(roles=frozenset(roles))
            result = world.call("POST", f"{base()}/validation-explanations",
                                {"submission_id": SUBMISSION})  # fmt: skip
            self.assertEqual((result.status, result.body["error"]["code"]), (403, "forbidden"))
            self.assertEqual(world.ledger[-1][1], "denied")

    def test_another_taxpayer_is_reported_like_a_missing_one(self) -> None:
        world = World()
        for path in (f"{base(OTHER)}/validation-explanations",
                     f"{base('20000000-0000-4000-8000-0000000009ff')}/validation-explanations"):  # fmt: skip
            result = world.call("POST", path, {"submission_id": SUBMISSION})
            self.assertEqual((result.status, result.body["error"]["code"]), (404, "not_found"))
        result = world.call("POST", f"{base()}/validation-explanations",
                            {"submission_id": "30000000-0000-4000-8000-000000000902"})  # fmt: skip
        self.assertEqual(
            (result.status, result.body["error"]["code"]), (422, "invalid_domain_input")
        )
        self.assertEqual(result.body["error"]["field_errors"][0]["code"], "not_found")

    def test_a_sent_submission_is_not_editable(self) -> None:
        world = World()
        world.store["submissions"][SUBMISSION]["status"] = "submitted"
        result = world.call("POST", f"{base()}/validation-explanations",
                            {"submission_id": SUBMISSION})  # fmt: skip
        self.assertEqual(result.body["error"]["field_errors"][0]["code"], "submission_not_editable")

    def test_the_assistant_sees_only_the_callers_records(self) -> None:
        seen: list[dict[str, Any]] = []
        world = World()
        reference = reference_runtime()

        def runtime(store: dict[str, Any]) -> Any:
            seen.append(copy.deepcopy(store))
            return reference(store)

        world.runtime = runtime
        world.call("POST", f"{base()}/validation-explanations", {"submission_id": SUBMISSION})
        store = seen[0]
        self.assertNotIn("internal", store)
        self.assertEqual(set(store["taxpayers"]), {OWN})
        text = str(store)
        for canary in FX["canaries"]["internal"] + FX["canaries"]["other_taxpayer"]:
            self.assertNotIn(canary, text)


class AnswerCheckTests(unittest.TestCase):
    def ask(self, runtime: Any) -> core.ApiResponse:
        world = World(runtime=runtime)
        self.world = world
        return world.call(
            "POST", f"{base()}/validation-explanations", {"submission_id": SUBMISSION}
        )

    def wrap(self, change: Any) -> Any:
        reference = reference_runtime()

        def runtime(store: dict[str, Any]) -> Any:
            def answer(request: Any) -> Any:
                response = reference(store)(request)
                change(response, store)
                return response

            return answer

        return runtime

    def test_no_runtime_is_unavailable_not_invented(self) -> None:
        result = self.ask(None)
        self.assertEqual(
            (result.status, result.body["error"]["code"]), (503, "upstream_unavailable")
        )

    def test_leaky_or_out_of_scope_answers_are_never_sent(self) -> None:
        cases = {
            "forbidden_field": lambda r, s: r["body"]["data"].update(risk_score="0.873"),
            "other_tenant": lambda r, s: r["body"]["data"].update(
                tenant_id="10000000-0000-4000-8000-0000000009ff"
            ),  # fmt: skip
            "changed_records": lambda r, s: s["submissions"][SUBMISSION]["declared"].update(
                claimed_refund="1.00"
            ),  # fmt: skip
            "bad_status": lambda r, s: r.update(status=500),
        }
        for name, change in cases.items():
            with self.subTest(name):
                result = self.ask(self.wrap(change))
                self.assertEqual(result.status, 503)
                self.assertNotIn("0.873", str(result.body))
                self.assertTrue(self.world.reports)

    def test_an_assistant_that_raises_is_a_dependency_failure(self) -> None:
        def runtime(store: Any) -> Any:
            def boom(request: Any) -> Any:
                raise RuntimeError("secret detail")

            return boom

        result = self.ask(runtime)
        self.assertEqual(result.status, 503)
        self.assertNotIn("secret detail", str(result.body))


class DraftAndConfirmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        result = self.world.call("POST", f"{base()}/evidence-response-drafts", draft_body())
        self.assertEqual(result.status, 201, result.body)
        self.draft = result.body["data"]

    def confirm(self, body: Any, key: str = "tax100-confirm-0001") -> core.ApiResponse:
        path = f"{base()}/evidence-response-drafts/{self.draft['draft_id']}/confirm"
        return self.world.call("POST", path, body, key=key)

    def test_a_draft_is_stored_and_nothing_is_sent(self) -> None:
        self.assertEqual((self.draft["status"], self.draft["version"]), ("draft", 1))
        self.assertIs(self.draft["sent_to_rsl"], False)
        self.assertIsNone(self.draft["confirmation"])
        self.assertEqual(self.world.responses, [])
        stored = self.world.drafts.rows[self.draft["draft_id"]]
        self.assertEqual(stored["disclosure"]["advisory_only"], True)
        got = self.world.call("GET", f"{base()}/evidence-response-drafts/{self.draft['draft_id']}")
        self.assertEqual(got.body["data"], self.draft)

    def test_draft_creation_is_idempotent(self) -> None:
        again = self.world.call("POST", f"{base()}/evidence-response-drafts", draft_body())
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(again.body["data"]["draft_id"], self.draft["draft_id"])
        self.assertEqual(len(self.world.drafts.rows), 1)
        changed = self.world.call("POST", f"{base()}/evidence-response-drafts",
                                  draft_body(taxpayer_note="Different."))  # fmt: skip
        self.assertEqual(changed.body["error"]["code"], "idempotency_conflict")

    def test_a_closed_request_cannot_be_drafted_or_confirmed(self) -> None:
        self.world.store["evidence_requests"][REQUEST_ID]["status"] = "responded"
        got = self.world.call("GET", f"{base()}/evidence-response-drafts/{self.draft['draft_id']}")
        self.assertEqual(got.body["data"]["status"], "request_closed")
        result = self.world.call("POST", f"{base()}/evidence-response-drafts", draft_body(),
                                 key="tax100-key-0000002")  # fmt: skip
        self.assertEqual(result.body["error"]["field_errors"][0]["code"], "request_closed")
        self.assertEqual(self.confirm(confirm_body()).status, 422)
        self.assertEqual(self.world.responses, [])

    def test_confirmation_sends_once_through_tax045(self) -> None:
        result = self.confirm(confirm_body())
        self.assertEqual(result.status, 200, result.body)
        data = result.body["data"]
        self.assertEqual(
            (data["status"], data["version"], data["sent_to_rsl"]), ("confirmed", 2, True)
        )
        self.assertEqual(data["confirmation"]["evidence_response_id"],
                         "e5000000-0000-4000-8000-000000000100")  # fmt: skip
        ((tenant, taxpayer, request_id, body),) = self.world.responses
        self.assertEqual((tenant, taxpayer, request_id), (TENANT, OWN, REQUEST_ID))
        self.assertEqual([a["item_index"] for a in body["items"]], [0, 1, 2, 3, 4])
        self.assertEqual(body["items"][0]["evidence_ids"], ["50000000-0000-4000-8102-000000000002"])
        self.assertIsNone(body["items"][0]["unavailable_reason"])
        self.assertRegex(body["idempotency_key"], r"^[A-Za-z0-9:._-]{16,255}$")
        self.assertIn(("assistance.draft_confirmed", "confirmed"), self.world.ledger)
        # Same key and body: replayed, not sent again.
        again = self.confirm(confirm_body())
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(len(self.world.responses), 1)
        # Same key, other body; or another key once confirmed.
        self.assertEqual(self.confirm(confirm_body(message="Other.")).body["error"]["code"],
                         "idempotency_conflict")  # fmt: skip
        other = self.confirm(confirm_body(version=2), key="tax100-confirm-0002")
        self.assertEqual((other.status, other.body["error"]["current_version"]), (409, 2))
        self.assertEqual(len(self.world.responses), 1)

    def test_a_stale_version_or_missing_declaration_sends_nothing(self) -> None:
        self.assertEqual(self.confirm(confirm_body(version=3)).status, 409)
        self.assertEqual(self.confirm(confirm_body(declaration=False)).status, 400)
        self.assertEqual(self.world.responses, [])

    def test_tax045_refusals_come_back_on_the_contract_item(self) -> None:
        self.world.reject = svc.ResponseRejected(
            "invalid_domain_input", "Check the highlighted answers.",
            [{"field": "items.2", "code": "answer_required", "message": "Upload a document."}],
        )  # fmt: skip
        result = self.confirm(confirm_body())
        self.assertEqual(result.status, 422)
        self.assertEqual(result.body["error"]["field_errors"][0]["field"], "items.3")
        stored = self.world.drafts.rows[self.draft["draft_id"]]
        self.assertEqual(stored["status"], "draft")
        self.assertIn(("assistance.draft_confirmed", "refused"), self.world.ledger)

    def test_another_taxpayers_draft_is_not_found(self) -> None:
        self.world.drafts.rows[self.draft["draft_id"]]["taxpayer_id"] = OTHER
        got = self.world.call("GET", f"{base()}/evidence-response-drafts/{self.draft['draft_id']}")
        self.assertEqual(got.status, 404)
        self.assertEqual(self.confirm(confirm_body()).status, 404)
        self.assertEqual(self.world.responses, [])


def service_factory(runtime: Any) -> Any:
    """The service as a TAX-102 assistant: harness requests through /api/v1."""

    def factory(store: dict[str, Any]) -> Any:
        world = World(runtime=runtime)
        world.store = store  # the harness's store; it checks that nothing changes

        def answer(request: dict[str, Any]) -> dict[str, Any]:
            result = world.call("POST", request["path"], request["body"],
                                key=request["headers"]["Idempotency-Key"])  # fmt: skip
            return {"status": result.status, "body": result.body}

        return answer

    return factory


class Tax102GateThroughTheServiceTests(unittest.TestCase):
    def test_the_reference_runtime_passes_every_case_through_the_service(self) -> None:
        checks, report = tae.evaluate(service_factory(reference_runtime()), FX,
                                      name="service+reference")  # fmt: skip
        self.assertEqual(report["gate"], "pass", [c.check_id for c in checks if not c.passed])
        self.assertEqual(report["cases"], 16)

    @unittest.skipIf(tax098 is None, "LangGraph not installed")
    def test_the_tax098_assistant_passes_its_cases_through_the_service(self) -> None:
        assert tax098 is not None
        checks, report = tae.evaluate(
            service_factory(tax098.make_assistant), FX, name="service+tax098",
            operations=("explainValidationResults", "reviewSubmissionDocuments"),
        )  # fmt: skip
        self.assertEqual(report["gate"], "pass", [c.check_id for c in checks if not c.passed])


if __name__ == "__main__":
    unittest.main()
