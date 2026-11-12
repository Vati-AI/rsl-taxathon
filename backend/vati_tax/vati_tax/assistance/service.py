"""Taxpayer-assistance operations behind /api/v1 (TAX-100; ADR-031, ADR-034).

Frappe-free. ``vati_tax.assistance.store`` supplies the ports from Frappe records;
the tests supply fakes. The five TAX-096 operations:

- ``explainValidationResults`` / ``reviewSubmissionDocuments``: the assistant
  (TAX-098) answers for the taxpayer's own editable submission. Nothing is stored.
- ``draftEvidenceResponse``: the assistant (TAX-099) drafts an answer to the
  taxpayer's own open evidence request; the draft and its disclosure are stored with
  status ``draft``. Nothing is sent.
- ``getEvidenceResponseDraft``: the taxpayer's own stored draft.
- ``confirmEvidenceResponseDraft``: the only step that sends anything. It is a
  separate, audited and idempotent step: the taxpayer's final answers go through the
  unchanged TAX-045 response rules (``respond`` port), which record the response,
  move the request to ``responded`` and write the ledger. No model is called.

**Permission context.** Only a caller whose sole role in the header tenant is
``taxpayer`` may use these operations (officers, specialists, supervisors and services
get ``403 forbidden``), and only for the taxpayer named in the path through their own
membership (anything else is ``404 not_found``, exactly like a missing taxpayer). The
``records`` port returns that taxpayer's own records only, in the shape the assistant
expects; officer-side records are never read, so the assistant cannot see them.

**Defence in depth.** An assistant answer is checked before it leaves: the declared
success status, the closed contract schema, no field name the taxpayer contract
forbids, and the caller's own tenant, taxpayer and IDs. A failing answer becomes
``503 upstream_unavailable`` and is reported, never sent. Without a configured
assistant runtime the AI operations are ``503`` too; nothing is invented.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import importlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from vati_tax.api_v1.core import ApiError, Call, Result

TAXPAYER = "taxpayer"
EDITABLE_SUBMISSIONS = frozenset({"draft", "correction_needed"})
OPEN_REQUESTS = frozenset({"open", "recorded", "sent"})
AI_OPERATIONS = ("explainValidationResults", "reviewSubmissionDocuments", "draftEvidenceResponse")
PATHS = {
    "explainValidationResults": "validation-explanations",
    "reviewSubmissionDocuments": "document-reviews",
    "draftEvidenceResponse": "evidence-response-drafts",
}
SUCCESS = {"explainValidationResults": 200, "reviewSubmissionDocuments": 200,
           "draftEvidenceResponse": 201}  # fmt: skip
NAMESPACE = uuid.UUID("10100000-0000-4000-8000-000000000100")

Assistant = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Runtime = Callable[[Mapping[str, Any]], Assistant]


@dataclass(frozen=True)
class Caller:
    """The signed-in principal's memberships in the header tenant."""

    user: str
    tenant_id: str
    roles: frozenset[str]
    taxpayer_ids: frozenset[str]


class ResponseRejected(Exception):
    """The TAX-045 rules refused the confirmed answers (422 with field errors)."""

    def __init__(self, code: str, message: str, field_errors: Sequence[Mapping[str, Any]] = ()):
        super().__init__(message)
        self.code = code
        self.field_errors = [dict(f) for f in field_errors]


class Drafts(Protocol):
    def get(self, tenant_id: str, taxpayer_id: str, draft_id: str) -> dict[str, Any] | None: ...

    def by_key(self, tenant_id: str, taxpayer_id: str, key: str) -> dict[str, Any] | None: ...

    def save(self, record: dict[str, Any]) -> None: ...


@dataclass
class Ports:
    caller: Callable[[str], Caller]
    # Only the taxpayer's own records, in the TAX-102 store shape (no "internal").
    records: Callable[[str, str], dict[str, Any]]
    runtime: Callable[[], Runtime | None]
    drafts: Drafts
    # TAX-045: record the response; returns the evidence response ID.
    respond: Callable[[str, str, str, dict[str, Any]], str]
    ledger: Callable[..., None]
    clock: Callable[[], datetime.datetime] = field(
        default=lambda: datetime.datetime.now(datetime.UTC)
    )
    report: Callable[[str], None] = field(default=lambda kind: None)


def _contract() -> Any:
    return importlib.import_module("integration.api.contract")


def digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AssistanceService:
    def __init__(self, ports: Ports, spec: dict[str, Any] | None = None) -> None:
        self.ports = ports
        self._spec = spec

    @property
    def spec(self) -> dict[str, Any]:
        if self._spec is None:
            self._spec = _contract().load_spec()
        return self._spec

    # --- permission context -------------------------------------------------------

    def authorise(self, call: Call, action: str) -> Caller:
        caller = self.ports.caller(call.tenant_id)
        taxpayer_id = call.params.get("taxpayer_id", "")
        if caller.roles != {TAXPAYER}:
            self.ports.ledger(action, "denied", tenant_id=call.tenant_id, user=caller.user,
                              reason="not_taxpayer")  # fmt: skip
            raise ApiError("forbidden", "These operations are for taxpayers.")
        if taxpayer_id not in caller.taxpayer_ids:
            self.ports.ledger(action, "denied", tenant_id=call.tenant_id, user=caller.user,
                              reason="not_own_taxpayer")  # fmt: skip
            raise ApiError("not_found")
        return caller

    def _store(self, caller: Caller, taxpayer_id: str) -> dict[str, Any]:
        store = self.ports.records(caller.tenant_id, taxpayer_id)
        # Belt and braces: whatever the port returns, only own records go on.
        own = taxpayer_id
        return {
            "tenant_id": caller.tenant_id,
            "taxpayers": {k: v for k, v in (store.get("taxpayers") or {}).items() if k == own},
            "submissions": {
                k: v for k, v in (store.get("submissions") or {}).items()
                if v.get("taxpayer_id") == own
            },
            "documents": {
                k: v for k, v in (store.get("documents") or {}).items()
                if v.get("taxpayer_id") == own
            },
            "evidence_requests": {
                k: v for k, v in (store.get("evidence_requests") or {}).items()
                if v.get("taxpayer_id") == own
            },
            "validation_history": [
                h for h in store.get("validation_history") or [] if h.get("taxpayer_id") == own
            ],
            "drafts": {},
            "outbox": [],
        }  # fmt: skip

    # --- AI operations ------------------------------------------------------------

    def _ask(self, call: Call, caller: Caller, store: dict[str, Any]) -> dict[str, Any]:
        runtime = self.ports.runtime()
        if runtime is None:
            raise ApiError("upstream_unavailable", "The assistant is not available.")
        taxpayer_id = call.params["taxpayer_id"]
        request = {
            "operation_id": call.operation_id,
            "method": "POST",
            "path": f"/api/v1/taxpayers/{taxpayer_id}/assistance/{PATHS[call.operation_id]}",
            "path_params": {"taxpayer_id": taxpayer_id},
            "caller": {
                "audience": TAXPAYER,
                "taxpayer_id": taxpayer_id,
                "tenant_id": caller.tenant_id,
            },  # fmt: skip
            "headers": {
                "X-Vati-Tenant-ID": caller.tenant_id,
                "X-Correlation-ID": call.correlation_id,
                "Idempotency-Key": call.idempotency_key or call.correlation_id,
            },  # fmt: skip
            "body": copy.deepcopy(call.body),
        }
        before = digest({k: store[k] for k in ("submissions", "documents", "evidence_requests")})
        try:
            response = runtime(store)(request)
        except Exception:  # an assistant failure is a dependency failure, never echoed
            self.ports.report("assistant_failed")
            raise ApiError("upstream_unavailable", "The assistant could not answer.") from None
        if (
            digest({k: store[k] for k in ("submissions", "documents", "evidence_requests")})
            != before
        ):
            self.ports.report("assistant_changed_records")
            raise ApiError("upstream_unavailable", "The assistant could not answer.")
        status = response.get("status") if isinstance(response, Mapping) else None
        body = response.get("body") if isinstance(response, Mapping) else None
        if status != SUCCESS[call.operation_id] or not isinstance(body, Mapping):
            error = (body or {}).get("error") if isinstance(body, Mapping) else None
            code = error.get("code") if isinstance(error, Mapping) else None
            if code in ("invalid_domain_input", "not_found", "upstream_unavailable"):
                # The assistant's own refusal (scope or input): pass the code, not text.
                raise ApiError(str(code))
            self.ports.report("assistant_bad_status")
            raise ApiError("upstream_unavailable", "The assistant could not answer.")
        self._check_answer(call, caller, body)
        return copy.deepcopy(dict(body))

    def _check_answer(self, call: Call, caller: Caller, body: Mapping[str, Any]) -> None:
        contract = _contract()
        problems: list[str] = []
        match = contract.match_operation(self.spec, "POST", self._path(call))
        if match is None:
            problems.append("no_operation")
        else:
            declared = contract._deref(
                self.spec, match[1]["responses"][str(SUCCESS[call.operation_id])]
            )
            schema = declared["content"]["application/json"]["schema"]
            if contract.validate(dict(body), schema, self.spec):
                problems.append("schema")
        names = [k for _, d in _walk(body) for k in d if contract.taxpayer_forbidden(str(k))]
        if names:
            problems.append("forbidden_field")
        data = body.get("data") or {}
        for key, want in (("tenant_id", caller.tenant_id),
                          ("taxpayer_id", call.params["taxpayer_id"]),
                          ("submission_id", (call.body or {}).get("submission_id")),
                          ("request_id", (call.body or {}).get("request_id"))):  # fmt: skip
            if key in data and want is not None and data[key] != want:
                problems.append(f"scope_{key}")
        if problems:
            self.ports.report("assistant_answer_refused:" + ",".join(problems))
            raise ApiError("upstream_unavailable", "The assistant could not answer.")

    @staticmethod
    def _path(call: Call) -> str:
        return (
            f"/api/v1/taxpayers/{call.params['taxpayer_id']}/assistance/{PATHS[call.operation_id]}"
        )

    def explain_or_review(self, call: Call) -> Result:
        caller = self.authorise(call, "assistance." + call.operation_id)
        store = self._store(caller, call.params["taxpayer_id"])
        submission = store["submissions"].get(str((call.body or {}).get("submission_id")))
        if submission is None:
            raise ApiError("invalid_domain_input", "The submission was not found.",
                           field_errors=[{"field": "submission_id", "code": "not_found"}])  # fmt: skip
        if submission.get("status") not in EDITABLE_SUBMISSIONS:
            raise ApiError("invalid_domain_input", "The submission can no longer be changed.",
                           field_errors=[{"field": "submission_id",
                                          "code": "submission_not_editable"}])  # fmt: skip
        body = self._ask(call, caller, store)
        self.ports.ledger("assistance." + call.operation_id, "answered",
                          tenant_id=caller.tenant_id, taxpayer_id=call.params["taxpayer_id"],
                          user=caller.user, subject_id=submission["submission_id"])  # fmt: skip
        return Result(body)

    # --- drafts -------------------------------------------------------------------

    def _view(self, record: Mapping[str, Any], store: Mapping[str, Any] | None) -> dict[str, Any]:
        data = copy.deepcopy(dict(record["data"]))
        data.update(status=record["status"], version=record["version"],
                    confirmation=copy.deepcopy(record.get("confirmation")),
                    sent_to_rsl=record["status"] == "confirmed")  # fmt: skip
        if record["status"] == "draft" and store is not None:
            req = store["evidence_requests"].get(record["request_id"])
            if req is None or req.get("status") not in OPEN_REQUESTS:
                data["status"] = "request_closed"
        return data

    def draft(self, call: Call) -> Result:
        caller = self.authorise(call, "assistance.draftEvidenceResponse")
        taxpayer_id = call.params["taxpayer_id"]
        key = call.idempotency_key or ""
        request_digest = digest(call.body)
        stored = self.ports.drafts.by_key(caller.tenant_id, taxpayer_id, key) if key else None
        if stored is not None:
            if stored["request_digest"] != request_digest:
                raise ApiError("idempotency_conflict")
            store = self._store(caller, taxpayer_id)
            return Result({"data": self._view(stored, store)}, replayed=True)
        store = self._store(caller, taxpayer_id)
        request_id = str((call.body or {}).get("request_id"))
        req = store["evidence_requests"].get(request_id)
        if req is None:
            raise ApiError("invalid_domain_input", "The evidence request was not found.",
                           field_errors=[{"field": "request_id", "code": "not_found"}])  # fmt: skip
        if req.get("status") not in OPEN_REQUESTS:
            raise ApiError("invalid_domain_input", "This request no longer accepts a response.",
                           field_errors=[{"field": "request_id", "code": "request_closed"}])  # fmt: skip
        body = self._ask(call, caller, store)
        data = dict(body["data"])
        # The stored draft has its own ID and starts at version 1; nothing is sent.
        data.update(
            draft_id=str(uuid.uuid5(NAMESPACE, f"{caller.tenant_id}/{taxpayer_id}/{key}")),
            status="draft", version=1, confirmation=None, sent_to_rsl=False,
            created_at=_iso(self.ports.clock()),
        )  # fmt: skip
        record = {
            "draft_id": data["draft_id"],
            "tenant_id": caller.tenant_id,
            "taxpayer_id": taxpayer_id,
            "request_id": request_id,
            "idempotency_key": key,
            "request_digest": request_digest,
            "status": "draft",
            "version": 1,
            "data": data,
            # Provenance a reviewer can trace; never shown beyond the disclosure.
            "disclosure": copy.deepcopy(data.get("disclosure")),
            "confirmation": None,
            "confirm_key": None,
            "confirm_digest": None,
        }
        self.ports.drafts.save(record)
        self.ports.ledger("assistance.draft_saved", "saved", tenant_id=caller.tenant_id,
                          taxpayer_id=taxpayer_id, user=caller.user,
                          subject_id=record["draft_id"])  # fmt: skip
        return Result({"data": self._view(record, store)})

    def get_draft(self, call: Call) -> Result:
        caller = self.authorise(call, "assistance.getEvidenceResponseDraft")
        taxpayer_id = call.params["taxpayer_id"]
        record = self.ports.drafts.get(
            caller.tenant_id, taxpayer_id, call.params.get("draft_id", "")
        )
        if record is None:
            raise ApiError("not_found")
        return Result({"data": self._view(record, self._store(caller, taxpayer_id))})

    def confirm(self, call: Call) -> Result:
        """The only step that sends: TAX-045 records the response, once."""
        caller = self.authorise(call, "assistance.confirmEvidenceResponseDraft")
        taxpayer_id = call.params["taxpayer_id"]
        record = self.ports.drafts.get(
            caller.tenant_id, taxpayer_id, call.params.get("draft_id", "")
        )
        if record is None:
            raise ApiError("not_found")
        body = call.body or {}
        key = call.idempotency_key or ""
        confirm_digest = digest(body)
        store = self._store(caller, taxpayer_id)
        if record["status"] == "confirmed":
            if record.get("confirm_key") == key:
                if record.get("confirm_digest") != confirm_digest:
                    raise ApiError("idempotency_conflict")
                return Result({"data": self._view(record, store)}, replayed=True)
            raise ApiError("version_conflict", current_version=int(record["version"]))
        if body.get("expected_version") != record["version"]:
            raise ApiError("version_conflict", current_version=int(record["version"]))
        if body.get("declaration") is not True:
            raise ApiError("invalid_domain_input", "Confirm that the answers are yours.")
        req = store["evidence_requests"].get(record["request_id"])
        if req is None or req.get("status") not in OPEN_REQUESTS:
            raise ApiError("invalid_domain_input", "This request no longer accepts a response.",
                           field_errors=[{"field": "request_id", "code": "request_closed"}])  # fmt: skip
        answers = []
        for item in body.get("items") or []:
            answers.append(
                {
                    # Contract items are 1-based; TAX-045 indexes them from 0.
                    "item_index": int(item["item_number"]) - 1,
                    "evidence_ids": list(item.get("evidence_ids") or []),
                    "unavailable_reason": item.get("reason_unavailable"),
                }
            )
        response_key = (
            "assist-"
            + hashlib.sha256(
                f"{caller.tenant_id}|{taxpayer_id}|{record['draft_id']}|{key}".encode()
            ).hexdigest()[:48]
        )
        try:
            response_id = self.ports.respond(
                caller.tenant_id,
                taxpayer_id,
                record["request_id"],
                {"items": answers, "message": body.get("message"), "idempotency_key": response_key},
            )
        except ResponseRejected as exc:
            self.ports.ledger("assistance.draft_confirmed", "refused", tenant_id=caller.tenant_id,
                              taxpayer_id=taxpayer_id, user=caller.user,
                              subject_id=record["draft_id"])  # fmt: skip
            raise ApiError(exc.code, str(exc), field_errors=[
                {"field": _contract_field(str(f.get("field", ""))), "code": str(f.get("code", ""))}
                for f in exc.field_errors
            ]) from None  # fmt: skip
        record = {
            **record,
            "status": "confirmed",
            "version": int(record["version"]) + 1,
            "confirmation": {
                "confirmed_at": _iso(self.ports.clock()),
                "items": copy.deepcopy(list(body.get("items") or [])),
                "message": body.get("message"),
                "evidence_response_id": response_id,
            },
            "confirm_key": key,
            "confirm_digest": confirm_digest,
        }
        self.ports.drafts.save(record)
        self.ports.ledger("assistance.draft_confirmed", "confirmed", tenant_id=caller.tenant_id,
                          taxpayer_id=taxpayer_id, user=caller.user,
                          subject_id=record["draft_id"])  # fmt: skip
        return Result({"data": self._view(record, store)})

    # --- dispatch -----------------------------------------------------------------

    def handlers(self) -> dict[str, Callable[[Call], Result]]:
        return {
            "explainValidationResults": self.explain_or_review,
            "reviewSubmissionDocuments": self.explain_or_review,
            "draftEvidenceResponse": self.draft,
            "getEvidenceResponseDraft": self.get_draft,
            "confirmEvidenceResponseDraft": self.confirm,
        }


def _contract_field(field: str) -> str:
    """TAX-045 ``items.N`` (0-based) -> the contract's ``items.N`` (1-based) item."""
    head, _, tail = field.partition(".")
    if head == "items" and tail.isdigit():
        return f"items.{int(tail) + 1}"
    return field


def _walk(value: Any, where: str = "$") -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, Mapping):
        out.append((where, dict(value)))
        for k, v in value.items():
            out += _walk(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _walk(v, f"{where}[{i}]")
    return out
