"""Lawful payment-channel evidence contracts (TAX-079).

A ``payment_channel_submission.v1`` envelope describes one supplied file (a bank statement,
mobile-money statement, foreign-payment advice or digital-wallet export) for one tenant, case
and taxpayer. It carries:

- ``permission``: the lawful-access basis (``taxpayer_supplied``, ``statutory_request`` or
  ``taxpayer_consent``) with its approval reference, requester, approver, scope (case,
  taxpayer, channels, account references, period) and validity window. A file without a valid
  permission is refused; nothing is stored.
- ``provenance``: the channel and synthetic provider, the TAX-032 evidence ID and SHA-256 of
  the supplied original, the retrieval method and the time it was received. The adapter ID and
  version are added by the store from the adapter that parsed the file.

``ChannelRecordStore.ingest`` checks the actor against the TAX-029 policy actions, the file
against its declared SHA-256, the permission against the file's contents, and then parses the
file with the channel adapter (``channel_adapters``). Each line becomes one immutable
``payment_channel_record.v1``. A correction is a new version of the record set that names the
digest of the version it supersedes; earlier versions stay readable as history. Reads need
``evidence.read`` inside the caller's tenant and case.

``ftm_bundle`` maps the current records to a TAX-036 ``tax_entity_bundle.v1`` (each line one
FtM ``Payment``, with an ``EvidenceRef`` to the line) and returns ``channel_links`` that keep
each Payment's permission, provenance, gaps and contradictions next to it.
``scheme_graph_input`` wraps that bundle as a TAX-074 graph input.

Nothing here assesses anything. Payment method and channel are descriptive; no record has a
risk, score or signal field and every record has ``interpretation: None``. Contradictions
between supplied files are listed unresolved for officer review (``finding.review``).

Standard library only (plus the TAX-036/048/073/074 pure modules). No network, no clock reads:
the ingest time is passed in, so a replay is byte-identical.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import channel_adapters as ca
import scheme_graph as sg
import tax_mapping as tm

SUBMISSION_VERSION = "payment_channel_submission.v1"
RECORD_VERSION = "payment_channel_record.v1"

# Lawful-access bases and the retrieval methods each one allows.
BASIS_METHODS: dict[str, frozenset[str]] = {
    "taxpayer_supplied": frozenset({"taxpayer_upload", "officer_upload"}),
    "statutory_request": frozenset({"statutory_response"}),
    "taxpayer_consent": frozenset({"consented_provider_file"}),
}
RETRIEVAL_METHODS = frozenset().union(*BASIS_METHODS.values())
LEGAL_ROUTE_STATUSES = frozenset({"unverified", "verified"})
MAX_PERMISSION_DAYS = 366  # prototype bound: no open-ended permission

# TAX-029 action roles (backend/vati_tax/vati_tax/identity/policy.py; a test keeps these equal).
REGISTER_ROLES = frozenset({"taxpayer", "auditor", "specialist", "supervisor", "service"})
READ_ROLES = frozenset({"auditor", "specialist", "supervisor", "service"})
AUTHORISE_ROLES = frozenset({"auditor", "supervisor"})
STAFF_ROLES = frozenset({"auditor", "specialist", "supervisor"})
ROLES = REGISTER_ROLES

DEFAULT_LIMIT, MAX_LIMIT = 25, 100
REVIEW_ROUTE = {"action": "finding.review", "decided_by": "officer", "interpretation": None}
HANDLING = {
    "read_action": "evidence.read",
    "export": "TAX-062 export.request and export.approve only",
    "retention": "TAX-062 evidence class; disposal manual_review_only",
}
NOTICE = (
    "Records describe payment activity from supplied files. The channel and payment method "
    "are context, not an indication of wrongdoing. Contradictions and gaps are listed for "
    "officer review; nothing here is an assessment."
)

_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ENVELOPE_KEYS = {
    "version",
    "synthetic",
    "tenant_id",
    "case_id",
    "taxpayer_id",
    "account_holder",
    "record_set_id",
    "record_set_version",
    "supersedes",
    "correction_reason",
    "permission",
    "provenance",
}
_PERMISSION_KEYS = {
    "basis",
    "reference",
    "requested_by",
    "approved_by",
    "legal_authority",
    "scope",
    "granted_at",
    "expires_at",
}
_SCOPE_KEYS = {"case_id", "taxpayer_id", "channels", "account_refs", "period_start", "period_end"}
_PERSON_KEYS = {"user_id", "role", "taxpayer_id"}
_PROVENANCE_KEYS = {
    "channel",
    "document_format",
    "provider",
    "evidence",
    "retrieval_method",
    "received_at",
}
_PROVIDER_KEYS = {"provider_id", "name", "synthetic"}
_EVIDENCE_KEYS = {"evidence_id", "document_sha256", "file_name", "media_type", "size"}
_HOLDER_KEYS = {"record_id", "name", "kind"}


class ChannelContractError(ValueError):
    """A submission or read is refused. ``code`` is an ADR-003 ``ErrorCode``:

    - ``invalid_domain_input``: malformed envelope or file;
    - ``forbidden``: the actor's role, or the lawful-access permission, does not allow it;
    - ``tenant_scope_denied``: the actor is not a member of the envelope's tenant;
    - ``not_found``: no records for that tenant and case in the caller's scope;
    - ``idempotency_conflict``: a recorded version (or evidence ID) with different content;
    - ``version_conflict``: a correction that does not follow the current version.

    ``field`` and ``reason`` say what and why. Messages never contain document content.
    """

    def __init__(self, code: str, message: str, *, field: str, reason: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.reason = reason

    def error_response(self, correlation_id: str) -> dict[str, Any]:
        """The contract ``ErrorResponse`` (integration/api/openapi.json)."""
        return {
            "error": {
                "code": self.code,
                "message": str(self)[:500],
                "retryable": False,
                "correlation_id": correlation_id,
                "service": "vati",
                "field_errors": [{"field": self.field[:255], "code": self.reason[:64]}],
            }
        }


@dataclass(frozen=True)
class Actor:
    """The authenticated principal, resolved by the caller's boundary from an enabled TAX-029
    membership (tenant from the session, never from the envelope)."""

    user_id: str
    role: str
    tenant_id: str
    taxpayer_id: str | None = None


@dataclass(frozen=True)
class ChannelScope:
    """A read scope: tenant from the session, case from a checked ``evidence.read``."""

    tenant_id: str
    case_id: str
    role: str


# ---------------------------------------------------------------- validation helpers


def _invalid(message: str, field: str, reason: str) -> ChannelContractError:
    return ChannelContractError("invalid_domain_input", message, field=field, reason=reason)


def _refused(message: str, field: str, reason: str) -> ChannelContractError:
    return ChannelContractError("forbidden", message, field=field, reason=reason)


def _object(value: Any, allowed: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{where} must be an object", where, "invalid")
    for key in sorted(set(value) - allowed):
        words = set(re.findall(r"[a-z]+", str(key).lower()))
        labelling = words & (sg.FORBIDDEN_TERMS | sg.LABEL_FIELDS) or sg.labelling_terms(str(key))
        reason = "risk_label_not_allowed" if labelling else "unexpected_field"
        raise _invalid(f"{where}.{key} is not accepted", f"{where}.{key}", reason)
    return value


def _text(record: Mapping[str, Any], key: str, where: str, *, optional: bool = False) -> Any:
    value = record.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise _invalid(f"{where}.{key} must be a non-empty string", f"{where}.{key}", "invalid")
    if sg.labelling_terms(value):
        raise _invalid(
            f"{where}.{key} carries labelling vocabulary; describe the record and send any "
            "assessment to officer review",
            f"{where}.{key}",
            "risk_label_not_allowed",
        )
    return value


def _ref(record: Mapping[str, Any], key: str, where: str) -> str:
    value = _text(record, key, where)
    if not tm.RECORD_ID.fullmatch(value):
        raise _invalid(f"{where}.{key} is not a valid reference", f"{where}.{key}", "invalid")
    return str(value)


def _uuid4(value: Any, where: str) -> str:
    try:
        if not isinstance(value, str) or uuid.UUID(value).version != 4 or value != value.lower():
            raise ValueError
    except ValueError:
        raise _invalid(f"{where} must be a lower-case UUIDv4", where, "invalid")
    return value


def _instant(value: Any, where: str) -> datetime.datetime:
    try:
        if not isinstance(value, str) or not _INSTANT.fullmatch(value):
            raise ValueError
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid(f"{where} must be UTC YYYY-MM-DDTHH:MM:SSZ", where, "invalid_timestamp")


def _date(value: Any, where: str) -> str:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        datetime.date.fromisoformat(value)
    except ValueError:
        raise _invalid(f"{where} must be YYYY-MM-DD", where, "invalid_date")
    return value


def _ref_list(value: Any, where: str, allowed: frozenset[str] | None = None) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 50:
        raise _invalid(f"{where} must list 1 to 50 values", where, "invalid")
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not tm.RECORD_ID.fullmatch(item):
            raise _invalid(f"{where}[{i}] is invalid", f"{where}[{i}]", "invalid")
        if allowed is not None and item not in allowed:
            raise _invalid(
                f"{where}[{i}] must be one of {sorted(allowed)}", f"{where}[{i}]", "invalid"
            )
        out.append(item)
    if len(set(out)) != len(out):
        raise _invalid(f"{where} repeats a value", where, "duplicate")
    return sorted(out)


def canonical_digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(str(p) for p in parts)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _person(value: Any, where: str) -> dict[str, Any]:
    person = _object(value, _PERSON_KEYS, where)
    role = person.get("role")
    if role not in ROLES:
        raise _invalid(f"{where}.role must be one of {sorted(ROLES)}", f"{where}.role", "invalid")
    taxpayer_id = person.get("taxpayer_id")
    if role == "taxpayer":
        taxpayer_id = _uuid4(taxpayer_id, f"{where}.taxpayer_id")
    elif taxpayer_id is not None:
        raise _invalid(
            f"{where}.taxpayer_id is only for taxpayer members", f"{where}.taxpayer_id", "invalid"
        )
    return {"user_id": _ref(person, "user_id", where), "role": role, "taxpayer_id": taxpayer_id}


# ---------------------------------------------------------------- envelope


@dataclass(frozen=True)
class _Envelope:
    data: dict[str, Any]
    adapter: ca.PaymentChannelAdapter
    permission: dict[str, Any]
    provenance: dict[str, Any]
    granted: datetime.datetime
    expires: datetime.datetime
    received: datetime.datetime


def _permission(value: Any, envelope: Mapping[str, Any]) -> tuple[dict[str, Any], Any, Any]:
    if value is None:
        raise _refused(
            "A lawful-access permission is required for every payment-channel record",
            "permission",
            "lawful_basis_missing",
        )
    p = _object(value, _PERMISSION_KEYS, "permission")
    basis = p.get("basis")
    if basis not in BASIS_METHODS:
        raise _refused(
            f"permission.basis must be one of {sorted(BASIS_METHODS)}",
            "permission.basis",
            "lawful_basis_missing",
        )
    requested_by = _person(p.get("requested_by"), "permission.requested_by")
    approved_by = (
        None
        if p.get("approved_by") is None
        else _person(p["approved_by"], "permission.approved_by")
    )
    authority = p.get("legal_authority")
    legal: dict[str, Any] | None = None
    if authority is not None:
        a = _object(authority, {"reference", "status"}, "permission.legal_authority")
        if a.get("status") not in LEGAL_ROUTE_STATUSES:
            raise _invalid(
                "permission.legal_authority.status must be unverified or verified",
                "permission.legal_authority.status",
                "invalid",
            )
        legal = {
            "reference": _text(a, "reference", "permission.legal_authority"),
            "status": a["status"],
        }
    scope = _object(p.get("scope"), _SCOPE_KEYS, "permission.scope")
    start = _date(scope.get("period_start"), "permission.scope.period_start")
    end = _date(scope.get("period_end"), "permission.scope.period_end")
    if end < start:
        raise _invalid(
            "permission.scope.period_end is before period_start",
            "permission.scope.period_end",
            "invalid_date",
        )
    granted = _instant(p.get("granted_at"), "permission.granted_at")
    expires = _instant(p.get("expires_at"), "permission.expires_at")
    if expires <= granted:
        raise _invalid(
            "permission.expires_at must follow granted_at", "permission.expires_at", "invalid"
        )
    if expires - granted > datetime.timedelta(days=MAX_PERMISSION_DAYS):
        raise _invalid(
            f"A permission is valid for at most {MAX_PERMISSION_DAYS} days",
            "permission.expires_at",
            "permission_too_long",
        )
    normalised = {
        "basis": basis,
        "reference": _ref(p, "reference", "permission"),
        "requested_by": requested_by,
        "approved_by": approved_by,
        "legal_authority": legal,
        "scope": {
            "case_id": _uuid4(scope.get("case_id"), "permission.scope.case_id"),
            "taxpayer_id": _uuid4(scope.get("taxpayer_id"), "permission.scope.taxpayer_id"),
            "channels": _ref_list(
                scope.get("channels"), "permission.scope.channels", frozenset(ca.ADAPTERS)
            ),
            "account_refs": _ref_list(scope.get("account_refs"), "permission.scope.account_refs"),
            "period_start": start,
            "period_end": end,
        },
        "granted_at": p["granted_at"],
        "expires_at": p["expires_at"],
    }
    if normalised["scope"]["case_id"] != envelope.get("case_id") or normalised["scope"][
        "taxpayer_id"
    ] != envelope.get("taxpayer_id"):
        raise _refused(
            "The permission was granted for another case or taxpayer",
            "permission.scope",
            "permission_subject_mismatch",
        )
    return normalised, granted, expires


def _check_basis(permission: Mapping[str, Any], method: str, taxpayer_id: str) -> None:
    """Who may stand behind each basis, in TAX-029 terms."""
    basis = permission["basis"]
    requester, approver = permission["requested_by"], permission["approved_by"]
    if method not in BASIS_METHODS[basis]:
        raise _refused(
            f"Retrieval method {method} is not allowed under basis {basis}",
            "provenance.retrieval_method",
            "basis_retrieval_mismatch",
        )
    if basis == "taxpayer_supplied":
        if approver is not None:
            raise _invalid(
                "A taxpayer-supplied record has no approver", "permission.approved_by", "invalid"
            )
        expected = {"taxpayer"} if method == "taxpayer_upload" else STAFF_ROLES
        if requester["role"] not in expected or (
            requester["role"] == "taxpayer" and requester["taxpayer_id"] != taxpayer_id
        ):
            raise _refused(
                "The supplier does not match the retrieval method or taxpayer",
                "permission.requested_by",
                "requester_not_permitted",
            )
        return
    if basis == "statutory_request":
        if requester["role"] not in STAFF_ROLES:
            raise _refused(
                "A statutory request is made by an officer",
                "permission.requested_by",
                "requester_not_permitted",
            )
        if approver is None:
            raise _refused(
                "A statutory request needs an approver holding evidence_request.authorise",
                "permission.approved_by",
                "approval_missing",
            )
        if approver["role"] not in AUTHORISE_ROLES:
            raise _refused(
                "The approver does not hold evidence_request.authorise",
                "permission.approved_by",
                "approver_not_permitted",
            )
        if permission["legal_authority"] is None:
            raise _refused(
                "A statutory request must record the legal authority it relied on",
                "permission.legal_authority",
                "legal_authority_missing",
            )
        return
    # taxpayer_consent: an authorised officer asks; the taxpayer itself consents.
    if requester["role"] not in AUTHORISE_ROLES:
        raise _refused(
            "A consent request is made by a role holding evidence_request.authorise",
            "permission.requested_by",
            "requester_not_permitted",
        )
    if approver is None:
        raise _refused(
            "Consent must be given by the taxpayer", "permission.approved_by", "approval_missing"
        )
    if approver["role"] != "taxpayer" or approver["taxpayer_id"] != taxpayer_id:
        raise _refused(
            "Consent must be given by the taxpayer the records describe",
            "permission.approved_by",
            "approver_not_permitted",
        )


def parse_envelope(data: Any) -> _Envelope:
    """Validate a ``payment_channel_submission.v1`` (structure, permission and provenance)."""
    env = _object(data, _ENVELOPE_KEYS, "envelope")
    if env.get("version") != SUBMISSION_VERSION:
        raise _invalid(f"version must be {SUBMISSION_VERSION}", "version", "unsupported_version")
    if env.get("synthetic") is not True:
        raise _invalid("Only synthetic submissions are accepted", "synthetic", "not_synthetic")
    _uuid4(env.get("tenant_id"), "tenant_id")
    _uuid4(env.get("case_id"), "case_id")
    taxpayer_id = _uuid4(env.get("taxpayer_id"), "taxpayer_id")
    holder = _object(env.get("account_holder"), _HOLDER_KEYS, "account_holder")
    if holder.get("kind") not in tm.PARTY_SCHEMA:
        raise _invalid(
            f"account_holder.kind must be one of {sorted(tm.PARTY_SCHEMA)}",
            "account_holder.kind",
            "invalid",
        )
    _ref(holder, "record_id", "account_holder")
    _text(holder, "name", "account_holder")
    _ref(env, "record_set_id", "envelope")
    version = env.get("record_set_version")
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 1000:
        raise _invalid("record_set_version must be 1..1000", "record_set_version", "invalid")
    supersedes = env.get("supersedes")
    if version == 1:
        if supersedes is not None or env.get("correction_reason") is not None:
            raise _invalid("A first version supersedes nothing", "supersedes", "invalid")
    else:
        s = _object(supersedes, {"record_set_version", "record_set_sha256"}, "supersedes")
        if s.get("record_set_version") != version - 1 or not (
            isinstance(s.get("record_set_sha256"), str)
            and tm.SHA256.fullmatch(s["record_set_sha256"])
        ):
            raise _invalid(
                "supersedes must name the previous version and its digest", "supersedes", "invalid"
            )
        _text(env, "correction_reason", "envelope")

    prov = _object(env.get("provenance"), _PROVENANCE_KEYS, "provenance")
    channel = prov.get("channel")
    adapter = ca.ADAPTERS.get(channel) if isinstance(channel, str) else None
    if adapter is None:
        raise _invalid(
            f"provenance.channel must be one of {sorted(ca.ADAPTERS)}",
            "provenance.channel",
            "unsupported_channel",
        )
    if prov.get("document_format") != adapter.document_format:
        raise _invalid(
            f"{channel} files use {adapter.document_format}",
            "provenance.document_format",
            "unsupported_format",
        )
    provider = _object(prov.get("provider"), _PROVIDER_KEYS, "provenance.provider")
    if provider.get("synthetic") is not True:
        raise _invalid(
            "Only synthetic providers are accepted; live provider data needs a reviewed change",
            "provenance.provider.synthetic",
            "not_synthetic",
        )
    ev = _object(prov.get("evidence"), _EVIDENCE_KEYS, "provenance.evidence")
    sha = ev.get("document_sha256")
    if not isinstance(sha, str) or not tm.SHA256.fullmatch(sha):
        raise _invalid(
            "provenance.evidence.document_sha256 must be lower-case hex SHA-256",
            "provenance.evidence.document_sha256",
            "invalid",
        )
    if ev.get("media_type") != adapter.media_type:
        raise _invalid(
            f"{channel} files are {adapter.media_type}",
            "provenance.evidence.media_type",
            "unsupported_media_type",
        )
    size = ev.get("size")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 1 <= size <= ca.MAX_DOCUMENT_BYTES
    ):
        raise _invalid("provenance.evidence.size is invalid", "provenance.evidence.size", "invalid")
    method = prov.get("retrieval_method")
    if method not in RETRIEVAL_METHODS:
        raise _invalid(
            f"provenance.retrieval_method must be one of {sorted(RETRIEVAL_METHODS)}",
            "provenance.retrieval_method",
            "invalid",
        )
    received = _instant(prov.get("received_at"), "provenance.received_at")
    permission, granted, expires = _permission(env.get("permission"), env)
    _check_basis(permission, str(method), taxpayer_id)
    provenance = {
        "channel": channel,
        "document_format": adapter.document_format,
        "provider": {
            "provider_id": _ref(provider, "provider_id", "provenance.provider"),
            "name": _text(provider, "name", "provenance.provider"),
            "synthetic": True,
        },
        "evidence": {
            "evidence_id": _uuid4(ev.get("evidence_id"), "provenance.evidence.evidence_id"),
            "document_sha256": sha,
            "file_name": _text(ev, "file_name", "provenance.evidence"),
            "media_type": adapter.media_type,
            "size": size,
        },
        "retrieval_method": method,
        "received_at": prov["received_at"],
        "adapter": {
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "document_format": adapter.document_format,
        },
    }
    return _Envelope(
        data=copy.deepcopy(dict(env)),
        adapter=adapter,
        permission=permission,
        provenance=provenance,
        granted=granted,
        expires=expires,
        received=received,
    )


# ---------------------------------------------------------------- store


class ChannelRecordStore:
    """In-memory (tenant, case) store of immutable channel records. A future Vati API route
    persists the same records next to the TAX-032 evidence originals."""

    def __init__(self) -> None:
        # (tenant, case) -> record_set_id -> [version entries]
        self._sets: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
        self._evidence: dict[tuple[str, str], dict[str, str]] = {}  # evidence_id -> sha256

    # -- writes

    def ingest(
        self, envelope: Mapping[str, Any], document: bytes, actor: Actor, *, as_of: str
    ) -> dict[str, Any]:
        """Validate and record one supplied file. Same envelope and file again: ``replayed``.
        Refusals raise ``ChannelContractError`` and record nothing."""
        env = parse_envelope(envelope)
        data = env.data
        tenant, case, taxpayer = data["tenant_id"], data["case_id"], data["taxpayer_id"]
        recorded = _instant(as_of, "as_of")
        self._authorise_actor(actor, tenant, taxpayer, env)

        evidence = env.provenance["evidence"]
        if not isinstance(document, bytes):
            raise _invalid("The supplied document must be bytes", "document", "invalid")
        if hashlib.sha256(document).hexdigest() != evidence["document_sha256"]:
            raise _invalid(
                "The supplied file does not match its recorded SHA-256",
                "provenance.evidence.document_sha256",
                "provenance_hash_mismatch",
            )
        if len(document) != evidence["size"]:
            raise _invalid(
                "The supplied file size differs from the evidence record",
                "provenance.evidence.size",
                "provenance_size_mismatch",
            )
        self._check_window(env, recorded)

        try:
            transactions = env.adapter.parse(document)
        except ca.AdapterError as exc:
            raise ChannelContractError(
                exc.code, str(exc), field=exc.field, reason=exc.reason
            ) from exc
        scope = env.permission["scope"]
        if env.provenance["channel"] not in scope["channels"]:
            raise _refused(
                "The permission does not cover this channel",
                "permission.scope.channels",
                "out_of_scope_channel",
            )
        for tx in transactions:
            if tx.account_ref not in scope["account_refs"]:
                raise _refused(
                    f"line {tx.line} is for an account the permission does not cover",
                    f"line {tx.line}",
                    "out_of_scope_account",
                )
            if not scope["period_start"] <= tx.date <= scope["period_end"]:
                raise _refused(
                    f"line {tx.line} is outside the permitted period",
                    f"line {tx.line}",
                    "out_of_scope_period",
                )

        key = (tenant, case)
        set_id, version = data["record_set_id"], data["record_set_version"]
        digest = canonical_digest(
            {"envelope": data, "document_sha256": evidence["document_sha256"]}
        )
        versions = self._sets.get(key, {}).get(set_id, [])
        if version <= len(versions):
            if versions[version - 1]["record_set_sha256"] == digest:
                return self._result("replayed", key, set_id, versions[version - 1])
            raise ChannelContractError(
                "idempotency_conflict",
                "This record-set version is already recorded with different content",
                field="record_set_version",
                reason="changed_record",
            )
        if version != len(versions) + 1:
            raise ChannelContractError(
                "version_conflict",
                "A correction must follow the current version",
                field="record_set_version",
                reason="stale_or_missing_version",
            )
        if versions:
            current = versions[-1]
            if data["supersedes"]["record_set_sha256"] != current["record_set_sha256"]:
                raise ChannelContractError(
                    "version_conflict",
                    "supersedes does not name the current version",
                    field="supersedes.record_set_sha256",
                    reason="stale_or_missing_version",
                )
            for name in ("taxpayer_id", "account_holder"):
                if data[name] != current["envelope"][name]:
                    raise _invalid("A correction cannot change its subject", name, "invalid")
            if env.provenance["channel"] != current["provenance"]["channel"]:
                raise _invalid(
                    "A correction cannot change its channel", "provenance.channel", "invalid"
                )
        self._check_case_consistency(key, set_id, data, evidence)

        records = [self._record(env, tx, digest, actor, as_of) for tx in transactions]
        entry = {
            "record_set_id": set_id,
            "record_set_version": version,
            "record_set_sha256": digest,
            "envelope": data,
            "provenance": env.provenance,
            "recorded_at": as_of,
            "records": records,
        }
        self._sets.setdefault(key, {}).setdefault(set_id, []).append(entry)
        self._evidence.setdefault(key, {})[evidence["evidence_id"]] = evidence["document_sha256"]
        return self._result("applied", key, set_id, entry)

    @staticmethod
    def _authorise_actor(actor: Actor, tenant: str, taxpayer: str, env: _Envelope) -> None:
        if actor.tenant_id != tenant:
            raise ChannelContractError(
                "tenant_scope_denied",
                "The actor is not a member of this tenant",
                field="tenant_id",
                reason="tenant_scope_denied",
            )
        if actor.role not in REGISTER_ROLES:
            raise _refused("This role cannot register evidence", "actor", "role_not_permitted")
        method = env.provenance["retrieval_method"]
        if actor.role == "taxpayer":
            # evidence.register for a taxpayer covers its own taxpayer only (TAX-029).
            if actor.taxpayer_id != taxpayer or method != "taxpayer_upload":
                raise _refused(
                    "A taxpayer registers only its own uploads", "actor", "role_not_permitted"
                )
            if env.permission["requested_by"]["user_id"] != actor.user_id:
                raise _refused(
                    "The uploader must be the supplier named in the permission",
                    "permission.requested_by",
                    "requester_not_permitted",
                )
        elif method == "taxpayer_upload":
            raise _refused(
                "A taxpayer upload is registered by the taxpayer", "actor", "role_not_permitted"
            )
        elif method == "officer_upload" and actor.role not in STAFF_ROLES:
            raise _refused("An officer upload is made by an officer", "actor", "role_not_permitted")

    @staticmethod
    def _check_window(env: _Envelope, recorded: datetime.datetime) -> None:
        if not env.granted <= env.received <= env.expires:
            raise _refused(
                "The file was received outside the permission's validity window",
                "provenance.received_at",
                "received_outside_permission",
            )
        if recorded < env.received:
            raise _invalid(
                "as_of is earlier than the time the file was received", "as_of", "invalid"
            )
        if recorded > env.expires:
            raise _refused(
                "The permission has expired; a new permission is required",
                "permission.expires_at",
                "permission_expired",
            )

    def _check_case_consistency(
        self,
        key: tuple[str, str],
        set_id: str,
        data: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> None:
        known = self._evidence.get(key, {}).get(evidence["evidence_id"])
        if known is not None and known != evidence["document_sha256"]:
            raise ChannelContractError(
                "idempotency_conflict",
                "This evidence ID is already recorded with another SHA-256",
                field="provenance.evidence.evidence_id",
                reason="changed_record",
            )
        holder = data["account_holder"]
        for other_id, versions in self._sets.get(key, {}).items():
            other = versions[-1]["envelope"]
            if other_id == set_id:
                continue
            if other["taxpayer_id"] != data["taxpayer_id"]:
                raise _invalid(
                    "A case's channel records describe one taxpayer", "taxpayer_id", "invalid"
                )
            prior = other["account_holder"]
            if prior["record_id"] == holder["record_id"] and prior != holder:
                raise _invalid(
                    "account_holder differs from the case's other submissions",
                    "account_holder",
                    "account_holder_conflict",
                )

    @staticmethod
    def _record(
        env: _Envelope, tx: ca.ParsedTransaction, digest: str, actor: Actor, as_of: str
    ) -> dict[str, Any]:
        data, prov = env.data, env.provenance
        record_id = _hash_id(
            "pcr-",
            data["tenant_id"],
            data["case_id"],
            data["record_set_id"],
            data["record_set_version"],
            tx.line,
        )
        evidence = prov["evidence"]
        return {
            "version": RECORD_VERSION,
            "record_id": record_id,
            "tenant_id": data["tenant_id"],
            "case_id": data["case_id"],
            "taxpayer_id": data["taxpayer_id"],
            "record_set": {
                "id": data["record_set_id"],
                "version": data["record_set_version"],
                "sha256": digest,
                "supersedes": copy.deepcopy(data["supersedes"]),
                "correction_reason": data.get("correction_reason"),
            },
            "channel": prov["channel"],
            "transaction": {
                "provider_reference": tx.provider_reference,
                "account_ref": tx.account_ref,
                "date": tx.date,
                "direction": tx.direction,
                "amount": tx.money(),
                "counterparty": {
                    "name": tx.counterparty.name,
                    "account_ref": tx.counterparty.account_ref,
                    "country": tx.counterparty.country,
                },
                "narration": tx.narration,
                "details": copy.deepcopy(tx.details),
            },
            "permission": copy.deepcopy(env.permission),
            "provenance": {
                "channel": prov["channel"],
                "provider": dict(prov["provider"]),
                "evidence_ref": {
                    "evidence_id": evidence["evidence_id"],
                    "document_sha256": evidence["document_sha256"],
                    "locator": {"page": None, "record": f"line:{tx.line}"},
                },
                "file_name": evidence["file_name"],
                "media_type": evidence["media_type"],
                "size": evidence["size"],
                "retrieval_method": prov["retrieval_method"],
                "received_at": prov["received_at"],
                "recorded_at": as_of,
                "recorded_by": {"user_id": actor.user_id, "role": actor.role},
                "adapter": dict(prov["adapter"]),
            },
            "gaps": [dict(g, record_id=record_id) for g in tx.gaps],
            "line_contradictions": copy.deepcopy(tx.contradictions),
            "interpretation": None,
        }

    def _result(
        self, status: str, key: tuple[str, str], set_id: str, entry: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "status": status,
            "tenant_id": key[0],
            "case_id": key[1],
            "record_set_id": set_id,
            "record_set_version": entry["record_set_version"],
            "record_set_sha256": entry["record_set_sha256"],
            "record_ids": [r["record_id"] for r in entry["records"]],
        }

    # -- reads

    def _case(self, scope: ChannelScope) -> dict[str, list[dict[str, Any]]]:
        if scope.role not in READ_ROLES:
            raise _refused("This role cannot read channel records", "scope.role", "forbidden")
        sets = self._sets.get((scope.tenant_id, scope.case_id))
        if not sets:
            raise ChannelContractError(
                "not_found", "No channel records in this scope", field="case_id", reason="not_found"
            )
        return sets

    def _current(self, scope: ChannelScope) -> list[dict[str, Any]]:
        sets = self._case(scope)
        records = [r for versions in sets.values() for r in versions[-1]["records"]]
        return sorted(records, key=lambda r: r["record_id"])

    def analysis(self, scope: ChannelScope) -> dict[str, Any]:
        """Contradictions, context and gaps across the case's current records. Nothing is
        resolved; payment method or channel never produces an entry on its own."""
        records = self._current(scope)
        contradictions: list[dict[str, Any]] = []
        context: list[dict[str, Any]] = []
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for r in records:
            t = r["transaction"]
            key = (
                r["channel"],
                r["provenance"]["provider"]["provider_id"],
                t["account_ref"],
                t["provider_reference"],
            )
            groups.setdefault(key, []).append(r)
            for c in r["line_contradictions"]:
                contradictions.append(
                    {
                        "id": _hash_id("pcc-", r["record_id"], c["code"]),
                        "code": c["code"],
                        "record_ids": [r["record_id"]],
                        "detail": {k: v for k, v in c.items() if k != "code"},
                        "status": "unresolved",
                        "requires_officer_review": True,
                        "evidence_refs": [r["provenance"]["evidence_ref"]],
                    }
                )
        for group_key, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            ids = sorted(r["record_id"] for r in group)
            facts = {
                (
                    r["transaction"]["date"],
                    r["transaction"]["direction"],
                    json.dumps(r["transaction"]["amount"], sort_keys=True),
                )
                for r in group
            }
            refs = [
                r["provenance"]["evidence_ref"] for r in sorted(group, key=lambda r: r["record_id"])
            ]
            if len(facts) > 1:
                contradictions.append(
                    {
                        "id": _hash_id("pcc-", *ids),
                        "code": "same_transaction_reference_differs",
                        "record_ids": ids,
                        "detail": {"provider_reference": group_key[3]},
                        "status": "unresolved",
                        "requires_officer_review": True,
                        "evidence_refs": refs,
                    }
                )
            else:
                context.append(
                    {
                        "code": "same_transaction_in_multiple_files",
                        "record_ids": ids,
                        "evidence_refs": refs,
                    }
                )
        gaps = [g for r in records for g in r["gaps"]]
        return {
            "contradictions": sorted(contradictions, key=lambda c: c["id"]),
            "context": context,
            "gaps": sorted(gaps, key=lambda g: (g["record_id"], g["code"])),
        }

    def records(
        self,
        scope: ChannelScope,
        *,
        include_superseded: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """A page of the case's records in record-ID order (contract list shape)."""
        sets = self._case(scope)
        if limit is None:
            limit = DEFAULT_LIMIT
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise _invalid(f"limit must be 1..{MAX_LIMIT}", "limit", "invalid")
        analysis = self.analysis(scope)
        by_record: dict[str, list[str]] = {}
        for c in analysis["contradictions"]:
            for rid in c["record_ids"]:
                by_record.setdefault(rid, []).append(c["id"])
        rows: list[dict[str, Any]] = []
        for versions in sets.values():
            last = len(versions)
            for entry in versions:
                if entry["record_set_version"] != last and not include_superseded:
                    continue
                status = "current" if entry["record_set_version"] == last else "superseded"
                for r in entry["records"]:
                    view = copy.deepcopy(r)
                    view["status"] = status
                    view["contradiction_ids"] = sorted(by_record.get(r["record_id"], []))
                    rows.append(view)
        rows.sort(key=lambda r: r["record_id"])
        if cursor is not None:
            rows = [r for r in rows if r["record_id"] > cursor]
        page = rows[:limit]
        has_more = len(rows) > limit
        return {
            "data": page,
            "meta": {
                "next_cursor": page[-1]["record_id"] if has_more else None,
                "has_more": has_more,
                "review_route": dict(REVIEW_ROUTE),
                "handling": dict(HANDLING),
                "notice": NOTICE,
            },
        }

    def history(self, scope: ChannelScope, record_set_id: str) -> list[dict[str, Any]]:
        """Every recorded version of one record set, oldest first."""
        versions = self._case(scope).get(record_set_id)
        if versions is None:
            raise ChannelContractError(
                "not_found", "Record set not found", field="record_set_id", reason="not_found"
            )
        return [
            {
                "record_set_version": e["record_set_version"],
                "record_set_sha256": e["record_set_sha256"],
                "supersedes": copy.deepcopy(e["envelope"]["supersedes"]),
                "correction_reason": e["envelope"].get("correction_reason"),
                "evidence_ref": {
                    "evidence_id": e["provenance"]["evidence"]["evidence_id"],
                    "document_sha256": e["provenance"]["evidence"]["document_sha256"],
                },
                "recorded_at": e["recorded_at"],
                "record_ids": [r["record_id"] for r in e["records"]],
            }
            for e in versions
        ]

    # -- FtM / graph mapping

    def ftm_bundle(self, scope: ChannelScope) -> dict[str, Any]:
        """Map the current records to a ``tax_entity_bundle.v1`` and FtM entities.

        Each record with an ISO-currency amount becomes one FtM ``Payment`` (payer and
        beneficiary from its direction). Counterparties are one party per record: parties are
        never joined on names or account references here (candidate matching is yente review
        work). A record without an ISO-currency value is listed as ``unmapped`` with its gap.
        ``channel_links`` keeps each Payment's permission, provenance, gaps and contradictions.
        """
        sets = self._case(scope)
        records = self._current(scope)
        analysis = self.analysis(scope)
        entries = [versions[-1] for versions in sets.values()]
        holder = entries[0]["envelope"]["account_holder"]
        evidence: dict[str, dict[str, Any]] = {}
        for e in entries:
            ev = e["provenance"]["evidence"]
            evidence[ev["evidence_id"]] = {
                "evidence_id": ev["evidence_id"],
                "document_sha256": ev["document_sha256"],
                "file_name": ev["file_name"],
                "media_type": ev["media_type"],
                "size": ev["size"],
                "received_on": e["provenance"]["received_at"][:10],
            }
        holder_source = {
            "system": "synthetic_fixture",
            "record_type": "payment_channel.account_holder",
            "record_id": holder["record_id"],
        }
        parties: list[dict[str, Any]] = [
            {
                "id": holder["record_id"],
                "kind": holder["kind"],
                "name": holder["name"],
                "role": "taxpayer",
                "source": holder_source,
            }
        ]
        payments: list[dict[str, Any]] = []
        unmapped: list[dict[str, Any]] = []
        for r in records:
            t = r["transaction"]
            if t["amount"] is None:
                unmapped.append(
                    {
                        "record_id": r["record_id"],
                        "reason": [g["code"] for g in r["gaps"]] or ["no_iso_currency_value"],
                    }
                )
                continue
            party_id = "cp-" + r["record_id"][4:]
            parties.append(
                {
                    "id": party_id,
                    "kind": "legal_entity",
                    "name": t["counterparty"]["name"],
                    "country": t["counterparty"]["country"],
                    "role": "counterparty",
                    "source": {
                        "system": "synthetic_fixture",
                        "record_type": f"payment_channel.{r['channel']}.counterparty",
                        "record_id": r["record_id"],
                    },
                    "evidence": [r["provenance"]["evidence_ref"]],
                }
            )
            payer, beneficiary = (
                (party_id, holder["record_id"])
                if t["direction"] == "inflow"
                else (holder["record_id"], party_id)
            )
            payments.append(
                {
                    "id": r["record_id"],
                    "payer_id": payer,
                    "beneficiary_id": beneficiary,
                    "amount": t["amount"]["amount"],
                    "currency": t["amount"]["currency"],
                    "date": t["date"],
                    "source": {
                        "system": "synthetic_fixture",
                        "record_type": f"payment_channel.{r['channel']}",
                        "record_id": r["record_id"],
                    },
                    "evidence": [r["provenance"]["evidence_ref"]],
                }
            )
        bundle = {
            "version": tm.BUNDLE_VERSION,
            "tenant_id": scope.tenant_id,
            "evidence": sorted(evidence.values(), key=lambda e: e["evidence_id"]),
            "parties": parties,
            "payments": payments,
        }
        mapped = tm.map_bundle(bundle)
        contradiction_ids: dict[str, list[str]] = {}
        for c in analysis["contradictions"]:
            for rid in c["record_ids"]:
                contradiction_ids.setdefault(rid, []).append(c["id"])
        by_id = {r["record_id"]: r for r in records}
        links = []
        for ext in mapped.extensions:
            if ext["kind"] != "payment":
                continue
            r = by_id[ext["source"]["record_id"]]
            links.append(
                {
                    "entity_id": ext["entity_id"],
                    "record_id": r["record_id"],
                    "channel": r["channel"],
                    "details": copy.deepcopy(r["transaction"]["details"]),
                    "permission": {
                        "basis": r["permission"]["basis"],
                        "reference": r["permission"]["reference"],
                        "expires_at": r["permission"]["expires_at"],
                    },
                    "provenance": copy.deepcopy(r["provenance"]),
                    "gaps": copy.deepcopy(r["gaps"]),
                    "contradiction_ids": sorted(contradiction_ids.get(r["record_id"], [])),
                    "interpretation": None,
                }
            )
        return {
            "bundle": bundle,
            "entities": mapped.entities,
            "extensions": mapped.extensions,
            "digests": mapped.digests,
            "channel_links": sorted(links, key=lambda x: x["entity_id"]),
            "unmapped": unmapped,
            "contradictions": analysis["contradictions"],
            "gaps": analysis["gaps"],
            "review_route": dict(REVIEW_ROUTE),
        }

    def scheme_graph_input(self, scope: ChannelScope) -> dict[str, Any]:
        """The case's channel bundle as a TAX-074 ``scheme_graph_input.v1`` (no extra links)."""
        mapped = self.ftm_bundle(scope)
        holder = mapped["bundle"]["parties"][0]["id"]
        return {
            "version": sg.INPUT_VERSION,
            "synthetic": True,
            "tenant_id": scope.tenant_id,
            "case_id": scope.case_id,
            "case_taxpayer_id": holder,
            "entity_bundle": mapped["bundle"],
        }
