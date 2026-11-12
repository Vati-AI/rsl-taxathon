"""Evidence requests that ask only for the gap: pure rules (TAX-127, ADR-043 §4, ADR-050).

No Frappe import, so the rules are testable without a bench. ``store`` binds them to the
lane proposal, the RSL source and the TAX-046 evidence request.

A proposal is built from two inputs:

- RSL's own claim lines (Online Refunds Application, read through the TAX-111 source);
- the TAX-112 line match recorded on the case's latest lane proposal (``line_match``:
  ``matched``, ``unintegrated_supplier``, ``supplier_gap`` or ``unmatched`` per line).

Lines RSL already evidences are listed as **already held** (Lekuka, ASYCUDA, or the copy
the taxpayer uploaded to Online Refunds), so the taxpayer sees them and is not asked for
them. Only the other lines become items, each answered with ``confirm``, ``dispute`` or
``attach_proof``. Nothing in a proposal names a risk score, lane, finding, officer or
internal status code: the taxpayer-facing text is built from a fixed wording table.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Final

PROPOSAL_VERSION: Final = "tax127-gap-request-v1"
MAX_ITEMS: Final = 20  # the TAX-046 request item limit

CONFIRM: Final = "confirm"
DISPUTE: Final = "dispute"
ATTACH_PROOF: Final = "attach_proof"
ANSWERS: Final = (CONFIRM, DISPUTE, ATTACH_PROOF)

# TAX-112 line statuses.
MATCHED: Final = "matched"
UNINTEGRATED: Final = "unintegrated_supplier"
SUPPLIER_GAP: Final = "supplier_gap"
UNMATCHED: Final = "unmatched"
STATUSES: Final = frozenset({MATCHED, UNINTEGRATED, SUPPLIER_GAP, UNMATCHED})

# Where RSL already holds the evidence for a line it does not ask about.
HELD_BY: Final = {
    ("domestic", MATCHED): "lekuka",
    ("import", MATCHED): "asycuda",
    ("domestic", UNINTEGRATED): "online_refunds_upload",
    ("import", UNINTEGRATED): "online_refunds_upload",
}
HELD_LABELS: Final = {
    "lekuka": "Lekuka e-invoicing records",
    "asycuda": "ASYCUDA customs records",
    "online_refunds_upload": "the copy you uploaded to the Online Refunds Application",
}

# Gap codes and the only text a taxpayer sees for them.
GAPS: Final = {
    ("domestic", UNMATCHED): "not_found_in_lekuka",
    ("domestic", SUPPLIER_GAP): "supplier_not_declared",
    ("import", UNMATCHED): "customs_entry_not_matched",
    ("import", SUPPLIER_GAP): "customs_entry_not_matched",
}
GAP_TEXT: Final = {
    "not_found_in_lekuka": (
        "We could not find invoice {invoice} from supplier {supplier} ({date}, VAT {vat}) "
        "in Lekuka."
    ),
    "supplier_not_declared": (
        "Invoice {invoice} from supplier {supplier} ({date}, VAT {vat}) is in Lekuka, but the "
        "matching VAT has not been declared by the supplier."
    ),
    "customs_entry_not_matched": (
        "We could not match customs entry {invoice} ({date}, import VAT {vat}) in ASYCUDA."
    ),
}
ASK_TEXT: Final = (
    " Confirm the line is correct, dispute it, or attach proof such as the invoice and "
    "proof of payment."
)
DOCUMENT_TYPE: Final = {"domestic": "invoice", "import": "import_declaration"}

# Keys allowed anywhere in what the taxpayer will see; anything else is a defect.
TAXPAYER_KEYS: Final = frozenset(
    {
        "line_id", "invoice_number", "supplier_ref", "invoice_date", "vat_amount",
        "currency", "kind", "gap", "held_by", "held_by_label",
    }
)  # fmt: skip


class GapProposalError(ValueError):
    """The inputs cannot give a safe proposal; ``code`` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _money(value: object) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GapProposalError("invalid_claim_line", "A claim line amount is not money.") from None
    return str(amount.quantize(Decimal("0.01")))


def _line(raw: Mapping[str, Any], currency: str) -> dict[str, Any]:
    kind = str(raw.get("kind") or "domestic")
    if kind not in DOCUMENT_TYPE:
        raise GapProposalError("invalid_claim_line", "A claim line has an unknown kind.")
    return {
        "line_id": str(raw["line_id"]),
        "invoice_number": str(raw["invoice_number"]),
        "supplier_ref": str(raw["supplier_ref"]),
        "invoice_date": str(raw["invoice_date"]),
        "vat_amount": _money(raw["vat_amount"]),
        "currency": currency,
        "kind": kind,
    }


def build_proposal(
    claim: Mapping[str, Any],
    line_match: Sequence[Mapping[str, Any]],
    *,
    currency: str = "LSL",
) -> dict[str, Any]:
    """The itemised request for one claim (see the module docstring).

    Every claim line must have a match result; a line the match does not cover means the
    lane proposal is out of date, and nothing is proposed.
    """
    status_by_line: dict[str, str] = {}
    for row in line_match:
        status = str(row.get("status"))
        if status not in STATUSES:
            raise GapProposalError("unknown_match_status", "A line has an unknown match status.")
        status_by_line[str(row["line_id"])] = status
    held: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for raw in claim.get("lines") or []:
        line = _line(raw, currency)
        found = status_by_line.get(line["line_id"])
        if found is None:
            raise GapProposalError(
                "line_match_out_of_date", "The line match does not cover every claim line."
            )
        status = found
        source = HELD_BY.get((line["kind"], status))
        if source is not None:
            held.append({**line, "held_by": source, "held_by_label": HELD_LABELS[source]})
            continue
        gap = GAPS[(line["kind"], status)]
        text = GAP_TEXT[gap].format(
            invoice=line["invoice_number"], supplier=line["supplier_ref"],
            date=line["invoice_date"], vat=f"{line['currency']} {line['vat_amount']}",
        ) + ASK_TEXT  # fmt: skip
        items.append(
            {
                "document_type": DOCUMENT_TYPE[line["kind"]],
                "description": text,
                "line": {**line, "gap": gap},
                "answer_options": list(ANSWERS),
            }
        )
    if len(items) > MAX_ITEMS:
        raise GapProposalError(
            "too_many_unmatched_lines",
            f"More than {MAX_ITEMS} lines are unmatched; review the claim before asking.",
        )
    proposal = {
        "version": PROPOSAL_VERSION,
        "claim_ref": str(claim["claim_id"]),
        "period": str(claim.get("period") or ""),
        "already_held": held,
        "items": items,
        "counts": {
            "lines": len(held) + len(items),
            "already_held": len(held),
            "asked": len(items),
        },  # fmt: skip
    }
    check_taxpayer_safe(proposal)
    proposal["proposal_digest"] = digest(proposal)
    return proposal


def digest(proposal: Mapping[str, Any]) -> str:
    """Stable digest of what the officer reviewed (excluding the digest itself)."""
    body = {k: v for k, v in proposal.items() if k != "proposal_digest"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def check_taxpayer_safe(proposal: Mapping[str, Any]) -> None:
    """Refuse a proposal whose taxpayer-facing parts carry anything outside the allowlist."""
    for entry in proposal.get("already_held", []):
        extra = set(entry) - TAXPAYER_KEYS
        if extra:
            raise GapProposalError("not_taxpayer_safe", f"Unexpected field {sorted(extra)[0]}.")
    for item in proposal.get("items", []):
        if set(item) != {"document_type", "description", "line", "answer_options"}:
            raise GapProposalError("not_taxpayer_safe", "Unexpected item field.")
        extra = set(item["line"]) - TAXPAYER_KEYS
        if extra:
            raise GapProposalError("not_taxpayer_safe", f"Unexpected field {sorted(extra)[0]}.")


def request_items(proposal: Mapping[str, Any]) -> list[dict[str, str]]:
    """The TAX-046 request items (document type and description) of a proposal."""
    return [
        {"document_type": i["document_type"], "description": i["description"]}
        for i in proposal["items"]
    ]


def line_items(proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per request item (same order): the line and its allowed answers."""
    return [
        {"index": n, "line": i["line"], "answer_options": i["answer_options"]}
        for n, i in enumerate(proposal["items"])
    ]


def find_claim(records: Sequence[Mapping[str, Any]], claim_ref: str) -> Mapping[str, Any] | None:
    for record in records:
        if str(record.get("claim_id")) == claim_ref:
            return record
    return None
