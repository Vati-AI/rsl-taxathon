"""Frappe-free tests for the /api/v1 router and projections (TAX-090).

The router is driven with the contract's own synthetic examples
(integration/api/examples/http): each example's request goes through ``core.handle``
and the actual response must pass ``contract.check_example`` with the example's
status and error code. Handlers here are stand-ins; the Frappe handlers are
exercised on a disposable site by ``test_api_v1_bench.py``.
"""

from __future__ import annotations

import copy
import datetime
import importlib
import json
import pathlib
import unittest
import uuid

from vati_tax.api_v1 import core, management, projections
from vati_tax.api_v1.operations import SERVED

contract = importlib.import_module("integration.api.contract")
SPEC = contract.load_spec()
EXAMPLES = sorted(contract.HTTP_EXAMPLES.glob("*.json"))
TENANT = "10000000-0000-4000-8000-000000000001"
CASE = "40000000-0000-4000-8000-000000000001"
CORRELATION = "c0000000-0000-4000-8000-0000000000aa"


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def request_of(example: dict) -> core.ApiRequest:
    req = example["request"]
    return core.ApiRequest(
        method=req["method"],
        path=req["path"],
        headers=req.get("headers", {}),
        query=req.get("query", {}),
        body=req.get("body"),
    )


def checked(example: dict, response: core.ApiResponse) -> list[str]:
    actual = copy.deepcopy(example)
    actual["response"] = {
        "status": response.status,
        "headers": response.headers,
        "body": response.body,
    }
    return contract.check_example(SPEC, actual)


def schema(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def valid(value: object, name: str) -> list[str]:
    return contract.validate(value, schema(name), SPEC)


class Recorder:
    """Handlers that record their calls and answer with a fixed outcome."""

    def __init__(self, outcome=None):
        self.calls: list[core.Call] = []
        self.outcome = outcome

    def handlers(self) -> dict:
        def handler(call: core.Call) -> core.Result:
            self.calls.append(call)
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

        return {op: handler for op in SERVED}


class ExampleTests(unittest.TestCase):
    """Every contract example for a served operation, through the live router."""

    def served(self):
        for path in EXAMPLES:
            example = load(path)
            if example["operation_id"] in SERVED:
                yield path.name, example

    def test_examples_cover_the_served_operations(self):
        covered = {example["operation_id"] for _, example in self.served()}
        self.assertGreaterEqual(len(covered), 10)

    def test_success_examples_keep_status_headers_and_body(self):
        for name, example in self.served():
            res = example["response"]
            if res["status"] >= 400:
                continue
            with self.subTest(name):
                replayed = res.get("headers", {}).get("Idempotent-Replayed") == "true"
                rec = Recorder(core.Result(copy.deepcopy(res["body"]), replayed=replayed))
                out = core.handle(
                    request_of(example), rec.handlers(), authenticated=True, spec=SPEC
                )
                self.assertEqual(checked(example, out), [])
                self.assertEqual(out.status, res["status"])
                self.assertEqual(out.headers[core.MODE_HEADER], "live")
                self.assertEqual(len(rec.calls), 1)
                self.assertEqual(
                    rec.calls[0].tenant_id, example["request"]["headers"]["X-Vati-Tenant-ID"]
                )

    def test_request_rule_examples_are_refused_before_any_handler(self):
        for name, example in self.served():
            if example.get("request_conformant", True):
                continue
            with self.subTest(name):
                rec = Recorder(AssertionError("handler must not run"))
                out = core.handle(
                    request_of(example), rec.handlers(), authenticated=True, spec=SPEC
                )
                self.assertEqual(rec.calls, [])
                self.assertEqual(out.status, example["response"]["status"])
                self.assertEqual(
                    out.body["error"]["code"], example["response"]["body"]["error"]["code"]
                )
                self.assertEqual(checked(example, out), [])

    def test_handler_errors_use_the_example_envelope(self):
        for name, example in self.served():
            res = example["response"]
            if res["status"] < 400 or not example.get("request_conformant", True):
                continue
            with self.subTest(name):
                error = res["body"]["error"]
                exc = core.ApiError(
                    error["code"],
                    error["message"],
                    field_errors=error.get("field_errors", []),
                    current_version=error.get("current_version"),
                )
                out = core.handle(
                    request_of(example), Recorder(exc).handlers(), authenticated=True, spec=SPEC
                )
                self.assertEqual(out.status, res["status"])
                self.assertEqual(out.body, res["body"] | {"error": out.body["error"]})
                self.assertEqual(out.body["error"]["code"], error["code"])
                self.assertEqual(checked(example, out), [])


def get(path: str, tenant: str | None = TENANT, **query: str) -> core.ApiRequest:
    headers = {"X-Correlation-ID": CORRELATION}
    if tenant:
        headers["X-Vati-Tenant-ID"] = tenant
    return core.ApiRequest("GET", path, headers, query)


class RouterTests(unittest.TestCase):
    def test_guest_is_unauthenticated(self):
        rec = Recorder(core.Result({"data": []}))
        out = core.handle(get("/api/v1/cases"), rec.handlers(), authenticated=False, spec=SPEC)
        self.assertEqual((out.status, out.body["error"]["code"]), (401, "unauthenticated"))
        self.assertEqual(out.headers["X-Correlation-ID"], CORRELATION)
        self.assertEqual(rec.calls, [])

    def test_unserved_and_unknown_operations_are_not_found(self):
        rec = Recorder(core.Result({"data": {}}))
        create = core.ApiRequest(
            "POST",
            "/api/v1/cases",
            {"X-Vati-Tenant-ID": TENANT, "Idempotency-Key": "k" * 16},
            body={},
        )
        for request in (create, get("/api/v1/nothing"), get("/api/v2/cases"), get("/cases")):
            with self.subTest(request.path):
                out = core.handle(request, rec.handlers(), authenticated=True, spec=SPEC)
                self.assertEqual((out.status, out.body["error"]["code"]), (404, "not_found"))
        self.assertEqual(rec.calls, [])

    def test_invalid_json_and_get_bodies_are_refused(self):
        rec = Recorder(core.Result({"data": {}}))
        bad = core.ApiRequest(
            "GET", f"/api/v1/cases/{CASE}", {"X-Vati-Tenant-ID": TENANT}, body_invalid=True
        )
        with_body = core.ApiRequest(
            "GET", f"/api/v1/cases/{CASE}", {"X-Vati-Tenant-ID": TENANT}, body={"a": 1}
        )
        for request in (bad, with_body):
            out = core.handle(request, rec.handlers(), authenticated=True, spec=SPEC)
            self.assertEqual((out.status, out.body["error"]["code"]), (400, "invalid_request"))
        self.assertEqual(rec.calls, [])

    def test_a_new_correlation_id_is_issued_when_missing_or_malformed(self):
        rec = Recorder(core.ApiError("not_found"))
        request = core.ApiRequest(
            "GET",
            f"/api/v1/cases/{CASE}",
            {"X-Vati-Tenant-ID": TENANT, "X-Correlation-ID": "not-a-uuid"},
        )
        out = core.handle(request, rec.handlers(), authenticated=True, spec=SPEC)
        issued = out.headers["X-Correlation-ID"]
        self.assertNotEqual(issued, "not-a-uuid")
        self.assertEqual(str(uuid.UUID(issued)), issued)
        self.assertEqual(out.body["error"]["correlation_id"], issued)

    def test_unexpected_errors_are_reported_and_never_echoed(self):
        seen = []
        rec = Recorder(RuntimeError("secret detail /srv/site/private"))
        out = core.handle(
            get(f"/api/v1/cases/{CASE}"),
            rec.handlers(),
            authenticated=True,
            spec=SPEC,
            on_error=lambda kind, exc: seen.append((kind, type(exc).__name__)),
        )
        self.assertEqual((out.status, out.body["error"]["code"]), (503, "upstream_unavailable"))
        self.assertNotIn("secret", json.dumps(out.body))
        self.assertEqual(seen, [("unhandled", "RuntimeError")])

    def test_translate_maps_domain_errors(self):
        class StaleVersion(Exception):
            pass

        rec = Recorder(StaleVersion())
        out = core.handle(
            get(f"/api/v1/cases/{CASE}"),
            rec.handlers(),
            authenticated=True,
            spec=SPEC,
            translate=lambda exc: core.ApiError("tenant_scope_denied"),
        )
        self.assertEqual((out.status, out.body["error"]["code"]), (403, "tenant_scope_denied"))

    def test_records_of_another_tenant_or_case_are_never_sent(self):
        other = str(uuid.uuid4())
        for body in (
            {"data": {"case_id": CASE, "tenant_id": other}},
            {"data": [{"case_id": other, "tenant_id": TENANT}], "meta": {}},
        ):
            seen = []
            out = core.handle(
                get(f"/api/v1/cases/{CASE}/evidence"),
                Recorder(core.Result(body)).handlers(),
                authenticated=True,
                spec=SPEC,
                on_error=lambda kind, exc: seen.append(kind),
            )
            self.assertEqual(out.status, 503)
            self.assertNotIn(other, json.dumps(out.body))
            self.assertTrue(seen[0].startswith("scope_mismatch:"))

    def test_fixture_role_header_is_ignored(self):
        rec = Recorder(core.Result({"data": []}))
        request = core.ApiRequest(
            "GET",
            "/api/v1/cases",
            {"X-Vati-Tenant-ID": TENANT, "X-Vati-Fixture-Role": "supervisor"},
        )
        core.handle(request, rec.handlers(), authenticated=True, spec=SPEC)
        self.assertEqual(rec.calls[0].tenant_id, TENANT)
        self.assertNotIn("x-vati-fixture-role", rec.calls[0].query)


class PagingTests(unittest.TestCase):
    ITEMS = [{"id": f"{i:03d}"} for i in range(7)]

    def key(self, item):
        return (item["id"],)

    def test_pages_follow_the_cursor_without_gaps_or_repeats(self):
        seen, cursor = [], None
        while True:
            query = {"limit": "3", **({"cursor": cursor} if cursor else {})}
            result = core.page(list(reversed(self.ITEMS)), self.key, query)
            seen += [i["id"] for i in result["data"]]
            self.assertEqual(valid(result["meta"], "PageMeta"), [])
            if not result["meta"]["has_more"]:
                self.assertIsNone(result["meta"]["next_cursor"])
                break
            cursor = result["meta"]["next_cursor"]
        self.assertEqual(seen, [i["id"] for i in self.ITEMS])

    def test_default_limit_and_empty_list(self):
        self.assertEqual(len(core.page(self.ITEMS * 5, lambda i: (i["id"],), {})["data"]), 25)
        empty = core.page([], self.key, {})
        self.assertEqual(empty, {"data": [], "meta": {"next_cursor": None, "has_more": False}})

    def test_malformed_cursors_are_invalid_requests(self):
        for cursor in ("%%%", core.encode_cursor(["x"])[:-2] + "!!", "bnVsbA", "W10"):
            with self.subTest(cursor):
                with self.assertRaises(core.ApiError) as caught:
                    core.page(self.ITEMS, self.key, {"cursor": cursor})
                self.assertEqual(caught.exception.code, "invalid_request")


NOW = datetime.datetime(2026, 11, 11, 9, 30, tzinfo=datetime.UTC)
TAXPAYER = {
    "taxpayer_id": "20000000-0000-4000-8000-000000000001",
    "tenant_id": TENANT,
    "display_name": "Synthetic Trading (Pty) Ltd",
    "taxpayer_type": "company",
    "tin_masked": "******1234",
    "registered_on": datetime.date(2019, 4, 1),
    "sector": "retail",
    "synthetic": 1,
}
IDENTITY = {
    "case_id": CASE,
    "tenant_id": TENANT,
    "display_id": "CASE-000001",
    "taxpayer_id": TAXPAYER["taxpayer_id"],
    "submission_id": "30000000-0000-4000-8000-000000000001",
    "synthetic": 1,
    "creation": datetime.datetime(2026, 11, 10, 8, 0),
}
LINK_ROW = {
    "service": "jube",
    "external_type": "case",
    "external_id": "JUBE-17",
    "sync_state": "synced",
    "last_attempt_at": datetime.datetime(2026, 11, 10, 8, 1),
    "error_code": None,
}


def make_case(**overrides):
    args = {
        "taxpayer": TAXPAYER,
        "status": "AWAITING_HUMAN",
        "version": 3,
        "refund_claim": projections.money(12500.5, "LSL"),
        "lane": "investigation",
        "links": projections.service_links([LINK_ROW, {"service": "rsl", "external_id": "x"}]),
        "updated_at": NOW,
    }
    return projections.case(IDENTITY, **{**args, **overrides})


class ProjectionTests(unittest.TestCase):
    def test_case_conforms_and_maps_lane_and_money(self):
        case = make_case()
        self.assertEqual(valid(case, "Case"), [])
        self.assertEqual(case["refund_lane"], "targeted")
        self.assertEqual(case["refund_claim"], {"amount": "12500.50", "currency": "LSL"})
        self.assertEqual([link["service"] for link in case["service_links"]], ["jube"])
        self.assertEqual(case["updated_at"], "2026-11-11T09:30:00Z")

    def test_unreported_case_is_received_with_nothing_invented(self):
        case = make_case(status=None, refund_claim=None, lane=None, links=[], updated_at=None)
        self.assertEqual(valid(case, "Case"), [])
        self.assertEqual(case["status"], "RECEIVED")
        self.assertIsNone(case["assigned_officer_id"])
        self.assertEqual(case["selection_reasons"], [])
        self.assertEqual(case["created_at"], case["updated_at"])

    def test_money_is_a_contract_decimal(self):
        self.assertEqual(projections.money(0, "lsl"), {"amount": "0.00", "currency": "LSL"})
        self.assertEqual(projections.money("-0.001", "LSL"), {"amount": "0.00", "currency": "LSL"})
        for empty in ((None, "LSL"), ("", "LSL"), (5, None), ("abc", "LSL"), ("NaN", "LSL")):
            self.assertIsNone(projections.money(*empty))

    def test_taxpayer_profile_conforms(self):
        profile = projections.taxpayer_profile(
            TAXPAYER,
            open_cases=[{"case_id": CASE, "display_id": "CASE-000001", "status": "INVESTIGATING"}],
        )
        self.assertEqual(valid(profile, "TaxpayerProfile"), [])
        self.assertNotIn("tin", profile)
        self.assertEqual(profile["registered_on"], "2019-04-01")

    def test_evidence_view_loses_vati_only_fields(self):
        view = {
            "evidence_id": str(uuid.uuid4()),
            "case_id": None,
            "tenant_id": TENANT,
            "taxpayer_id": TAXPAYER["taxpayer_id"],
            "version": 1,
            "supersedes_evidence_id": None,
            "lineage_id": str(uuid.uuid4()),
            "document_sha256": "a" * 64,
            "media_type": "application/pdf",
            "size_bytes": 2048,
            "document_type": "invoice",
            "source": "taxpayer_portal",
            "received_at": "2026-11-10T08:00:00Z",
            "extraction": {"status": "completed", "extractor_version": "1", "error_code": None},
            "service_links": [],
            "synthetic": True,
            "reference": {"doctype": "Vati Submission", "name": "x"},
        }
        item = projections.evidence(view, case_id=CASE)
        self.assertEqual(valid(item, "Evidence"), [])
        self.assertEqual(item["case_id"], CASE)

    def test_entity_ids_are_stable_uuids_and_documents_are_left_out(self):
        ftm = {
            "id": "f" * 64,
            "schema": "Company",
            "properties": {"name": ["Synthetic Supplier"], "registrationNumber": 1234, "x": None},
        }
        extension = {"evidence": [{"evidence_id": "e2"}, {"evidence_id": "e1"}, {}]}
        candidate = {
            "candidate_id": "Q1",
            "dataset": "sanctions",
            "dataset_version": "2026-11-01",
            "score": "0.81",
            "review_status": "unreviewed",
            "reviewed_by": None,
        }
        item = projections.entity(
            tenant_id=TENANT,
            case_id=CASE,
            ftm=ftm,
            extension=extension,
            index_row={"indexed_at": NOW, "reindex_required": 0},
            candidates=[candidate],
            synthetic=True,
        )
        assert item is not None
        item["evidence_ids"] = [
            str(uuid.uuid5(uuid.NAMESPACE_URL, e)) for e in item["evidence_ids"]
        ]
        self.assertEqual(valid(item, "Entity"), [])
        self.assertEqual(item["entity_id"], projections.entity_uuid(TENANT, "f" * 64))
        self.assertNotEqual(item["entity_id"], projections.entity_uuid(str(uuid.uuid4()), "f" * 64))
        self.assertEqual(item["properties"]["registrationNumber"], ["1234"])
        self.assertNotIn("x", item["properties"])
        self.assertEqual(item["service_links"][0]["external_id"], "f" * 64)
        document = projections.entity(
            tenant_id=TENANT, case_id=CASE, ftm={**ftm, "schema": "Document"},
            extension=None, index_row=None, candidates=[], synthetic=True,
        )  # fmt: skip
        self.assertIsNone(document)

    def test_officer_ids_are_stable_per_login(self):
        self.assertEqual(
            core.officer_id("Auditor@Example.test"), core.officer_id("auditor@example.test")
        )
        self.assertNotEqual(core.officer_id("a@example.test"), core.officer_id("b@example.test"))

    def decision_view(self, **overrides):
        view = {
            "decision_id": str(uuid.uuid4()),
            "case_id": CASE,
            "tenant_id": TENANT,
            "status": "proposed",
            "origin": "officer",
            "outcome": "close_no_further_action",
            "operation": "transition",
            "target_status": "RESOLVED",
            "rationale": "Synthetic rationale citing the reconciled invoices.",
            "evidence_ids": [str(uuid.uuid4())],
            "finding_ids": [],
            "rating": 3,
            "draft_id": None,
            "proposed_by": "auditor@example.test",
            "proposed_at": "2026-11-10T09:00:00Z",
            "case_status_at_proposal": "AWAITING_HUMAN",
            "case_version": 2,
            "reviewed_by": None,
            "reviewed_at": None,
            "review_reason": None,
            "jube_sync": {
                "state": "not_started",
                "attempts": 0,
                "error_code": None,
                "retryable": False,
                "last_attempt_at": None,
                "synced_at": None,
                "changed": None,
                "status_after": None,
                "status_authority": "jube",
            },  # fmt: skip
            "replayed": False,
        }
        return {**view, **overrides}

    def test_decision_maps_officers_to_uuids(self):
        decision = projections.decision(self.decision_view(reviewed_by="sup@example.test"))
        self.assertEqual(valid(decision, "Decision"), [])
        self.assertEqual(decision["proposed_by"], core.officer_id("auditor@example.test"))
        self.assertEqual(decision["reviewed_by"], core.officer_id("sup@example.test"))
        self.assertNotIn("replayed", decision)

    def test_case_review_conforms(self):
        state = {
            "case_id": CASE,
            "tenant_id": TENANT,
            "case_version": 2,
            "viewer": {
                "actor": "sup@example.test",
                "roles": ["supervisor", "service"],
                "allowed_actions": ["decision.propose", "decision.approve"],
            },
            "open_decision": self.decision_view(),
            "audit_events": [
                {
                    "event_id": str(uuid.uuid4()),
                    "sequence": 2,
                    "actor": "auditor@example.test",
                    "actor_role": "auditor",
                    "action": "decision.propose",
                    "subject_type": "decision",
                    "subject_id": str(uuid.uuid4()),
                    "occurred_at": "2026-11-10T09:00:00Z",
                    "correlation_id": str(uuid.uuid4()),
                    "payload_hash": "b" * 64,
                    "outcome": "proposed",
                }
            ],
        }
        review = projections.case_review(state)
        self.assertEqual(valid(review, "CaseReview"), [])
        self.assertEqual(review["viewer"]["roles"], ["supervisor"])
        self.assertNotIn("payload_hash", review["audit_events"][0])

    def test_evidence_request_conforms(self):
        view = {
            "request_id": str(uuid.uuid4()),
            "case_id": CASE,
            "tenant_id": TENANT,
            "status": "recorded",
            "authorised_by": "auditor@example.test",
            "finding_ids": [str(uuid.uuid4())],
            "items": [
                {"document_type": "contract", "description": "The signed service agreement."}
            ],
            "due_date": "2026-11-25",
            "case_version": 3,
            "service_links": [],
            "created_at": "2026-11-11T09:00:00Z",
            "replayed": True,
        }
        item = projections.evidence_request(view)
        self.assertEqual(valid(item, "EvidenceRequest"), [])
        self.assertEqual(item["authorised_by"], core.officer_id("auditor@example.test"))


class ManagementTests(unittest.TestCase):
    def test_live_summary_with_no_ledger_lists_the_gap(self):
        until = NOW
        body = management.summary(
            tenant_id=TENANT,
            until=until,
            window_days=30,
            open_cases=[
                {
                    "case_id": CASE,
                    "status": "AWAITING_EVIDENCE",
                    "assigned_officer_id": None,
                    "created_at": until - datetime.timedelta(hours=10),
                },
                {
                    "case_id": "c2",
                    "status": "INVESTIGATING",
                    "assigned_officer_id": None,
                    "created_at": None,
                },
            ],  # fmt: skip
            closed_cases=[
                {
                    "case_id": "c3",
                    "created_at": until - datetime.timedelta(days=3),
                    "closed_at": until - datetime.timedelta(days=1),
                    "outcome": "close_after_correction",
                },
                {"case_id": "c4", "created_at": None, "closed_at": until, "outcome": None},
                {"case_id": "c5", "created_at": None, "closed_at": None, "outcome": None},
            ],  # fmt: skip
            evidence_requests=[
                {
                    "case_id": CASE,
                    "status": "sent",
                    "due_date": until.date() - datetime.timedelta(days=1),
                },
                {"case_id": CASE, "status": "responded", "due_date": until.date()},
            ],  # fmt: skip
            findings=[
                {"case_id": CASE, "legal_route_status": "unverified"},
                {"case_id": "elsewhere", "legal_route_status": "unverified"},
            ],
            officers={},
            viewer_login="sup@example.test",
            entries=None,
            synthetic=True,
            extra_missing=[{"metric": "workload", "detail": "Assignments are not mirrored."}],
        )
        self.assertEqual(valid(body, "ManagementSummary"), [])
        self.assertEqual(body["unresolved_evidence"]["overdue_requests"], 1)
        self.assertEqual(body["closure_outcomes"]["closed_in_window"], 2)
        self.assertEqual(
            body["closure_outcomes"]["after_correction"], {"numerator": 1, "denominator": 1}
        )
        self.assertEqual(body["legal_route_coverage"]["unverified"], 1)
        metrics = [m["metric"] for m in body["missing_data"]]
        self.assertEqual(
            metrics,
            ["workload", "open_case_age", "time_to_close", "closure_outcomes", "assurance"],
        )
        self.assertFalse(body["assurance"]["available"])


if __name__ == "__main__":
    unittest.main()
