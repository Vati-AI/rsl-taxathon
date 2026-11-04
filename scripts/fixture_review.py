"""Stateful fixture for the officer review operations (TAX-042).

Serves getCaseReview, listFindings, proposeDecision, reviewDecision and
requestEvidence from integration/api/fixtures/case_review.json so the auditor UI
can be developed and reviewed without Frappe or Jube. It follows the TAX-046
rules (ADR-013) closely enough for the UI to show every refusal: permissions,
separation of duties, stale versions, expired proposals, a case that moved, one
open decision per case and idempotent replays. It is not the decision engine.

State lives in this server process only and resets when the server restarts.
Nothing is sent to Jube or Frappe: an approval records a fixture Jube result,
and every response carries ``X-Vati-Mode: fixture``.

The fixture principal comes from the fixture-only ``X-Vati-Fixture-Role`` header
(auditor, specialist or supervisor; auditor when absent). Real servers ignore it
and resolve the principal from authentication (TAX-029).
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "integration" / "api" / "fixtures" / "case_review.json"

ROLE_HEADER = "x-vati-fixture-role"
OPERATIONS = {
    "getCaseReview",
    "listFindings",
    "proposeDecision",
    "reviewDecision",
    "requestEvidence",
}

# TAX-029 action roles (backend/vati_tax/vati_tax/identity/policy.py).
ACTION_ROLES = {
    "decision.propose": {"auditor", "specialist", "supervisor"},
    "specialist.refer": {"auditor", "supervisor"},
    "evidence_request.authorise": {"auditor", "supervisor"},
    "decision.approve": {"supervisor"},
}
# ADR-013 outcomes: (operation, target status, closes the case).
OUTCOMES = {
    "close_no_further_action": ("transition", "RESOLVED", True),
    "close_after_correction": ("transition", "RESOLVED", True),
    "request_further_evidence": ("transition", "AWAITING_EVIDENCE", False),
    "continue_investigation": ("transition", "INVESTIGATING", False),
    "escalate_to_supervisor": ("escalate", "AWAITING_HUMAN", False),
    "refer_to_specialist": ("escalate", "AWAITING_HUMAN", False),
}
MAX_PROPOSAL_AGE = datetime.timedelta(hours=72)
MAX_DUE_DAYS = 180
MESSAGES = {
    "forbidden": "Your role does not permit this action on this case.",
    "version_conflict": "The case changed since you loaded it; reload and try again.",
    "invalid_domain_input": "The request breaks a review rule.",
    "idempotency_conflict": "This Idempotency-Key was already used with a different request.",
    "not_found": "The requested resource was not found.",
    "invalid_request": "The request is not valid for this operation.",
}
DETAIL = {
    "decision_pending": "Another decision on this case is still open.",
    "stale_case_version": "The case changed since you loaded it; reload and try again.",
    "case_state_changed": "The case status in Jube differs from the one you reviewed.",
    "proposal_expired": "The proposal is too old to approve; review the case again.",
    "not_pending": "Only a proposed decision can be reviewed.",
    "self_approval": "The proposer cannot approve or reject their own decision.",
    "citation_required": "A closing decision must cite at least one finding or evidence item.",
    "case_closed": "The case is already resolved.",
    "no_change": "The case is already in the status this outcome sets.",
}

Response = tuple[int, dict[str, str], dict[str, Any]]


class Refusal(Exception):
    def __init__(
        self,
        code: str,
        field: str | None = None,
        reason: str | None = None,
        current_version: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.reason = reason
        self.current_version = current_version


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def load_review(path: Path = REVIEW_PATH) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError("fixture data must be marked synthetic")
    return data


class ReviewStore:
    """In-memory review state for one fixture server process."""

    def __init__(
        self,
        review: dict[str, Any] | None = None,
        now: datetime.datetime | None = None,
    ) -> None:
        self.review = review if review is not None else load_review()
        self.started = now or datetime.datetime.now(datetime.UTC)
        self.fixed_now = now
        self.lock = threading.Lock()
        self.versions: dict[str, int] = {}
        self.jube_status: dict[str, str] = {}
        self.decisions: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.requests: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, tuple[str, Response]] = {}
        self._seeded = False

    # --- time and principals ------------------------------------------------------

    def now(self) -> datetime.datetime:
        return self.fixed_now or datetime.datetime.now(datetime.UTC)

    def _ago(self, hours: float) -> datetime.datetime:
        return self.started - datetime.timedelta(hours=hours)

    def principal(self, headers: dict[str, str]) -> dict[str, Any]:
        role = headers.get(ROLE_HEADER) or "auditor"
        found = self.review["principals"].get(role)
        if found is None:
            raise Refusal("invalid_request", "X-Vati-Fixture-Role", "unknown_fixture_role")
        return {"role": role, **found}

    # --- seeding ------------------------------------------------------------------

    def _seed(self, cases: list[dict[str, Any]]) -> None:
        if self._seeded:
            return
        self._seeded = True
        for case in cases:
            self.versions[case["case_id"]] = case["version"]
            self.jube_status[case["case_id"]] = case["status"]
            self.events[case["case_id"]] = []
        tenant = cases[0]["tenant_id"] if cases else ""
        for seed in self.review["decisions"]:
            case_id = seed["case_id"]
            proposer = self.review["principals"][seed["proposed_by"]]
            proposed_at = self._ago(seed["hours_ago"])
            version = self.versions[case_id] - 1
            record = self._new_decision(
                case_id=case_id,
                tenant=tenant,
                body=seed,
                officer=proposer["officer_id"],
                when=proposed_at,
                version=version,
                status_now=self.jube_status[case_id],
            )
            record["decision_id"] = seed["decision_id"]
            self.decisions[record["decision_id"]] = record
            self._event(
                case_id,
                proposer["officer_id"],
                proposer["roles"][0],
                "decision.proposed",
                "decision",
                record["decision_id"],
                "proposed",
                version,
                proposed_at,
            )

    # --- views --------------------------------------------------------------------

    def overlay_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """A case with the status a fixture approval moved it to (Jube projection)."""
        status = self.jube_status.get(case["case_id"])
        if status is None or status == case["status"]:
            return case
        return {**case, "status": status, "updated_at": _iso(self.now())}

    def _open_decision(self, case_id: str) -> dict[str, Any] | None:
        for record in self.decisions.values():
            if record["case_id"] == case_id and record["status"] == "proposed":
                return record
        return None

    def _allowed(self, who: dict[str, Any], open_decision: dict[str, Any] | None) -> list[str]:
        roles = set(who["roles"])
        allowed = []
        for action in ACTION_ROLES:
            if not roles & ACTION_ROLES[action]:
                continue
            if action == "decision.approve" and (
                open_decision is None or open_decision["proposed_by"] == who["officer_id"]
            ):
                continue
            allowed.append(action)
        return allowed

    def _findings(self, case: dict[str, Any]) -> list[dict[str, Any]]:
        found = []
        for seed in self.review["findings"]:
            if seed["case_id"] != case["case_id"]:
                continue
            item = {k: v for k, v in seed.items() if k != "hours_ago"}
            item["tenant_id"] = case["tenant_id"]
            item["created_at"] = _iso(self._ago(seed["hours_ago"]))
            found.append(item)
        return found

    def _new_decision(
        self,
        *,
        case_id: str,
        tenant: str,
        body: dict[str, Any],
        officer: str,
        when: datetime.datetime,
        version: int,
        status_now: str,
    ) -> dict[str, Any]:
        operation, target, _ = OUTCOMES[body["outcome"]]
        return {
            "decision_id": str(uuid.uuid4()),
            "case_id": case_id,
            "tenant_id": tenant,
            "status": "proposed",
            "origin": "officer",
            "outcome": body["outcome"],
            "operation": operation,
            "target_status": target,
            "rationale": body["rationale"],
            "evidence_ids": list(body.get("evidence_ids") or []),
            "finding_ids": list(body.get("finding_ids") or []),
            "rating": body["rating"],
            "draft_id": body.get("draft_id"),
            "proposed_by": officer,
            "proposed_at": _iso(when),
            "case_status_at_proposal": status_now,
            "case_version": version,
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
            },
        }

    def _event(
        self,
        case_id: str,
        actor: str,
        role: str,
        action: str,
        subject_type: str,
        subject_id: str,
        outcome: str,
        sequence: int | None,
        when: datetime.datetime | None = None,
    ) -> None:
        self.events[case_id].append(
            {
                "event_id": str(uuid.uuid4()),
                "sequence": sequence,
                "actor_id": actor,
                "actor_role": role,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "occurred_at": _iso(when or self.now()),
                "outcome": outcome,
            }
        )

    # --- checks -------------------------------------------------------------------

    @staticmethod
    def _require(who: dict[str, Any], action: str) -> None:
        if not set(who["roles"]) & ACTION_ROLES[action]:
            raise Refusal("forbidden", reason=action)

    def _check_version(self, case_id: str, expected: int) -> int:
        current = self.versions[case_id]
        if expected != current:
            raise Refusal("version_conflict", "expected_version", "stale_case_version", current)
        return current

    def _check_findings(self, case: dict[str, Any], finding_ids: list[str]) -> None:
        known = {f["finding_id"] for f in self._findings(case)}
        if any(fid not in known for fid in finding_ids):
            raise Refusal("invalid_domain_input", "finding_ids", "not_found")

    def _replay(self, key: str, digest: str) -> Response | None:
        stored = self.idempotency.get(key)
        if stored is None:
            return None
        if stored[0] != digest:
            raise Refusal("idempotency_conflict", "Idempotency-Key", "idempotency_conflict")
        status, extra, body = stored[1]
        return status, {**extra, "Idempotent-Replayed": "true"}, copy.deepcopy(body)

    # --- operations ---------------------------------------------------------------

    def handle(
        self,
        op_id: str,
        params: dict[str, str],
        headers: dict[str, str],
        body: Any,
        cases: list[dict[str, Any]],
        correlation: str,
    ) -> Response:
        with self.lock:
            self._seed(cases)
            try:
                who = self.principal(headers)
                case = next((c for c in cases if c["case_id"] == params.get("case_id")), None)
                if case is None:
                    raise Refusal("not_found")
                handler = getattr(self, "_" + op_id)
                result: Response = handler(who, case, params, headers, body)
                return result
            except Refusal as refusal:
                return self._error(refusal, correlation)

    def _getCaseReview(self, who, case, params, headers, body) -> Response:  # noqa: N802
        case_id = case["case_id"]
        open_decision = self._open_decision(case_id)
        data = {
            "case_id": case_id,
            "tenant_id": case["tenant_id"],
            "case_version": self.versions[case_id],
            "viewer": {
                "officer_id": who["officer_id"],
                "roles": list(who["roles"]),
                "allowed_actions": self._allowed(who, open_decision),
            },
            "open_decision": copy.deepcopy(open_decision),
            "audit_events": copy.deepcopy(self.events[case_id][-200:]),
        }
        return 200, {}, {"data": data}

    def _listFindings(self, who, case, params, headers, body) -> Response:  # noqa: N802
        return (
            200,
            {},
            {"data": self._findings(case), "meta": {"next_cursor": None, "has_more": False}},
        )

    def _proposeDecision(self, who, case, params, headers, body) -> Response:  # noqa: N802
        self._require(who, "decision.propose")
        if body["outcome"] == "refer_to_specialist":
            self._require(who, "specialist.refer")
        case_id = case["case_id"]
        key = f"propose:{case['tenant_id']}:{headers['idempotency-key']}"
        digest = _digest({"case_id": case_id, **body})
        replay = self._replay(key, digest)
        if replay is not None:
            return replay
        _, target, closes = OUTCOMES[body["outcome"]]
        if closes and not (body["evidence_ids"] or body["finding_ids"]):
            raise Refusal("invalid_domain_input", "finding_ids", "citation_required")
        self._check_findings(case, body["finding_ids"])
        if self._open_decision(case_id) is not None:
            raise Refusal("version_conflict", "case_id", "decision_pending")
        version = self._check_version(case_id, body["expected_version"])
        current = self.jube_status[case_id]
        if current != body["expected_case_status"]:
            raise Refusal("version_conflict", "expected_case_status", "case_state_changed")
        if current == "RESOLVED":
            raise Refusal("invalid_domain_input", "case_id", "case_closed")
        if OUTCOMES[body["outcome"]][0] == "transition" and current == target:
            raise Refusal("invalid_domain_input", "outcome", "no_change")
        record = self._new_decision(
            case_id=case_id,
            tenant=case["tenant_id"],
            body=body,
            officer=who["officer_id"],
            when=self.now(),
            version=version,
            status_now=current,
        )
        self.decisions[record["decision_id"]] = record
        self._event(
            case_id,
            who["officer_id"],
            who["roles"][0],
            "decision.proposed",
            "decision",
            record["decision_id"],
            "proposed",
            version,
        )
        self.versions[case_id] = version + 1
        response: Response = (201, {}, {"data": copy.deepcopy(record)})
        self.idempotency[key] = (digest, response)
        return response

    def _reviewDecision(self, who, case, params, headers, body) -> Response:  # noqa: N802
        record = self.decisions.get(params["decision_id"])
        if record is None or record["case_id"] != case["case_id"]:
            raise Refusal("not_found")
        action = body["action"]
        if action == "withdraw":
            self._require(who, "decision.propose")
            if record["proposed_by"] != who["officer_id"]:
                raise Refusal("forbidden", reason="not_proposer")
        else:
            self._require(who, "decision.approve")
            if record["proposed_by"] == who["officer_id"]:
                raise Refusal("forbidden", reason="self_approval")
        key = f"review:{case['tenant_id']}:{headers['idempotency-key']}"
        digest = _digest({"decision_id": record["decision_id"], **body})
        replay = self._replay(key, digest)
        if replay is not None:
            return replay
        if record["status"] != "proposed":
            raise Refusal("version_conflict", "decision_id", "not_pending")
        case_id = case["case_id"]
        version = self._check_version(case_id, body["expected_version"])
        now = self.now()
        if action == "approve":
            proposed_at = datetime.datetime.strptime(
                record["proposed_at"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime.UTC)
            if now - proposed_at > MAX_PROPOSAL_AGE:
                raise Refusal("version_conflict", "decision_id", "proposal_expired")
            if self.jube_status[case_id] != record["case_status_at_proposal"]:
                raise Refusal("version_conflict", "decision_id", "case_state_changed")
        reason = body.get("reason")
        if action == "reject" and not reason:
            raise Refusal("invalid_domain_input", "reason", "too_short")
        new_status = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
        record.update(
            status=new_status,
            reviewed_by=who["officer_id"],
            reviewed_at=_iso(now),
            review_reason=reason or None,
        )
        role = who["roles"][0]
        self._event(
            case_id,
            who["officer_id"],
            role,
            f"decision.{new_status}",
            "decision",
            record["decision_id"],
            new_status,
            version,
        )
        self.versions[case_id] = version + 1
        if action == "approve":
            # Fixture stand-in for the Jube adapter (TAX-034): nothing leaves this process.
            target = record["target_status"]
            changed = self.jube_status[case_id] != target
            self.jube_status[case_id] = target
            record["jube_sync"].update(
                state="synced",
                attempts=1,
                last_attempt_at=_iso(now),
                synced_at=_iso(now),
                changed=changed,
                status_after=target,
            )
            self._event(
                case_id,
                who["officer_id"],
                role,
                "decision.executed",
                "decision",
                record["decision_id"],
                "changed" if changed else "replayed",
                None,
            )
        response: Response = (200, {}, {"data": copy.deepcopy(record)})
        self.idempotency[key] = (digest, response)
        return response

    def _requestEvidence(self, who, case, params, headers, body) -> Response:  # noqa: N802
        self._require(who, "evidence_request.authorise")
        case_id = case["case_id"]
        key = f"evidence:{case['tenant_id']}:{headers['idempotency-key']}"
        digest = _digest({"case_id": case_id, **body})
        replay = self._replay(key, digest)
        if replay is not None:
            return replay
        self._check_findings(case, body["finding_ids"])
        version = self._check_version(case_id, body["expected_version"])
        today = self.now().date()
        due = datetime.date.fromisoformat(body["due_date"])
        if due <= today:
            raise Refusal("invalid_domain_input", "due_date", "due_date_past")
        if due > today + datetime.timedelta(days=MAX_DUE_DAYS):
            raise Refusal("invalid_domain_input", "due_date", "due_date_too_far")
        request = {
            "request_id": str(uuid.uuid4()),
            "case_id": case_id,
            "tenant_id": case["tenant_id"],
            "status": "recorded",
            "authorised_by": who["officer_id"],
            "finding_ids": list(body["finding_ids"]),
            "items": copy.deepcopy(body["items"]),
            "due_date": body["due_date"],
            "case_version": version,
            "service_links": [],
            "created_at": _iso(self.now()),
        }
        self.requests[request["request_id"]] = request
        self._event(
            case_id,
            who["officer_id"],
            who["roles"][0],
            "evidence_request.recorded",
            "evidence_request",
            request["request_id"],
            "recorded",
            version,
        )
        self.versions[case_id] = version + 1
        response: Response = (202, {}, {"data": copy.deepcopy(request)})
        self.idempotency[key] = (digest, response)
        return response

    # --- errors -------------------------------------------------------------------

    @staticmethod
    def _error(refusal: Refusal, correlation: str) -> Response:
        # Local import: fixture_api imports this module.
        import contract  # noqa: PLC0415

        error: dict[str, Any] = {
            "code": refusal.code,
            "message": DETAIL.get(refusal.reason or "", MESSAGES.get(refusal.code, "Refused.")),
            "retryable": False,
            "correlation_id": correlation,
            "service": "vati",
        }
        if refusal.field and refusal.reason:
            error["field_errors"] = [{"field": refusal.field, "code": refusal.reason}]
        elif refusal.reason:
            error["field_errors"] = [{"field": "principal", "code": refusal.reason}]
        if refusal.current_version is not None:
            error["current_version"] = refusal.current_version
        return int(contract.ERROR_STATUS[refusal.code]), {}, {"error": error}


STORE = ReviewStore()
