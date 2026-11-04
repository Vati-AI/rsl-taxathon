"""Versioned legal-route registry and deterministic citation validation (TAX-052).

The v1 registry holds only synthetic placeholder authorities. It is not legal
advice and contains no verified Lesotho law. Real authorities need independent
RSL legal validation and a reviewed change; the loader refuses non-synthetic
entries so placeholder fixtures cannot be mistaken for, or replaced by,
unreviewed real citations.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("legal_route_registry.synthetic.json")

VerificationStatus: TypeAlias = Literal["reviewer_verified", "unverified"]
CitationRejection: TypeAlias = Literal[
    "citation_malformed",
    "citation_not_in_registry",
    "citation_version_mismatch",
    "citation_not_effective",
    "citation_unverified",
    "citation_not_applicable",
]

_PLACEHOLDER_ID = re.compile(r"SYN-PLACEHOLDER-AUTH-\d{3}")
_SYNTHETIC_REVIEWER = re.compile(r"SYN-REVIEWER-\d{2}")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.\-]{0,31}")
_FINDING_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STATUSES: frozenset[str] = frozenset({"reviewer_verified", "unverified"})


class RegistryError(ValueError):
    """The registry document is malformed or violates the placeholder policy."""


class Citation(TypedDict):
    authority_id: str
    version: str


@dataclass(frozen=True)
class AuthorityEntry:
    authority_id: str
    version: str
    title: str
    effective_from: date
    effective_to: date | None
    verification_status: VerificationStatus
    reviewer: str | None
    reviewed_on: date | None
    finding_codes: frozenset[str]
    synthetic: bool

    def is_effective(self, as_of: date) -> bool:
        if as_of < self.effective_from:
            return False
        return self.effective_to is None or as_of <= self.effective_to

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "reviewer_verified"

    def audit_record(self, registry_version: str) -> dict[str, object]:
        return {
            "registry_version": registry_version,
            "authority_id": self.authority_id,
            "version": self.version,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "verification_status": self.verification_status,
            "reviewer": self.reviewer,
            "reviewed_on": self.reviewed_on.isoformat() if self.reviewed_on else None,
            "synthetic": self.synthetic,
        }


@dataclass(frozen=True)
class CitationCheck:
    citation: Citation | None
    entry: AuthorityEntry | None
    rejection: CitationRejection | None

    @property
    def accepted(self) -> bool:
        return self.rejection is None


class LegalRouteRegistry:
    """Immutable, versioned lookup of placeholder authorities by ID and version."""

    def __init__(self, registry_version: str, entries: Sequence[AuthorityEntry]) -> None:
        if not isinstance(registry_version, str) or not registry_version.strip():
            raise RegistryError("registry_version must be a non-empty string")
        index: dict[tuple[str, str], AuthorityEntry] = {}
        for entry in entries:
            key = (entry.authority_id, entry.version)
            if key in index:
                raise RegistryError(f"duplicate authority version {key[0]}@{key[1]}")
            index[key] = entry
        self.registry_version = registry_version
        self._entries = index

    @property
    def entries(self) -> tuple[AuthorityEntry, ...]:
        return tuple(self._entries.values())

    def get(self, authority_id: str, version: str) -> AuthorityEntry | None:
        return self._entries.get((authority_id, version))

    def has_authority(self, authority_id: str) -> bool:
        return any(key[0] == authority_id for key in self._entries)

    def routes_for(self, finding_code: str, as_of: date) -> tuple[AuthorityEntry, ...]:
        """Entries that name this finding code and are in force on ``as_of``."""
        return tuple(
            entry
            for entry in self._entries.values()
            if finding_code in entry.finding_codes and entry.is_effective(as_of)
        )

    def check_citation(self, raw: object, *, finding_code: str, as_of: date) -> CitationCheck:
        """Validate an untrusted (e.g. model-proposed) citation against the registry."""
        citation = _parse_citation(raw)
        if citation is None:
            return CitationCheck(None, None, "citation_malformed")
        if not self.has_authority(citation["authority_id"]):
            return CitationCheck(citation, None, "citation_not_in_registry")
        entry = self.get(citation["authority_id"], citation["version"])
        if entry is None:
            return CitationCheck(citation, None, "citation_version_mismatch")
        if not entry.is_effective(as_of):
            return CitationCheck(citation, entry, "citation_not_effective")
        if not entry.is_verified:
            return CitationCheck(citation, entry, "citation_unverified")
        if finding_code not in entry.finding_codes:
            return CitationCheck(citation, entry, "citation_not_applicable")
        return CitationCheck(citation, entry, None)


def _parse_citation(raw: object) -> Citation | None:
    if not isinstance(raw, Mapping) or set(raw) != {"authority_id", "version"}:
        return None
    authority_id = raw["authority_id"]
    version = raw["version"]
    if not isinstance(authority_id, str) or not isinstance(version, str):
        return None
    if len(authority_id) > 64 or not _VERSION.fullmatch(version):
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-_/ ]{0,63}", authority_id):
        return None
    return {"authority_id": authority_id, "version": version}


def _date(value: object, field: str, *, optional: bool = False) -> date | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise RegistryError(f"{field} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RegistryError(f"{field} must be an ISO date string") from exc


def _entry(raw: object) -> AuthorityEntry:
    if not isinstance(raw, dict):
        raise RegistryError("registry entries must be objects")
    authority_id = raw.get("authority_id")
    if not isinstance(authority_id, str) or not _PLACEHOLDER_ID.fullmatch(authority_id):
        raise RegistryError(
            "v1 registry accepts only SYN-PLACEHOLDER-AUTH-NNN authorities; real "
            "authorities require RSL legal validation in a reviewed change"
        )
    if raw.get("synthetic") is not True:
        raise RegistryError(f"{authority_id} must be marked synthetic")
    version = raw.get("version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise RegistryError(f"{authority_id} has an invalid version")
    title = raw.get("title")
    if not isinstance(title, str) or not title.startswith("Synthetic placeholder"):
        raise RegistryError(f"{authority_id}@{version} title must declare a synthetic placeholder")
    status = raw.get("verification_status")
    if status not in _STATUSES:
        raise RegistryError(f"{authority_id}@{version} has an unknown verification_status")
    effective_from = _date(raw.get("effective_from"), "effective_from")
    assert effective_from is not None
    effective_to = _date(raw.get("effective_to"), "effective_to", optional=True)
    if effective_to is not None and effective_to < effective_from:
        raise RegistryError(f"{authority_id}@{version} ends before it starts")
    reviewer = raw.get("reviewer")
    reviewed_on = _date(raw.get("reviewed_on"), "reviewed_on", optional=True)
    if status == "reviewer_verified":
        if not isinstance(reviewer, str) or not _SYNTHETIC_REVIEWER.fullmatch(reviewer):
            raise RegistryError(f"{authority_id}@{version} verified without a synthetic reviewer")
        if reviewed_on is None:
            raise RegistryError(f"{authority_id}@{version} verified without a review date")
    elif reviewer is not None or reviewed_on is not None:
        raise RegistryError(f"{authority_id}@{version} is unverified but records a review")
    codes = raw.get("finding_codes")
    if (
        not isinstance(codes, list)
        or not codes
        or not all(isinstance(code, str) and _FINDING_CODE.fullmatch(code) for code in codes)
    ):
        raise RegistryError(f"{authority_id}@{version} needs valid finding_codes")
    return AuthorityEntry(
        authority_id=authority_id,
        version=version,
        title=title,
        effective_from=effective_from,
        effective_to=effective_to,
        verification_status=status,
        reviewer=reviewer if isinstance(reviewer, str) else None,
        reviewed_on=reviewed_on,
        finding_codes=frozenset(codes),
        synthetic=True,
    )


def parse_registry(document: object) -> LegalRouteRegistry:
    if not isinstance(document, dict) or document.get("synthetic") is not True:
        raise RegistryError("v1 legal-route registry must be marked synthetic")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise RegistryError("registry entries must be a list")
    version = document.get("registry_version")
    if not isinstance(version, str):
        raise RegistryError("registry_version must be a non-empty string")
    return LegalRouteRegistry(version, [_entry(item) for item in entries])


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> LegalRouteRegistry:
    return parse_registry(json.loads(path.read_text(encoding="utf-8")))
