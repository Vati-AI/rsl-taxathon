"""Jube case lifecycle adapter (TAX-034).

Jube is the operational case authority (ADR-001). This module is a client plus a mapping
layer: it creates, reads, assigns, transitions and escalates the one Jube case that belongs
to a Vati ``case_id``, and projects Jube's state onto the Vati ``CaseStatus`` vocabulary in
``integration/api/openapi.json``. It never stores or invents a case status: every status it
returns was read from Jube, and an unmapped Jube status is an error, not a guess.

Routes and semantics come from the locked Jube source (``Jube.App/Endpoints/Repository/
CaseEndpoints.cs`` and ``Jube.Service/Repository/Case/CaseService.cs``) and the deployed
Swagger, recorded in ``integration/jube/README.md``:

- ``POST /api/Case/CreateFromCaseKeyValue`` raises a case from the latest archived model
  invocation whose payload has ``case_key == case_id``. Jube answers 409 when an open case
  already exists for that key and 404 when no archived invocation exists.
- ``GET /api/Case/`` lists the cases the caller's role and tenant can see (closed ones too);
  ``GET /api/Case/{id}`` reads one.
- ``PUT /api/Case/`` replaces status, closed status, lock, diary and rating. Jube requires a
  1-5 rating and a non-empty form payload on every update, writes one audit event per
  changed field and answers 409 when the case is locked to another Jube user.

Every operation is replay-safe so the durable outbox (TAX-033) and the Frappe bridge
(TAX-039) can retry it: creation looks up the Vati service link and then Jube before
creating; updates compare the requested target with Jube's current state and send nothing
when it already holds; a stale caller (``expected_*`` differs from Jube) gets
``version_conflict``. Jube has no version number or compare-and-set, so a write racing
another Jube user between our read and our write is last-writer-wins inside Jube.
"""

from __future__ import annotations

import json
import logging
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "integration" / "jube" / "case_workflow.v1.json"

# integration/api/openapi.json components.schemas.CaseStatus
CASE_STATUSES = (
    "RECEIVED",
    "VALIDATING",
    "INVESTIGATING",
    "AWAITING_EVIDENCE",
    "AWAITING_HUMAN",
    "RESOLVED",
    "FAILED_PROCESSING",
)
# Jube CaseDto.ClosedStatusId (Jube.Dto/Repository/Case/CaseDto.cs).
CLOSED_STATES = {0: "open", 1: "suspended", 2: "reopened", 3: "closed", 4: "bypassed"}
CLOSED = 3
SEMVER = re.compile(r"\d+\.\d+\.\d+")
RETRYABLE_HTTP = {429, 502, 503, 504}
MAX_RETRY_AFTER_S = 10.0
# Plain HTTP stays on the developer's machine: loopback, or host.docker.internal, Docker's
# name for that machine as seen from the Frappe developer container (TAX-085).
LOCAL_HTTP_HOSTS = ("127.0.0.1", "localhost", "::1", "host.docker.internal")

log = logging.getLogger("vati.jube_case")


class WorkflowError(ValueError):
    """The case workflow definition is not a valid, reviewable configuration."""


class ConfigError(ValueError):
    """Adapter configuration is missing or malformed (fails at startup, not mid-request)."""


class JubeAdapterError(Exception):
    """Structured failure; ``code`` is an ``ErrorCode`` from integration/api/openapi.json."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        retryable: bool = False,
        reason: str | None = None,
        field: str | None = None,
        upstream_status: int | None = None,
        current: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.reason = reason
        self.field = field
        self.upstream_status = upstream_status
        self.current = dict(current or {})

    def as_error(self, correlation_id: str) -> dict[str, dict[str, Any]]:
        """``ErrorResponse`` body; never includes Jube response bodies or credentials."""
        error: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "correlation_id": correlation_id,
            "service": "jube",
        }
        if self.field:
            error["field_errors"] = [{"field": self.field, "code": self.reason or self.code}]
        return {"error": error}


def _invalid(message: str, field: str, reason: str) -> JubeAdapterError:
    return JubeAdapterError("invalid_request", message, http_status=400, reason=reason, field=field)


def _domain(message: str, reason: str, field: str | None = None, **kw: Any) -> JubeAdapterError:
    return JubeAdapterError(
        "invalid_domain_input", message, http_status=422, reason=reason, field=field, **kw
    )


def _unavailable(message: str, reason: str, *, retryable: bool, **kw: Any) -> JubeAdapterError:
    return JubeAdapterError(
        "upstream_unavailable", message, http_status=503, retryable=retryable, reason=reason, **kw
    )


# --------------------------------------------------------------------------- workflow


@dataclass(frozen=True)
class StatusDef:
    name: str
    vati_status: str
    priority: int
    escalated: bool = False
    closes_case: bool = False
    requires_decision: bool = False


@dataclass(frozen=True)
class Workflow:
    version: str
    tenant_name: str
    tenant_description: str
    case_key: str
    name: str
    description: str
    initial_status: str
    escalation_status: str
    statuses: tuple[StatusDef, ...]
    identities: Mapping[str, Mapping[str, Any]]

    def status(self, name: str) -> StatusDef:
        for status in self.statuses:
            if status.name == name:
                return status
        raise WorkflowError(f"unknown Jube status {name!r}")

    def target_for(self, vati_status: str) -> StatusDef:
        """The Jube status a plain transition to ``vati_status`` moves the case to."""
        for status in self.statuses:
            if status.vati_status == vati_status and not status.escalated:
                return status
        raise WorkflowError(f"no Jube status for {vati_status}")


def validate_workflow(raw: Mapping[str, Any]) -> Workflow:
    if raw.get("synthetic") is not True:
        raise WorkflowError("workflow must be marked synthetic")
    if not SEMVER.fullmatch(str(raw.get("workflow_version", ""))):
        raise WorkflowError("workflow_version must be semantic (x.y.z)")
    statuses = []
    for item in raw.get("statuses", []):
        if item.get("vati_status") not in CASE_STATUSES:
            raise WorkflowError(f"status {item.get('name')!r} maps to an unknown Vati status")
        priority = item.get("priority")
        if not isinstance(priority, int) or not 0 <= priority <= 255:
            raise WorkflowError(f"status {item.get('name')!r} needs a 0-255 Jube priority")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise WorkflowError("every status needs a name")
        statuses.append(
            StatusDef(
                item["name"],
                item["vati_status"],
                priority,
                bool(item.get("escalated")),
                bool(item.get("closes_case")),
                bool(item.get("requires_decision")),
            )
        )
    names = [s.name for s in statuses]
    if len(set(names)) != len(names):
        raise WorkflowError("duplicate Jube status name")
    plain = [s.vati_status for s in statuses if not s.escalated]
    if sorted(plain) != sorted(CASE_STATUSES):
        raise WorkflowError("each Vati status needs exactly one non-escalated Jube status")
    for status in statuses:
        if status.closes_case != (status.vati_status == "RESOLVED"):
            raise WorkflowError("only the RESOLVED status closes the Jube case")
        if status.vati_status == "RESOLVED" and not status.requires_decision:
            raise WorkflowError("RESOLVED must require an approved decision reference")
    workflow = Workflow(
        version=raw["workflow_version"],
        tenant_name=str(raw.get("jube_tenant", {}).get("name", "")),
        tenant_description=str(raw.get("jube_tenant", {}).get("description", "")),
        case_key=str(raw.get("case_key", "")),
        name=str(raw.get("workflow", {}).get("name", "")),
        description=str(raw.get("workflow", {}).get("description", "")),
        initial_status=str(raw.get("initial_status", "")),
        escalation_status=str(raw.get("escalation_status", "")),
        statuses=tuple(statuses),
        identities=raw.get("identities", {}),
    )
    if not (workflow.tenant_name and workflow.case_key and workflow.name):
        raise WorkflowError("tenant, case_key and workflow name are required")
    if workflow.status(workflow.initial_status).vati_status != "RECEIVED":
        raise WorkflowError("new cases must start in a RECEIVED status")
    escalation = workflow.status(workflow.escalation_status)
    if not escalation.escalated or escalation.vati_status != "AWAITING_HUMAN":
        raise WorkflowError("escalation must be an escalated AWAITING_HUMAN status")
    for key in ("case_adapter", "officer"):
        if not isinstance(workflow.identities.get(key), Mapping):
            raise WorkflowError(f"identity {key} is required")
    return workflow


def load_workflow(path: Path = WORKFLOW_PATH) -> Workflow:
    return validate_workflow(json.loads(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- config


def _uuid(value: object, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        raise ConfigError(f"{field} must be a UUID") from None


@dataclass(frozen=True)
class AdapterConfig:
    base_url: str
    api_key: str = field(repr=False)
    vati_tenant_id: str
    workflow_guid: str
    status_guids: Mapping[str, str]
    timeout_s: float = 30.0
    max_attempts: int = 3

    @classmethod
    def build(
        cls,
        workflow: Workflow,
        *,
        base_url: str,
        api_key: str | None,
        vati_tenant_id: str,
        workflow_guid: str,
        status_guids: Mapping[str, str],
        timeout_s: float = 30.0,
        max_attempts: int = 3,
    ) -> AdapterConfig:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigError("JUBE_BASE_URL must be an http(s) URL")
        if parsed.scheme == "http" and parsed.hostname not in LOCAL_HTTP_HOSTS:
            raise ConfigError("plain HTTP to Jube is only allowed on loopback")
        if not api_key or len(api_key) < 20:
            raise ConfigError("the Jube case adapter API key is missing")
        guids = {name: _uuid(guid, f"status {name}") for name, guid in status_guids.items()}
        missing = sorted({s.name for s in workflow.statuses} - set(guids))
        if missing:
            raise ConfigError(f"no Jube status GUID for {missing}; run jube_cases.py provision")
        if len(set(guids.values())) != len(guids):
            raise ConfigError("two workflow statuses share a Jube GUID")
        if not 1 <= max_attempts <= 3:
            raise ConfigError("max_attempts must be 1-3 (docs/API_STANDARD.md)")
        return cls(
            base_url.rstrip("/"),
            api_key,
            _uuid(vati_tenant_id, "vati_tenant_id"),
            _uuid(workflow_guid, "workflow_guid"),
            guids,
            timeout_s,
            max_attempts,
        )


# --------------------------------------------------------------------------- transport


@dataclass(frozen=True)
class Response:
    status: int
    body: Any
    retry_after: float | None = None


class TransportError(Exception):
    def __init__(self, timed_out: bool) -> None:
        super().__init__("Jube request timed out" if timed_out else "Jube is unreachable")
        self.timed_out = timed_out


Transport = Callable[[str, str, Any], Response]


class HttpTransport:
    """urllib client authenticating with the adapter's ``X-API-KEY``; never logs the key."""

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 30.0) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout_s = timeout_s

    def __call__(self, method: str, path: str, body: Any = None) -> Response:
        request = urllib.request.Request(
            self._base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", "X-API-KEY": self._api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw, status, headers = response.read(), response.status, response.headers
        except urllib.error.HTTPError as error:
            raw, status, headers = error.read(), error.code, error.headers
        except (TimeoutError, socket.timeout):
            raise TransportError(timed_out=True) from None
        except (urllib.error.URLError, ConnectionError) as error:
            reason = getattr(error, "reason", None)
            raise TransportError(
                timed_out=isinstance(reason, (TimeoutError, socket.timeout))
            ) from None
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        return Response(
            status, parsed, _retry_after(headers.get("Retry-After") if headers else None)
        )


def _retry_after(value: str | None) -> float | None:
    try:
        return min(float(value), MAX_RETRY_AFTER_S) if value else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- links


class LinkStore(Protocol):
    """Where the Vati case -> Jube case link lives (``Vati Service Link``, TAX-030)."""

    def find_jube_case(self, tenant_id: str, case_id: str) -> str | None: ...

    def record_jube_case(
        self, tenant_id: str, case_id: str, jube_case_id: str, source_event_id: str | None
    ) -> None: ...


class MappingLinkStore:
    """Binds ``vati_tax.ids.mapping`` (``record_link``, ``case_service_links``) as a LinkStore.

    Passed as callables so this module stays importable without Frappe. ``record_link``
    enforces one Jube case per Vati case and makes a replayed event a no-op.
    """

    def __init__(
        self,
        record_link: Callable[..., Mapping[str, Any]],
        case_service_links: Callable[..., list[Mapping[str, Any]]],
    ) -> None:
        self._record_link = record_link
        self._case_service_links = case_service_links

    def find_jube_case(self, tenant_id: str, case_id: str) -> str | None:
        for link in self._case_service_links(tenant_id=tenant_id, case_id=case_id):
            if link.get("service") == "jube" and link.get("external_type") == "case":
                external_id = link.get("external_id")
                return str(external_id) if external_id else None
        return None

    def record_jube_case(
        self, tenant_id: str, case_id: str, jube_case_id: str, source_event_id: str | None
    ) -> None:
        self._record_link(
            tenant_id=tenant_id,
            case_id=case_id,
            service="jube",
            external_type="case",
            external_id=jube_case_id,
            source_event_id=source_event_id,
        )


class InMemoryLinkStore:
    """Test and local-smoke LinkStore with the same one-Jube-case-per-case rule."""

    def __init__(self) -> None:
        self.links: dict[tuple[str, str], str] = {}

    def find_jube_case(self, tenant_id: str, case_id: str) -> str | None:
        return self.links.get((tenant_id, case_id))

    def record_jube_case(
        self, tenant_id: str, case_id: str, jube_case_id: str, source_event_id: str | None
    ) -> None:
        existing = self.links.get((tenant_id, case_id))
        if existing not in (None, jube_case_id):
            raise _domain("This case already has a Jube case.", "slot_taken", "external_type")
        if any(v == jube_case_id and k != (tenant_id, case_id) for k, v in self.links.items()):
            raise _domain("This Jube case is mapped to another case.", "already_mapped")
        self.links[(tenant_id, case_id)] = jube_case_id


# --------------------------------------------------------------------------- adapter


@dataclass(frozen=True)
class JubeCase:
    """Read-only projection of one Jube case. Every field was read from Jube."""

    tenant_id: str
    case_id: str
    jube_case_id: int
    status: str
    jube_status: str
    escalated: bool
    closed_state: str
    assigned_officer_id: str | None
    assigned_jube_user: str | None
    locked: bool
    rating: int
    created_at: str | None
    status_authority: str = "jube"


@dataclass(frozen=True)
class Outcome:
    case: JubeCase
    changed: bool  # False: the request was already satisfied in Jube (a replay); nothing was sent


class JubeCaseAdapter:
    """Case lifecycle operations for one Vati tenant served by one Jube tenant."""

    def __init__(
        self,
        config: AdapterConfig,
        workflow: Workflow,
        link_store: LinkStore,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.workflow = workflow
        self.links = link_store
        self._send = transport or HttpTransport(config.base_url, config.api_key, config.timeout_s)
        self._sleep = sleep
        self._by_guid = {guid: workflow.status(name) for name, guid in config.status_guids.items()}
        prefix = workflow.identities["officer"].get("user_prefix")
        if not isinstance(prefix, str) or not prefix:
            raise ConfigError("officer user_prefix is required")
        self._officer_prefix = prefix

    # ----------------------------------------------------------------- public operations

    def ensure_case(
        self,
        tenant_id: str,
        case_id: str,
        *,
        correlation_id: str,
        source_event_id: str | None = None,
    ) -> Outcome:
        """Create the Jube case for ``case_id`` once; later calls return the same case.

        Order: the recorded Vati link, then a Jube lookup by case key (covers a crash between
        Jube creation and link recording, and a concurrent creator), then creation in the
        workflow's initial status. The risk model must already have been invoked for this
        ``case_id`` (Jube raises cases from archived invocations).
        """
        tenant_id, case_id = self._scope(tenant_id, case_id)
        linked = self.links.find_jube_case(tenant_id, case_id)
        if linked is not None:
            case = self._read(tenant_id, case_id, linked)
            self._log("ensure_case", "replayed_link", correlation_id, case)
            return Outcome(case, changed=False)
        found = self._find_by_key(tenant_id, case_id)
        if found is not None:
            self.links.record_jube_case(
                tenant_id, case_id, str(found.jube_case_id), source_event_id
            )
            self._log("ensure_case", "replayed_jube", correlation_id, found)
            return Outcome(found, changed=False)
        body = {
            "caseWorkflowGuid": self.config.workflow_guid,
            "caseWorkflowStatusGuid": self.config.status_guids[self.workflow.initial_status],
            "caseKey": self.workflow.case_key,
            "caseKeyValue": case_id,
        }
        changed = True
        try:
            response = self._send("POST", "/api/Case/CreateFromCaseKeyValue", body)
        except TransportError as error:
            # The create may have landed: never retry blindly, look it up instead.
            found = self._find_by_key(tenant_id, case_id)
            if found is None:
                raise self._transport_error(error) from None
        else:
            if response.status == 200 and isinstance(response.body, dict):
                found = self._project(tenant_id, case_id, response.body)
            elif response.status == 409:
                changed = False  # a concurrent creator won; adopt its case
                found = self._find_by_key(tenant_id, case_id)
                if found is None:
                    raise _unavailable(
                        "Jube reports an existing case that this adapter cannot see.",
                        "jube_case_not_visible",
                        retryable=False,
                        upstream_status=409,
                    )
            elif response.status == 404:
                raise _domain(
                    "Jube has no archived risk invocation for this case_id yet "
                    "(or the case workflow is not visible to the adapter).",
                    "risk_evaluation_missing",
                    "case_id",
                    retryable=True,
                    upstream_status=404,
                )
            else:
                raise self._status_error(response, "create")
        self.links.record_jube_case(tenant_id, case_id, str(found.jube_case_id), source_event_id)
        self._log("ensure_case", "created" if changed else "replayed_jube", correlation_id, found)
        return Outcome(found, changed=changed)

    def get_case(self, tenant_id: str, case_id: str, *, correlation_id: str) -> JubeCase:
        tenant_id, case_id = self._scope(tenant_id, case_id)
        linked = self.links.find_jube_case(tenant_id, case_id)
        if linked is None:
            raise JubeAdapterError(
                "not_found", "Case has no Jube case.", http_status=404, reason="no_jube_link"
            )
        case = self._read(tenant_id, case_id, linked)
        self._log("get_case", "ok", correlation_id, case)
        return case

    def assign(
        self,
        tenant_id: str,
        case_id: str,
        officer_id: str | None,
        *,
        expected_officer_id: str | None,
        correlation_id: str,
        rating: int | None = None,
    ) -> Outcome:
        """Record the assigned officer as the Jube case's ``LockedUser`` (lock left unchanged).

        Jube has no separate assignee field; ``LockedUser`` is the user a case belongs to and
        must be a Jube user of the same tenant (``vati-officer-<officer_id>``). ``None``
        unassigns. The authorisation decision (supervisor ``case.assign``) is the caller's.
        """
        target = None if officer_id is None else self.officer_user(officer_id)
        expected = None if expected_officer_id is None else self.officer_user(expected_officer_id)
        tenant_id, case_id = self._scope(tenant_id, case_id)
        jube_id, raw, case = self._current(tenant_id, case_id)
        if case.assigned_jube_user == target:
            self._log("assign", "replayed", correlation_id, case)
            return Outcome(case, changed=False)
        if case.assigned_jube_user != expected:
            raise self._stale("assignment", case)
        self._refuse_closed(case)
        body = self._update_body(raw, case, rating, locked_user=target)
        updated = self._put(tenant_id, case_id, jube_id, body)
        self._log("assign", "updated", correlation_id, updated)
        return Outcome(updated, changed=True)

    def transition(
        self,
        tenant_id: str,
        case_id: str,
        target_status: str,
        *,
        expected_status: str,
        correlation_id: str,
        decision_id: str | None = None,
        rating: int | None = None,
    ) -> Outcome:
        """Move the Jube case to the workflow status that projects to ``target_status``.

        RESOLVED also closes the Jube case and needs the approved decision record's ID
        (Frappe, TAX-046); approving it is not this adapter's job. A closed case is never
        reopened here. Escalation has its own operation.
        """
        if target_status not in CASE_STATUSES:
            raise _invalid("Unknown target status.", "status", "unknown_status")
        if expected_status not in CASE_STATUSES:
            raise _invalid("expected_status is required.", "expected_status", "unknown_status")
        target = self.workflow.target_for(target_status)
        if target.requires_decision:
            # Proof that an approved decision exists; recording it is Frappe's (TAX-046).
            _require_uuid(decision_id, "decision_id")
        return self._move(
            "transition", tenant_id, case_id, target, expected_status, correlation_id, rating
        )

    def escalate(
        self,
        tenant_id: str,
        case_id: str,
        *,
        expected_status: str,
        correlation_id: str,
        rating: int | None = None,
    ) -> Outcome:
        """Move the case to the workflow's escalation status (higher Jube priority).

        It projects to AWAITING_HUMAN with ``escalated`` true, so the Vati status stays one of
        the contract's values while Jube shows a distinct, prioritised status.
        """
        if expected_status not in CASE_STATUSES:
            raise _invalid("expected_status is required.", "expected_status", "unknown_status")
        target = self.workflow.status(self.workflow.escalation_status)
        return self._move(
            "escalate", tenant_id, case_id, target, expected_status, correlation_id, rating
        )

    def officer_user(self, officer_id: str) -> str:
        return self._officer_prefix + _require_uuid(officer_id, "officer_id")

    # ----------------------------------------------------------------- internals

    def _move(
        self,
        action: str,
        tenant_id: str,
        case_id: str,
        target: StatusDef,
        expected_status: str,
        correlation_id: str,
        rating: int | None,
    ) -> Outcome:
        tenant_id, case_id = self._scope(tenant_id, case_id)
        jube_id, raw, case = self._current(tenant_id, case_id)
        if case.jube_status == target.name:
            self._log(action, "replayed", correlation_id, case)
            return Outcome(case, changed=False)
        if case.status != expected_status:
            raise self._stale("status", case)
        self._refuse_closed(case)
        body = self._update_body(raw, case, rating, status=target)
        updated = self._put(tenant_id, case_id, jube_id, body)
        self._log(action, "updated", correlation_id, updated)
        return Outcome(updated, changed=True)

    def _scope(self, tenant_id: str, case_id: str) -> tuple[str, str]:
        tenant = _require_uuid(tenant_id, "tenant_id", code="missing_tenant_context")
        case = _require_uuid(case_id, "case_id", code="invalid_case_id")
        if tenant != self.config.vati_tenant_id:
            # Concealed like a missing case: this Jube tenant does not serve that Vati tenant.
            raise JubeAdapterError(
                "not_found", "Case not found.", http_status=404, reason="tenant_not_served"
            )
        return tenant, case

    def _current(self, tenant_id: str, case_id: str) -> tuple[str, dict[str, Any], JubeCase]:
        linked = self.links.find_jube_case(tenant_id, case_id)
        if linked is None:
            raise JubeAdapterError(
                "not_found", "Case has no Jube case.", http_status=404, reason="no_jube_link"
            )
        raw = self._get_raw(linked)
        return linked, raw, self._project(tenant_id, case_id, raw)

    def _read(self, tenant_id: str, case_id: str, jube_case_id: str) -> JubeCase:
        return self._project(tenant_id, case_id, self._get_raw(jube_case_id))

    def _get_raw(self, jube_case_id: str) -> dict[str, Any]:
        if not jube_case_id.isdigit():
            raise _unavailable("Malformed Jube case link.", "bad_jube_link", retryable=False)
        response = self._call("GET", f"/api/Case/{jube_case_id}")
        if response.status == 404:
            raise JubeAdapterError(
                "not_found",
                "The linked Jube case is missing or not visible to the adapter.",
                http_status=404,
                reason="jube_case_missing",
                upstream_status=404,
            )
        if response.status != 200 or not isinstance(response.body, dict):
            raise self._status_error(response, "read")
        return response.body

    def _find_by_key(self, tenant_id: str, case_id: str) -> JubeCase | None:
        response = self._call("GET", "/api/Case/")
        if response.status != 200 or not isinstance(response.body, list):
            raise self._status_error(response, "list")
        matches = [
            row
            for row in response.body
            if isinstance(row, dict)
            and row.get("caseKey") == self.workflow.case_key
            and str(row.get("caseKeyValue", "")).lower() == case_id
        ]
        if len(matches) > 1:
            raise _domain(
                "Jube holds more than one case for this case_id; resolve it in Jube.",
                "jube_duplicate_cases",
                "case_id",
            )
        return self._project(tenant_id, case_id, matches[0]) if matches else None

    def _project(self, tenant_id: str, case_id: str, raw: Mapping[str, Any]) -> JubeCase:
        if (
            raw.get("caseKey") != self.workflow.case_key
            or str(raw.get("caseKeyValue", "")).lower() != case_id
        ):
            raise _unavailable(
                "Jube returned a case for another key.", "jube_case_mismatch", retryable=False
            )
        status = self._by_guid.get(str(raw.get("caseWorkflowStatusGuid", "")).lower())
        if status is None:
            # Never guess: an unmapped Jube status means the workflow and config disagree.
            raise _unavailable(
                "The Jube case is in a status this adapter does not map.",
                "unmapped_jube_status",
                retryable=False,
            )
        user = raw.get("lockedUser") or None
        officer = None
        if isinstance(user, str) and user.startswith(self._officer_prefix):
            try:
                officer = str(uuid.UUID(user[len(self._officer_prefix) :]))
            except ValueError:
                officer = None
        closed = raw.get("closedStatusId")
        return JubeCase(
            tenant_id=tenant_id,
            case_id=case_id,
            jube_case_id=int(raw["id"]),
            status=status.vati_status,
            jube_status=status.name,
            escalated=status.escalated,
            closed_state=CLOSED_STATES.get(closed, "unknown")
            if isinstance(closed, int)
            else "unknown",
            assigned_officer_id=officer,
            assigned_jube_user=user,
            locked=bool(raw.get("locked")),
            rating=int(raw.get("rating") or 0),
            created_at=raw.get("createdDate"),
        )

    def _update_body(
        self,
        raw: Mapping[str, Any],
        case: JubeCase,
        rating: int | None,
        *,
        status: StatusDef | None = None,
        locked_user: str | None | object = ...,
    ) -> dict[str, Any]:
        """Full ``CaseDto`` for PUT: unchanged fields are sent back exactly as read."""
        if rating is not None and (not isinstance(rating, int) or not 1 <= rating <= 5):
            raise _invalid("rating must be 1-5.", "rating", "invalid_rating")
        effective = rating if rating is not None else case.rating
        if not 1 <= effective <= 5:
            raise _domain(
                "Jube requires an investigator rating (1-5) before any case update; "
                "this case has none, so the request must carry the officer's rating.",
                "jube_rating_required",
                "rating",
            )
        closed = raw.get("closedStatusId", 0)
        if status is not None and status.closes_case:
            closed = CLOSED
        return {
            "id": raw["id"],
            "caseWorkflowStatusGuid": (
                self.config.status_guids[status.name]
                if status is not None
                else raw["caseWorkflowStatusGuid"]
            ),
            "closedStatusId": closed,
            "locked": bool(raw.get("locked")),
            "lockedUser": raw.get("lockedUser") if locked_user is ... else locked_user,
            "diary": bool(raw.get("diary")),
            "diaryDate": raw.get("diaryDate"),
            "rating": effective,
            # Jube requires a form snapshot; empty means no form fields (our statuses send no
            # notification or HTTP dispatch, which is what Payload would feed).
            "payload": "{}",
        }

    def _put(self, tenant_id: str, case_id: str, jube_id: str, body: dict[str, Any]) -> JubeCase:
        response = self._call("PUT", "/api/Case/", body)
        if response.status == 409:
            raise JubeAdapterError(
                "version_conflict",
                "The Jube case is locked to another Jube user.",
                http_status=409,
                reason="locked_in_jube",
                upstream_status=409,
            )
        if response.status == 400:
            codes = _validation_codes(response.body)
            if "LockedUserInTenant" in codes:
                raise _domain(
                    "The officer has no Jube user in this tenant; provision it first.",
                    "officer_not_provisioned",
                    "officer_id",
                    upstream_status=400,
                )
            raise _domain(
                "Jube rejected the update.",
                "jube_validation_failed",
                upstream_status=400,
            )
        if response.status == 204:  # Jube's answer for a case it no longer finds
            raise JubeAdapterError(
                "not_found",
                "The linked Jube case is missing.",
                http_status=404,
                reason="jube_case_missing",
            )
        if response.status != 200:
            raise self._status_error(response, "update")
        return self._read(tenant_id, case_id, jube_id)  # report what Jube now holds

    def _call(self, method: str, path: str, body: Any = None) -> Response:
        """GET/PUT with bounded retries (both idempotent); POST is never retried here."""
        attempts = self.config.max_attempts if method in ("GET", "PUT") else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._send(method, path, body)
            except TransportError as error:
                if attempt == attempts:
                    raise self._transport_error(error) from None
                self._sleep(_backoff(attempt, None))
                continue
            if response.status in RETRYABLE_HTTP and attempt < attempts:
                self._sleep(_backoff(attempt, response.retry_after))
                continue
            return response
        raise AssertionError("unreachable")

    def _status_error(self, response: Response, action: str) -> JubeAdapterError:
        status = response.status
        if status in (401, 403):
            return _unavailable(
                f"Jube refused the adapter's {action} request; check its API key and role grants.",
                "jube_auth",
                retryable=False,
                upstream_status=status,
            )
        if status == 429:
            return JubeAdapterError(
                "rate_limited",
                "Jube rate-limited the adapter.",
                http_status=429,
                retryable=True,
                upstream_status=status,
            )
        return _unavailable(
            f"Jube {action} failed.",
            "jube_error",
            retryable=status >= 500,
            upstream_status=status,
        )

    @staticmethod
    def _transport_error(error: TransportError) -> JubeAdapterError:
        if error.timed_out:
            return JubeAdapterError(
                "upstream_timeout",
                "Jube did not answer in time.",
                http_status=503,
                retryable=True,
                reason="jube_timeout",
            )
        return _unavailable("Jube is unreachable.", "jube_unreachable", retryable=True)

    @staticmethod
    def _stale(what: str, case: JubeCase) -> JubeAdapterError:
        return JubeAdapterError(
            "version_conflict",
            f"The case {what} changed in Jube; re-read it before retrying.",
            http_status=409,
            reason=f"stale_{what}",
            current={"status": case.status, "assigned_officer_id": case.assigned_officer_id},
        )

    @staticmethod
    def _refuse_closed(case: JubeCase) -> None:
        if case.closed_state == "closed":
            raise _domain("The Jube case is closed.", "case_closed", "case_id")

    def _log(self, action: str, outcome: str, correlation_id: str, case: JubeCase) -> None:
        log.info(
            json.dumps(
                {
                    "service": "jube",
                    "action": action,
                    "outcome": outcome,
                    "correlation_id": correlation_id,
                    "tenant_id": case.tenant_id,
                    "case_id": case.case_id,
                    "jube_case_id": case.jube_case_id,
                    "status": case.status,
                }
            )
        )


def _require_uuid(value: object, field: str, code: str = "invalid_request") -> str:
    try:
        if isinstance(value, str) and value:
            return str(uuid.UUID(value))
    except ValueError:
        pass
    raise JubeAdapterError(
        code, f"{field} must be a UUID.", http_status=400, field=field, reason="invalid_uuid"
    )


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    return min(0.5 * 2 ** (attempt - 1), 4.0) + random.uniform(0, 0.25)  # noqa: S311


def _validation_codes(body: Any) -> set[str]:
    """``errorCode`` values from a FluentValidation ``ValidationResult`` body."""
    if not isinstance(body, dict):
        return set()
    errors = body.get("errors") or body.get("Errors") or []
    return {
        str(e.get("errorCode") or e.get("ErrorCode"))
        for e in errors
        if isinstance(e, dict) and (e.get("errorCode") or e.get("ErrorCode"))
    }
