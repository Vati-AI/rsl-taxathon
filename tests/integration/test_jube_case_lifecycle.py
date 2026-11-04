"""Behaviour tests for the Jube case lifecycle adapter (TAX-034) against a Jube fake.

FakeJube models the case routes from the locked Jube source (CaseEndpoints.cs,
CaseService.cs, CaseDtoValidator.cs) as observed live: creation needs an archived
invocation and answers 409 for an open case with the same key; updates need a 1-5 rating
and a payload, refuse a LockedUser outside the tenant (400 LockedUserInTenant) and answer
409 when the case is locked to another user. The live contract run is scripts/jube_cases.py.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "jube"))

import case_lifecycle as cl  # noqa: E402
import tax_risk  # noqa: E402

_spec = importlib.util.spec_from_file_location("jube_cases", ROOT / "scripts" / "jube_cases.py")
assert _spec and _spec.loader
jube_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jube_cases)
jube_rules = jube_cases.jube_rules
jube_dev = jube_cases.jube_dev

WORKFLOW = cl.load_workflow()
RAW_WORKFLOW = json.loads(cl.WORKFLOW_PATH.read_text(encoding="utf-8"))
TENANT = "10000000-0000-4000-8000-000000000001"
OTHER_TENANT = "10000000-0000-4000-8000-000000000002"
OFFICER = "20000000-0000-4000-8000-000000000001"
STATUS_GUIDS = {s.name: str(uuid.uuid5(uuid.NAMESPACE_URL, s.name)) for s in WORKFLOW.statuses}
WORKFLOW_GUID = "30000000-0000-4000-8000-000000000001"
KEY = "k" * 40
CORR = "40000000-0000-4000-8000-000000000001"


class FakeJube:
    def __init__(self) -> None:
        self.archived: set[str] = set()
        self.cases: dict[int, dict[str, Any]] = {}
        self.users = {"vati-case-adapter", "vati-officer-" + OFFICER}
        self.calls: list[tuple[str, str]] = []
        self.puts: list[dict[str, Any]] = []
        self.fail: list[Any] = []  # queued Response or Exception, consumed per call

    def add_case(self, case_id: str, status: str = "Received", **fields: Any) -> int:
        new_id = len(self.cases) + 1
        self.cases[new_id] = {
            "id": new_id,
            "caseKey": WORKFLOW.case_key,
            "caseKeyValue": case_id,
            "caseWorkflowStatusGuid": STATUS_GUIDS[status],
            "closedStatusId": 0,
            "locked": False,
            "lockedUser": None,
            "diary": False,
            "diaryDate": "2026-11-04T08:46:38.599336+00:00",
            "rating": 0,
            "createdDate": "2026-11-04T08:46:38+00:00",
            **fields,
        }
        return new_id

    def __call__(self, method: str, path: str, body: Any = None) -> cl.Response:
        self.calls.append((method, path))
        if self.fail:
            outcome = self.fail.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is not None:
                return outcome
        if method == "POST" and path == "/api/Case/CreateFromCaseKeyValue":
            value = body["caseKeyValue"]
            if any(
                c["caseKeyValue"] == value and c["closedStatusId"] in (0, 1, 2, 4)
                for c in self.cases.values()
            ):
                return cl.Response(409, "A case already exists.")
            if value not in self.archived:
                return cl.Response(404, "No transaction found for this Case Key/Value combination.")
            name = next(n for n, g in STATUS_GUIDS.items() if g == body["caseWorkflowStatusGuid"])
            return cl.Response(200, copy.deepcopy(self.cases[self.add_case(value, name)]))
        if method == "GET" and path == "/api/Case/":
            return cl.Response(200, copy.deepcopy(list(self.cases.values())))
        if method == "GET" and path.startswith("/api/Case/"):
            case = self.cases.get(int(path.rsplit("/", 1)[1]))
            return cl.Response(200, copy.deepcopy(case)) if case else cl.Response(404, None)
        if method == "PUT" and path == "/api/Case/":
            self.puts.append(copy.deepcopy(body))
            errors = []
            if not 1 <= body.get("rating", 0) <= 5:
                errors.append({"propertyName": "Rating", "errorCode": "RatingInclusiveBetween"})
            if not body.get("payload"):
                errors.append({"propertyName": "Payload", "errorCode": "PayloadNotEmpty"})
            if errors:
                return cl.Response(400, {"isValid": False, "errors": errors})
            case = self.cases.get(body["id"])
            if case is None:
                return cl.Response(204, None)
            if case["locked"] and case["lockedUser"] not in (None, "vati-case-adapter"):
                return cl.Response(409, None)
            if body["lockedUser"] and body["lockedUser"] not in self.users:
                return cl.Response(
                    400,
                    {"errors": [{"propertyName": "LockedUser", "errorCode": "LockedUserInTenant"}]},
                )
            for field in ("caseWorkflowStatusGuid", "closedStatusId", "lockedUser", "rating"):
                case[field] = body[field]
            return cl.Response(200, {"id": case["id"]})
        raise AssertionError(f"unexpected {method} {path}")


def make(jube: FakeJube | None = None, links: cl.LinkStore | None = None):
    jube = jube or FakeJube()
    config = cl.AdapterConfig.build(
        WORKFLOW,
        base_url="http://127.0.0.1:55501",
        api_key=KEY,
        vati_tenant_id=TENANT,
        workflow_guid=WORKFLOW_GUID,
        status_guids=STATUS_GUIDS,
    )
    sleeps: list[float] = []
    adapter = cl.JubeCaseAdapter(
        config, WORKFLOW, links or cl.InMemoryLinkStore(), jube, sleep=sleeps.append
    )
    return adapter, jube, sleeps


def created(adapter: cl.JubeCaseAdapter, jube: FakeJube, rating: int = 0) -> str:
    case_id = str(uuid.uuid4())
    jube.archived.add(case_id)
    adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
    if rating:
        next(c for c in jube.cases.values() if c["caseKeyValue"] == case_id)["rating"] = rating
    return case_id


def posts(jube: FakeJube) -> int:
    return sum(1 for m, _ in jube.calls if m == "POST")


class WorkflowDefinitionTests(unittest.TestCase):
    def test_every_vati_status_has_exactly_one_plain_jube_status(self) -> None:
        plain = [s.vati_status for s in WORKFLOW.statuses if not s.escalated]
        self.assertCountEqual(plain, cl.CASE_STATUSES)
        contract = json.loads((ROOT / "integration/api/openapi.json").read_text())
        self.assertEqual(
            set(cl.CASE_STATUSES), set(contract["components"]["schemas"]["CaseStatus"]["enum"])
        )

    def test_case_key_is_the_risk_model_entry_name_jube_archives_under(self) -> None:
        self.assertEqual(WORKFLOW.case_key, tax_risk.load_ruleset()["jube_model"]["entry_name"])

    def test_rejects_ambiguous_or_unsafe_definitions(self) -> None:
        def broken(mutate):
            raw = copy.deepcopy(RAW_WORKFLOW)
            mutate(raw)
            with self.assertRaises(cl.WorkflowError):
                cl.validate_workflow(raw)

        broken(lambda r: r.update(synthetic=False))
        broken(lambda r: r["statuses"].pop(0))  # RECEIVED unmapped
        broken(lambda r: r["statuses"].append(dict(r["statuses"][1])))  # duplicate
        broken(lambda r: r["statuses"][2].update(vati_status="CLOSED"))  # not in contract
        broken(lambda r: r["statuses"][2].update(closes_case=True))  # only RESOLVED closes
        broken(lambda r: r["statuses"][6].pop("requires_decision"))
        broken(lambda r: r.update(initial_status="Investigating"))
        broken(lambda r: r.update(escalation_status="Investigating"))

    def test_provisioned_statuses_send_no_notification_or_webhook(self) -> None:
        for status in WORKFLOW.statuses:
            body = jube_cases.status_body(7, status)
            self.assertFalse(body["enableNotification"] or body["enableHttpEndpoint"])


class ConfigTests(unittest.TestCase):
    def build(self, **overrides):
        kwargs = dict(
            base_url="http://127.0.0.1:55501",
            api_key=KEY,
            vati_tenant_id=TENANT,
            workflow_guid=WORKFLOW_GUID,
            status_guids=STATUS_GUIDS,
        )
        kwargs.update(overrides)
        return cl.AdapterConfig.build(WORKFLOW, **kwargs)

    def test_missing_or_unsafe_configuration_fails_at_startup(self) -> None:
        for overrides in (
            {"api_key": None},
            {"base_url": "http://jube.example.test"},
            {"base_url": "ftp://127.0.0.1"},
            {"vati_tenant_id": "tenant-a"},
            {"status_guids": {k: v for k, v in STATUS_GUIDS.items() if k != "Resolved"}},
            {"status_guids": {k: WORKFLOW_GUID for k in STATUS_GUIDS}},
            {"max_attempts": 5},
        ):
            with self.subTest(overrides=list(overrides)), self.assertRaises(cl.ConfigError):
                self.build(**overrides)

    def test_api_key_is_not_in_repr(self) -> None:
        self.assertNotIn(KEY, repr(self.build(base_url="https://jube.example.test")))


class CreateTests(unittest.TestCase):
    def test_creates_one_case_in_the_initial_status_and_records_the_link(self) -> None:
        adapter, jube, _ = make()
        case_id = str(uuid.uuid4())
        jube.archived.add(case_id)
        outcome = adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
        self.assertTrue(outcome.changed)
        self.assertEqual((outcome.case.status, outcome.case.jube_status), ("RECEIVED", "Received"))
        self.assertEqual(adapter.links.find_jube_case(TENANT, case_id), "1")
        self.assertEqual(outcome.case.status_authority, "jube")

    def test_replay_uses_the_link_and_sends_no_create(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube)
        again = adapter.ensure_case(TENANT, case_id.upper(), correlation_id=CORR)
        self.assertFalse(again.changed)
        self.assertEqual((posts(jube), len(jube.cases)), (1, 1))

    def test_lost_link_is_recovered_from_jube_without_a_second_case(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube)
        fresh, _, _ = make(jube)  # empty link store: the crash happened before recording
        outcome = fresh.ensure_case(TENANT, case_id, correlation_id=CORR)
        self.assertFalse(outcome.changed)
        self.assertEqual(fresh.links.find_jube_case(TENANT, case_id), "1")
        self.assertEqual((posts(jube), len(jube.cases)), (1, 1))

    def test_a_closed_case_is_still_found_so_closure_never_causes_a_second_case(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube)
        jube.cases[1]["closedStatusId"] = 3
        fresh, _, _ = make(jube)
        self.assertFalse(fresh.ensure_case(TENANT, case_id, correlation_id=CORR).changed)
        self.assertEqual(len(jube.cases), 1)

    def test_concurrent_create_conflict_resolves_to_the_winner(self) -> None:
        adapter, jube, _ = make()
        case_id = str(uuid.uuid4())
        jube.archived.add(case_id)
        winner = jube.add_case(case_id)
        # Our lookup runs before the winner commits, then Jube answers 409.
        jube.fail = [cl.Response(200, []), None]
        outcome = adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
        self.assertEqual((outcome.changed, outcome.case.jube_case_id), (False, winner))

    def test_missing_risk_invocation_is_a_retryable_domain_error(self) -> None:
        adapter, _, _ = make()
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.ensure_case(TENANT, str(uuid.uuid4()), correlation_id=CORR)
        self.assertEqual(
            (ctx.exception.code, ctx.exception.reason, ctx.exception.retryable),
            ("invalid_domain_input", "risk_evaluation_missing", True),
        )

    def test_create_is_never_blindly_retried_after_a_timeout(self) -> None:
        adapter, jube, _ = make()
        case_id = str(uuid.uuid4())
        jube.archived.add(case_id)
        jube.fail = [None, cl.TransportError(timed_out=True)]  # list ok, create times out
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
        self.assertEqual((ctx.exception.code, ctx.exception.retryable), ("upstream_timeout", True))
        self.assertEqual(posts(jube), 1)
        self.assertIsNone(adapter.links.find_jube_case(TENANT, case_id))

    def test_timeout_after_jube_committed_returns_the_committed_case(self) -> None:
        adapter, jube, _ = make()
        case_id = str(uuid.uuid4())
        jube.archived.add(case_id)

        def lands_then_times_out(method: str, path: str, body: Any = None) -> cl.Response:
            if method == "POST":
                jube(method, path, body)
                raise cl.TransportError(timed_out=True)
            return jube(method, path, body)

        adapter._send = lands_then_times_out
        outcome = adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
        self.assertTrue(outcome.changed)
        self.assertEqual(len(jube.cases), 1)
        self.assertEqual(adapter.links.find_jube_case(TENANT, case_id), "1")

    def test_duplicate_jube_cases_are_reported_not_guessed(self) -> None:
        adapter, jube, _ = make()
        case_id = str(uuid.uuid4())
        jube.add_case(case_id, closedStatusId=3)
        jube.add_case(case_id)
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
        self.assertEqual(ctx.exception.reason, "jube_duplicate_cases")

    def test_tenant_and_case_ids_are_checked_before_any_call(self) -> None:
        adapter, jube, _ = make()
        cases = [
            (OTHER_TENANT, str(uuid.uuid4()), "not_found"),
            ("tenant-a", str(uuid.uuid4()), "missing_tenant_context"),
            (TENANT, "SYN-001", "invalid_case_id"),
        ]
        for tenant, case_id, code in cases:
            with self.subTest(code=code), self.assertRaises(cl.JubeAdapterError) as ctx:
                adapter.ensure_case(tenant, case_id, correlation_id=CORR)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(jube.calls, [])

    def test_link_failure_is_not_swallowed_and_a_retry_recovers_without_a_second_case(self) -> None:
        class FailingOnce(cl.InMemoryLinkStore):
            failed = False

            def record_jube_case(self, *args: Any) -> None:
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("mapping database unavailable")
                super().record_jube_case(*args)

        adapter, jube, _ = make(links=FailingOnce())
        case_id = str(uuid.uuid4())
        jube.archived.add(case_id)
        with self.assertRaises(RuntimeError):
            adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
        retry = adapter.ensure_case(TENANT, case_id, correlation_id=CORR)
        self.assertFalse(retry.changed)
        self.assertEqual((len(jube.cases), adapter.links.find_jube_case(TENANT, case_id)), (1, "1"))

    def test_in_memory_links_keep_one_jube_case_per_case(self) -> None:
        links = cl.InMemoryLinkStore()
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        links.record_jube_case(TENANT, first, "1", None)
        links.record_jube_case(TENANT, first, "1", None)  # replay
        for case_id, jube_id, reason in (
            (first, "2", "slot_taken"),
            (second, "1", "already_mapped"),
        ):
            with self.assertRaises(cl.JubeAdapterError) as ctx:
                links.record_jube_case(TENANT, case_id, jube_id, None)
            self.assertEqual(ctx.exception.reason, reason)


class ReadTests(unittest.TestCase):
    def test_unmapped_jube_status_is_an_error_not_a_guess(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube)
        jube.cases[1]["caseWorkflowStatusGuid"] = str(uuid.uuid4())
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.get_case(TENANT, case_id, correlation_id=CORR)
        self.assertEqual(ctx.exception.reason, "unmapped_jube_status")

    def test_case_without_link_or_missing_in_jube_is_not_found(self) -> None:
        adapter, jube, _ = make()
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.get_case(TENANT, str(uuid.uuid4()), correlation_id=CORR)
        self.assertEqual((ctx.exception.code, ctx.exception.reason), ("not_found", "no_jube_link"))
        case_id = created(adapter, jube)
        jube.cases.clear()
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.get_case(TENANT, case_id, correlation_id=CORR)
        self.assertEqual(ctx.exception.reason, "jube_case_missing")

    def test_auth_failures_are_not_retryable_and_hide_upstream_bodies(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube)
        jube.fail = [cl.Response(403, {"secret": "upstream detail"})]
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.get_case(TENANT, case_id, correlation_id=CORR)
        body = ctx.exception.as_error(CORR)
        self.assertEqual(
            (body["error"]["code"], body["error"]["retryable"]), ("upstream_unavailable", False)
        )
        self.assertNotIn("upstream detail", json.dumps(body))


class TransitionTests(unittest.TestCase):
    def test_transition_moves_jube_and_reports_what_jube_holds(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=2)
        jube.cases[1].update(locked=True, lockedUser="vati-case-adapter", diary=True)
        out = adapter.transition(
            TENANT, case_id, "VALIDATING", expected_status="RECEIVED", correlation_id=CORR
        )
        self.assertTrue(out.changed)
        self.assertEqual(out.case.status, "VALIDATING")
        sent = jube.puts[-1]
        self.assertEqual(sent["caseWorkflowStatusGuid"], STATUS_GUIDS["Validating"])
        self.assertEqual(
            (sent["locked"], sent["lockedUser"], sent["diary"], sent["rating"]),
            (True, "vati-case-adapter", True, 2),
            "unchanged fields are sent back as read",
        )
        self.assertEqual(sent["diaryDate"], jube.cases[1]["diaryDate"])

    def test_replay_sends_nothing_and_stale_requests_conflict(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        adapter.transition(
            TENANT, case_id, "INVESTIGATING", expected_status="RECEIVED", correlation_id=CORR
        )
        before = len(jube.puts)
        replay = adapter.transition(
            TENANT, case_id, "INVESTIGATING", expected_status="RECEIVED", correlation_id=CORR
        )
        self.assertFalse(replay.changed)
        self.assertEqual(len(jube.puts), before)
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.transition(
                TENANT,
                case_id,
                "AWAITING_EVIDENCE",
                expected_status="RECEIVED",
                correlation_id=CORR,
            )
        self.assertEqual((ctx.exception.code, ctx.exception.http_status), ("version_conflict", 409))
        self.assertEqual(ctx.exception.current["status"], "INVESTIGATING")

    def test_unrated_case_needs_the_officers_rating(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube)
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.transition(
                TENANT, case_id, "VALIDATING", expected_status="RECEIVED", correlation_id=CORR
            )
        self.assertEqual(ctx.exception.reason, "jube_rating_required")
        self.assertEqual(jube.puts, [])
        with self.assertRaises(cl.JubeAdapterError):
            adapter.transition(
                TENANT,
                case_id,
                "VALIDATING",
                expected_status="RECEIVED",
                correlation_id=CORR,
                rating=9,
            )
        out = adapter.transition(
            TENANT, case_id, "VALIDATING", expected_status="RECEIVED", correlation_id=CORR, rating=3
        )
        self.assertEqual(out.case.rating, 3)

    def test_resolution_needs_a_decision_closes_the_case_and_is_final_here(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.transition(
                TENANT, case_id, "RESOLVED", expected_status="RECEIVED", correlation_id=CORR
            )
        self.assertEqual(ctx.exception.field, "decision_id")
        out = adapter.transition(
            TENANT,
            case_id,
            "RESOLVED",
            expected_status="RECEIVED",
            correlation_id=CORR,
            decision_id=str(uuid.uuid4()),
        )
        self.assertEqual((out.case.status, out.case.closed_state), ("RESOLVED", "closed"))
        self.assertEqual(jube.puts[-1]["closedStatusId"], 3)
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.transition(
                TENANT, case_id, "INVESTIGATING", expected_status="RESOLVED", correlation_id=CORR
            )
        self.assertEqual(ctx.exception.reason, "case_closed")

    def test_case_locked_by_another_jube_user_is_a_conflict(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        jube.cases[1].update(locked=True, lockedUser="vati-officer-" + OFFICER)
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.transition(
                TENANT, case_id, "VALIDATING", expected_status="RECEIVED", correlation_id=CORR
            )
        self.assertEqual(
            (ctx.exception.code, ctx.exception.reason), ("version_conflict", "locked_in_jube")
        )

    def test_escalation_is_a_distinct_jube_status_projected_to_awaiting_human(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        out = adapter.escalate(TENANT, case_id, expected_status="RECEIVED", correlation_id=CORR)
        self.assertEqual(
            (out.case.status, out.case.escalated, out.case.jube_status),
            ("AWAITING_HUMAN", True, WORKFLOW.escalation_status),
        )
        self.assertFalse(
            adapter.escalate(
                TENANT, case_id, expected_status="RECEIVED", correlation_id=CORR
            ).changed
        )
        plain = adapter.transition(
            TENANT, case_id, "AWAITING_HUMAN", expected_status="AWAITING_HUMAN", correlation_id=CORR
        )
        self.assertTrue(plain.changed, "de-escalation is a real Jube status change")
        self.assertFalse(plain.case.escalated)

    def test_unknown_statuses_are_rejected_before_calling_jube(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        calls = len(jube.calls)
        for target, expected in (("CLOSED", "RECEIVED"), ("VALIDATING", "")):
            with self.assertRaises(cl.JubeAdapterError):
                adapter.transition(
                    TENANT, case_id, target, expected_status=expected, correlation_id=CORR
                )
        self.assertEqual(len(jube.calls), calls)


class AssignTests(unittest.TestCase):
    def test_assignment_is_the_jube_locked_user_and_is_replay_safe(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        out = adapter.assign(
            TENANT, case_id, OFFICER, expected_officer_id=None, correlation_id=CORR
        )
        self.assertEqual(
            (out.case.assigned_officer_id, out.case.assigned_jube_user, out.case.locked),
            (OFFICER, "vati-officer-" + OFFICER, False),
        )
        before = len(jube.puts)
        self.assertFalse(
            adapter.assign(
                TENANT, case_id, OFFICER, expected_officer_id=None, correlation_id=CORR
            ).changed
        )
        self.assertEqual(len(jube.puts), before)
        cleared = adapter.assign(
            TENANT, case_id, None, expected_officer_id=OFFICER, correlation_id=CORR
        )
        self.assertIsNone(cleared.case.assigned_officer_id)

    def test_stale_or_unprovisioned_assignments_fail_clearly(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.assign(
                TENANT, case_id, str(uuid.uuid4()), expected_officer_id=None, correlation_id=CORR
            )
        self.assertEqual(ctx.exception.reason, "officer_not_provisioned")
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.assign(
                TENANT, case_id, OFFICER, expected_officer_id=str(uuid.uuid4()), correlation_id=CORR
            )
        self.assertEqual(ctx.exception.reason, "stale_assignment")
        with self.assertRaises(cl.JubeAdapterError):
            adapter.assign(
                TENANT, case_id, "officer-1", expected_officer_id=None, correlation_id=CORR
            )

    def test_a_non_officer_jube_user_is_reported_without_an_officer_id(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        jube.cases[1]["lockedUser"] = "analyst.in.jube"
        case = adapter.get_case(TENANT, case_id, correlation_id=CORR)
        self.assertEqual(
            (case.assigned_officer_id, case.assigned_jube_user), (None, "analyst.in.jube")
        )


class RetryTests(unittest.TestCase):
    def test_reads_and_updates_retry_transient_failures_with_bounded_backoff(self) -> None:
        adapter, jube, sleeps = make()
        case_id = created(adapter, jube, rating=1)
        jube.fail = [cl.Response(503, None, retry_after=2.0), cl.TransportError(timed_out=False)]
        self.assertEqual(adapter.get_case(TENANT, case_id, correlation_id=CORR).status, "RECEIVED")
        self.assertEqual(sleeps[0], 2.0, "Retry-After is honoured")
        self.assertLessEqual(len(sleeps), 2)

    def test_gives_up_after_three_attempts(self) -> None:
        adapter, jube, _ = make()
        case_id = created(adapter, jube, rating=1)
        jube.fail = [cl.Response(503, None)] * 3
        with self.assertRaises(cl.JubeAdapterError) as ctx:
            adapter.get_case(TENANT, case_id, correlation_id=CORR)
        self.assertEqual(
            (ctx.exception.code, ctx.exception.retryable), ("upstream_unavailable", True)
        )
        self.assertEqual(sum(1 for m, p in jube.calls if p == "/api/Case/1"), 3)


class LinkStoreBindingTests(unittest.TestCase):
    def test_mapping_binding_uses_tax030_record_link_and_service_links(self) -> None:
        recorded: list[dict[str, Any]] = []

        def record_link(**kwargs: Any) -> dict[str, Any]:
            recorded.append(kwargs)
            return {"replayed": False}

        def case_service_links(*, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
            return [
                {"service": "frappe", "external_type": "Vati Submission", "external_id": "S-1"},
                *[
                    {
                        "service": r["service"],
                        "external_type": r["external_type"],
                        "external_id": r["external_id"],
                    }
                    for r in recorded
                ],
            ]

        store = cl.MappingLinkStore(record_link, case_service_links)
        case_id = str(uuid.uuid4())
        self.assertIsNone(store.find_jube_case(TENANT, case_id))
        event = str(uuid.uuid4())
        store.record_jube_case(TENANT, case_id, "7", event)
        self.assertEqual(
            recorded[0],
            {
                "tenant_id": TENANT,
                "case_id": case_id,
                "service": "jube",
                "external_type": "case",
                "external_id": "7",
                "source_event_id": event,
            },
        )
        self.assertEqual(store.find_jube_case(TENANT, case_id), "7")


class ProvisioningToolTests(unittest.TestCase):
    def test_admin_tenant_switch_creates_once_restores_and_refuses_landlord(self) -> None:
        state = {"current": 1, "tenants": [{"id": 1, "name": "Administrator"}], "puts": []}

        def call(method: str, path: str, body: Any = None, token: Any = None):
            if path == "/api/UserInTenant/GetCurrentTenantRegistry":
                return 200, {"tenantRegistryId": state["current"]}
            if path == "/api/TenantRegistry/" and method == "GET":
                return 200, state["tenants"]
            if path == "/api/TenantRegistry/" and method == "POST":
                state["tenants"].append({"id": 2, **body})
                return 200, None
            if path == "/api/UserInTenant/" and method == "PUT":
                state["current"] = body["tenantRegistryId"]
                state["puts"].append(body["tenantRegistryId"])
                return 200, None
            raise AssertionError(path)

        for _ in range(2):
            with jube_dev.admin_in_tenant("t", "Vati", "d", call) as tid:
                self.assertEqual((tid, state["current"]), (2, 2))
            self.assertEqual(state["current"], 1)
        self.assertEqual(len(state["tenants"]), 2)
        with self.assertRaises(SystemExit):
            with jube_dev.admin_in_tenant("t", "Administrator", "d", call):
                pass

    def test_landlord_invoker_is_revoked_and_deactivated(self) -> None:
        calls: list[tuple[str, str, Any]] = []
        user = {"id": 4, "name": jube_rules.LEGACY_INVOKER_USER, "active": True}

        def call(method: str, path: str, body: Any = None, token: Any = None):
            calls.append((method, path, body))
            if path == "/api/UserRegistry/" and method == "GET":
                return 200, [user]
            if path.startswith("/api/UserRegistryApiKey/ByUserRegistryId/"):
                return 200, [{"id": 11}]
            return 200, None

        done = jube_rules.retire_landlord_invoker(call, "t")
        self.assertEqual(done, ["revoked_landlord_api_key", "deactivated_landlord_user"])
        self.assertIn(("DELETE", "/api/UserRegistryApiKey/11", None), calls)
        self.assertFalse(calls[-1][2]["active"])

    def test_roles_keep_exactly_the_declared_permissions(self) -> None:
        grants = [
            {"id": 1, "roleRegistryId": 5, "permissionSpecificationId": 1, "active": True},
            {"id": 2, "roleRegistryId": 5, "permissionSpecificationId": 35, "active": True},
            {"id": 3, "roleRegistryId": 6, "permissionSpecificationId": 35, "active": True},
        ]
        calls: list[tuple[str, str]] = []

        def call(method: str, path: str, body: Any = None, token: Any = None):
            calls.append((method, path))
            if path == "/api/RoleRegistry/":
                return 200, [{"id": 5, "guid": "g5", "name": "Vati case adapter (local)"}]
            if path == "/api/RoleRegistryPermission/" and method == "GET":
                return 200, grants
            return 200, None

        identity = dict(WORKFLOW.identities["case_adapter"])
        _, done = jube_cases.ensure_role(call, "t", identity, {"Read Write Case": 1})
        self.assertEqual(done, ["removed_permission:35"])
        self.assertIn(("DELETE", "/api/RoleRegistryPermission/2"), calls)
        self.assertNotIn(
            ("DELETE", "/api/RoleRegistryPermission/3"), calls, "other roles untouched"
        )


if __name__ == "__main__":
    unittest.main()
