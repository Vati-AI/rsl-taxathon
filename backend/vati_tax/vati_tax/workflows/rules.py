"""Learned-workflow approval, versioning and rollback rules (TAX-065), Frappe-free.

TAX-064 (``ai/workflows/workflow_observation.py``) proposes workflows mined from
approved officer actions; it can never activate one. This module is the human
gate those proposals point to:

- A proposal is recorded as a *version* of a named workflow, keeping the
  proposal's digest and its held-out evaluation exactly as submitted.
- A supervisor who did not record it approves or rejects it with a reason and
  an explicit acknowledgement that the held-out comparison was reviewed.
- A supervisor activates an approved version. Every activation is an
  append-only history entry that names the version it replaced, which is the
  rollback reference. Rollback re-activates that previous approved version.
- Only the active version of a workflow may execute. Anything else (proposed,
  rejected, an approved version that is not active, unknown) is refused.

Approving a learned workflow records a human judgement about a sequence of
already-approved officer actions. It never makes those steps automatic: each
step still needs its own officer approval (TAX-046/TAX-047).
"""

from __future__ import annotations

import datetime
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from vati_tax.decisions.rules import OUTCOMES
from vati_tax.exports.rules import digest, iso, required_text, utc
from vati_tax.ids.canonical import InvalidMapping, MappingError, validate_idempotency_key

__all__ = ["digest", "iso", "utc"]

Status = Literal["proposed", "approved", "rejected"]
STATUSES: Final[tuple[Status, ...]] = ("proposed", "approved", "rejected")
ActivationKind = Literal["activate", "rollback"]

# Step names TAX-064 can emit: an authorised evidence request, or an approved
# TAX-046 outcome. Anything else is not a step a learned workflow may contain.
STEP_NAMES: Final[frozenset[str]] = frozenset({"request_evidence", *OUTCOMES})
MIN_STEPS: Final = 2
MAX_STEPS: Final = 6
PROPOSAL_ID: Final = re.compile(r"^wfp-[0-9a-f]{16}$")
OBSERVER_VERSION: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WORKFLOW_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SEMVER: Final = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")
REASON_MIN: Final = 20
REASON_MAX: Final = 2000


class WorkflowNotFound(MappingError):
    """Unknown workflow or version, or one outside the tenant (reported identically)."""

    code = "not_found"
    http_status = 404


class WorkflowConflict(MappingError):
    """The version is not in a state that allows this step, or is stale."""

    code = "invalid_domain_input"
    http_status = 422


class ExecutionRefused(MappingError):
    """A version that is not the active approved one was asked to execute."""

    code = "invalid_domain_input"
    http_status = 422


def version_tuple(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


_NS: Final = uuid.UUID("9b3c6a1e-2f47-4c65-8a06-5e0d7f3b1065")


def workflow_uuid(tenant_id: str, workflow: str) -> str:
    """Ledger subject for workflow-level events (activation, rollback)."""
    return str(uuid.uuid5(_NS, f"{tenant_id}|{workflow}"))


def version_uuid(tenant_id: str, workflow: str, version: str) -> str:
    """Ledger subject for one version (proposal, approval, execution checks)."""
    return str(uuid.uuid5(_NS, f"{tenant_id}|{workflow}|{version}"))


def version_key(tenant_id: str, workflow: str, version: str) -> str:
    return digest({"tenant": tenant_id, "workflow": workflow, "version": version})


def activation_key(tenant_id: str, workflow: str, seq: int) -> str:
    return digest({"tenant": tenant_id, "workflow": workflow, "activation_seq": seq})


# --- proposal intake ----------------------------------------------------------------


@dataclass(frozen=True)
class ProposalIntake:
    workflow: str
    version: str
    steps: tuple[str, ...]
    proposal_id: str
    observer_version: str
    proposal_digest: str
    holdout: dict[str, Any]
    idempotency_key: str

    @classmethod
    def build(cls, body: Mapping[str, Any], idempotency_key: str) -> ProposalIntake:
        """Validate a TAX-064 ``workflow_proposal`` plus the name and version to file it as.

        The proposal must still be what the observer produced: ``status:
        proposed``, ``auto_activate: false``, no approver, and only known steps.
        """
        workflow = body.get("workflow")
        if not isinstance(workflow, str) or not WORKFLOW_NAME.match(workflow):
            raise InvalidMapping("workflow must be a lower-case name.", "workflow", "invalid")
        version = body.get("version")
        if not isinstance(version, str) or not SEMVER.match(version):
            raise InvalidMapping("version must be MAJOR.MINOR.PATCH.", "version", "invalid")
        proposal = body.get("proposal")
        if not isinstance(proposal, Mapping):
            raise InvalidMapping("proposal is required.", "proposal", "required")
        if proposal.get("kind") != "workflow_proposal":
            raise InvalidMapping("Not a workflow proposal.", "proposal", "invalid_kind")
        if (
            proposal.get("status") != "proposed"
            or proposal.get("auto_activate") is not False
            or proposal.get("approved_by") is not None
        ):
            raise InvalidMapping(
                "A proposal must arrive unapproved and inactive.", "proposal", "not_a_proposal"
            )
        proposal_id = proposal.get("proposal_id")
        if not isinstance(proposal_id, str) or not PROPOSAL_ID.match(proposal_id):
            raise InvalidMapping("Invalid proposal_id.", "proposal", "invalid_proposal_id")
        observer = proposal.get("observer_version")
        if not isinstance(observer, str) or not OBSERVER_VERSION.match(observer):
            raise InvalidMapping("Invalid observer_version.", "proposal", "invalid_observer")
        steps = proposal.get("steps")
        if (
            not isinstance(steps, Sequence)
            or isinstance(steps, str)
            or not MIN_STEPS <= len(steps) <= MAX_STEPS
        ):
            raise InvalidMapping(
                f"A workflow has {MIN_STEPS} to {MAX_STEPS} steps.", "proposal", "invalid_steps"
            )
        if any(step not in STEP_NAMES for step in steps):
            raise InvalidMapping("Unknown workflow step.", "proposal", "unknown_step")
        holdout = proposal.get("holdout_evaluation")
        if not isinstance(holdout, Mapping) or holdout.get("status") not in (
            "evaluated",
            "insufficient_holdout",
        ):
            raise InvalidMapping(
                "The held-out evaluation is required.", "proposal", "missing_holdout"
            )
        return cls(
            workflow=workflow,
            version=version,
            steps=tuple(str(s) for s in steps),
            proposal_id=proposal_id,
            observer_version=observer,
            proposal_digest=digest(dict(proposal)),
            holdout=dict(holdout),
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def request_digest(self) -> str:
        return digest(
            {
                "workflow": self.workflow,
                "version": self.version,
                "proposal_digest": self.proposal_digest,
            }
        )


# --- records ------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowVersion:
    tenant_id: str
    workflow: str
    version: str
    steps: tuple[str, ...]
    proposal_id: str
    observer_version: str
    proposal_digest: str
    holdout: dict[str, Any]
    request_digest: str
    proposed_by: str
    proposed_at: datetime.datetime
    status: Status = "proposed"
    record_version: int = 1
    decided_by: str | None = None
    decided_at: datetime.datetime | None = None
    decision_reason: str | None = None

    @property
    def key(self) -> str:
        return version_key(self.tenant_id, self.workflow, self.version)


@dataclass(frozen=True)
class Activation:
    tenant_id: str
    workflow: str
    seq: int
    kind: ActivationKind
    version: str
    replaced_version: str | None  # the version in force before this entry
    rollback_to: str | None  # the approved version a rollback of this entry restores
    activated_by: str
    activated_at: datetime.datetime
    reason: str


def active(history: Sequence[Activation]) -> Activation | None:
    """The latest activation is the one in force."""
    return max(history, key=lambda a: a.seq) if history else None


def check_decidable(
    record: WorkflowVersion,
    expected_version: object,
    holdout_reviewed: object,
    approve: bool,
) -> None:
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise InvalidMapping("expected_version must be an integer.", "expected_version", "required")
    if record.status != "proposed":
        raise WorkflowConflict("The version has already been decided.", "version", "not_pending")
    if expected_version != record.record_version:
        raise WorkflowConflict(
            "The version changed since you loaded it.", "expected_version", "stale_version"
        )
    if approve and holdout_reviewed is not True:
        raise InvalidMapping(
            "Confirm that you reviewed the held-out comparison.",
            "holdout_reviewed",
            "required",
        )


def check_activatable(
    record: WorkflowVersion, history: Sequence[Activation], expected_seq: object
) -> Activation | None:
    """An approved version that is not already active; ``expected_seq`` guards races."""
    if record.status != "approved":
        raise WorkflowConflict(
            "Only an approved version can be activated.", "version", "not_approved"
        )
    current = active(history)
    if isinstance(expected_seq, bool) or not isinstance(expected_seq, int):
        raise InvalidMapping("expected_seq must be an integer.", "expected_seq", "required")
    if expected_seq != (current.seq if current else 0):
        raise WorkflowConflict(
            "The workflow's activation changed since you loaded it.",
            "expected_seq",
            "stale_activation",
        )
    if current is not None and current.version == record.version:
        raise WorkflowConflict("This version is already active.", "version", "already_active")
    return current


def rollback_target(
    history: Sequence[Activation], versions: Mapping[str, WorkflowVersion]
) -> tuple[Activation, WorkflowVersion, str | None]:
    """The active entry, the approved version to restore and that version's own
    rollback reference, so repeated rollbacks walk back through history."""
    current = active(history)
    if current is None or current.rollback_to is None:
        raise WorkflowConflict(
            "There is no earlier approved version to roll back to.", "workflow", "no_previous"
        )
    target = versions.get(current.rollback_to)
    if target is None or target.status != "approved":
        raise WorkflowConflict(
            "The previous version is no longer approved.", "workflow", "previous_not_approved"
        )
    earlier = [a for a in history if a.version == target.version and a.seq < current.seq]
    onward = max(earlier, key=lambda a: a.seq).rollback_to if earlier else None
    return current, target, onward


def check_executable(
    history: Sequence[Activation], versions: Mapping[str, WorkflowVersion], version: str
) -> WorkflowVersion:
    """Refuse any version that is not the active, approved one."""
    record = versions.get(version)
    current = active(history)
    if record is None:
        raise ExecutionRefused("Unknown workflow version.", "version", "unknown_version")
    if record.status != "approved":
        raise ExecutionRefused(
            "This workflow version is not approved.", "version", "unapproved_version"
        )
    if current is None or current.version != version:
        raise ExecutionRefused("This workflow version is not active.", "version", "not_active")
    return record


def reason_text(value: object) -> str:
    return required_text({"reason": value}, "reason", REASON_MIN, REASON_MAX)


# --- views --------------------------------------------------------------------------


def version_view(record: WorkflowVersion, current: Activation | None) -> dict[str, Any]:
    return {
        "tenant_id": record.tenant_id,
        "workflow": record.workflow,
        "version": record.version,
        "steps": list(record.steps),
        "status": record.status,
        "active": current is not None and current.version == record.version,
        "record_version": record.record_version,
        "proposal_id": record.proposal_id,
        "observer_version": record.observer_version,
        "proposal_digest": record.proposal_digest,
        "holdout_evaluation": record.holdout,
        "proposed_by": record.proposed_by,
        "proposed_at": iso(record.proposed_at),
        "decided_by": record.decided_by,
        "decided_at": iso(record.decided_at),
        "decision_reason": record.decision_reason,
        "auto_activate": False,
    }


def activation_view(entry: Activation) -> dict[str, Any]:
    return {
        "workflow": entry.workflow,
        "seq": entry.seq,
        "kind": entry.kind,
        "version": entry.version,
        "replaced_version": entry.replaced_version,
        "rollback_to": entry.rollback_to,
        "activated_by": entry.activated_by,
        "activated_at": iso(entry.activated_at),
        "reason": entry.reason,
    }
