"""The case reference card: every identifier of one case, in one shape (TAX-133, ADR-052).

Pure functions with no Frappe import. ``vati_tax.references.store`` reads the stored
case, its ``Vati Service Link`` rows (TAX-030) and the taxpayer's masked TIN, and this
module turns them into the card every Vati surface shows:

- the Vati case (``case_id``, ``display_id``);
- the taxpayer's TIN, masked as everywhere else (never the full TIN);
- the RSL claim (Online Refunds) and the RSL audit case (the audit system) the case is
  about, one of each at most (``ids.canonical`` enforces that);
- the Jube case and the OpenAleph collection holding the case's work;
- other RSL records the case relies on (e-Tax returns, IDMS invoices, ASYCUDA entries,
  PSRM records).

What a caller may see depends on the audience. Officers see the whole card. A taxpayer
sees the references RSL or Vati already gave them (their case number, their TIN, the
RSL claim and the RSL audit case) and never the internal tool references (Jube,
OpenAleph) or the list of RSL records officers are working from. A hidden reference is
``null`` (or an empty list) whether or not it exists, so the card does not reveal it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from vati_tax.ids.canonical import RSL_SERVICES

OFFICER = "officer"
TAXPAYER = "taxpayer"
Audience = Literal["officer", "taxpayer"]
AUDIENCES = (OFFICER, TAXPAYER)

# (service, external_type) -> card field. All four are one-per-case in ids.canonical.
SLOTS: dict[tuple[str, str], str] = {
    ("online_refunds", "claim"): "rsl_claim_id",
    ("audit_system", "audit_case"): "rsl_audit_case_ref",
    ("jube", "case"): "jube_case_id",
    ("openaleph", "collection"): "openaleph_collection_id",
}
TAXPAYER_SLOTS = frozenset({"rsl_claim_id", "rsl_audit_case_ref"})
MAX_RSL_RECORDS = 200


class UnknownAudience(ValueError):
    """A caller asked for an audience this module does not define."""


def card(
    *,
    tenant_id: str,
    case_id: str,
    display_id: str,
    tin_masked: str | None,
    links: Iterable[Mapping[str, Any]],
    audience: str,
) -> dict[str, Any]:
    """The reference card for ``audience`` from the case's stored service links.

    ``links`` are ``Vati Service Link`` rows of this case only (``service``,
    ``external_type``, ``external_id``); the caller has already checked that the case
    belongs to the caller's tenant (and, for a taxpayer, to the caller).
    """
    if audience not in AUDIENCES:
        raise UnknownAudience(audience)
    slots: dict[str, str | None] = dict.fromkeys(SLOTS.values())
    records: list[dict[str, str]] = []
    for link in links:
        service, external_type = str(link["service"]), str(link["external_type"])
        external_id = link.get("external_id")
        if not external_id:
            # Not yet acknowledged by the upstream: nothing to show.
            continue
        slot = SLOTS.get((service, external_type))
        if slot is not None:
            # One per case is enforced on write; keep the first if older data disagrees.
            if slots[slot] is None:
                slots[slot] = str(external_id)
        elif service in RSL_SERVICES:
            records.append(
                {"system": service, "record_type": external_type, "reference": str(external_id)}
            )
    records.sort(key=lambda r: (r["system"], r["record_type"], r["reference"]))
    if audience == TAXPAYER:
        slots = {k: (v if k in TAXPAYER_SLOTS else None) for k, v in slots.items()}
        records = []
    return {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "display_id": display_id,
        "audience": audience,
        "tin_masked": tin_masked or None,
        **slots,
        "rsl_records": records[:MAX_RSL_RECORDS],
        "rsl_records_truncated": len(records) > MAX_RSL_RECORDS,
    }
