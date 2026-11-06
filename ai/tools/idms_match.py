"""Lekuka/IDMS input-VAT match in hybrid mode (TAX-112, framework BP4, ADR-040).

Each input-VAT line of a refund claim is compared with RSL's own records:

- a **domestic** line with the IDMS invoice of the same supplier TIN and invoice number
  (date within a window, amounts exact) and with whether that supplier declared the
  matching output VAT;
- an **import** line with the ASYCUDA World entry it cites (importer, entry, import VAT).

Classifications:

``matched``                 IDMS invoice found and its supplier declared it (or the
                            ASYCUDA entry matches an import line).
``supplier_gap``            IDMS invoice found, but the supplier did not declare it.
``unintegrated_supplier``   The supplier is not yet integrated with Lekuka; the taxpayer's
                            uploaded invoice copy is used instead (BP1).
``unmatched``               No evidence found.

Hybrid mode (default): ``supplier_gap`` and ``unmatched`` lines become targeted questions,
never a denial. ``hard_rule_mode`` exists only so RSL Legal can later confirm whether a
missing IDMS invoice may be refused; it defaults off and only changes the wording of the
line's treatment, never makes a decision.

Matching never joins on names. A supplier TIN that is not registered is tested for a
single adjacent-digit transposition of a registered TIN (a typing slip); only when no such
TIN exists is it reported as ``supplier_not_registered``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from ai.tools.refund_reconciliation import money, parse_money

MATCH_VERSION = "tax112-idms-match-v1"
DATE_WINDOW_DAYS = 45

MATCHED = "matched"
SUPPLIER_GAP = "supplier_gap"
UNINTEGRATED = "unintegrated_supplier"
UNMATCHED = "unmatched"


def transpositions(tin: str) -> set[str]:
    """Every TIN one adjacent-digit swap away from ``tin``."""
    out = set()
    for i in range(len(tin) - 1):
        if tin[i] != tin[i + 1]:
            out.add(tin[:i] + tin[i + 1] + tin[i] + tin[i + 2 :])
    return out


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def match_claim_lines(
    claim: Mapping[str, Any],
    *,
    idms_invoices: Iterable[Mapping[str, Any]],
    idms_available: bool,
    integrated_tins: Iterable[str],
    registered_tins: Iterable[str],
    asycuda_entries: Iterable[Mapping[str, Any]] = (),
    documents: Mapping[str, Mapping[str, Any]] | None = None,
    hard_rule_mode: bool = False,
    currency: str = "LSL",
) -> dict[str, Any]:
    """Classify each claimed input-VAT line; returns a per-line schedule and totals.

    ``documents`` maps an uploaded document ID to its extracted fields (``supplier_tin``,
    ``invoice_number``, ``net_amount``, ``vat_amount``), as Docling output would supply.
    """
    integrated = frozenset(integrated_tins)
    registered = frozenset(registered_tins)
    docs = documents or {}
    by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for inv in idms_invoices:
        by_key[(str(inv["supplier_tin"]), str(inv["invoice_number"]))] = inv
    entries = {str(e["entry_id"]): e for e in asycuda_entries}
    uploads: dict[str, list[str]] = {}
    for item in claim.get("uploaded_documents", []) or []:
        uploads.setdefault(str(item["line_id"]), []).append(str(item["document_id"]))

    seen: dict[tuple[str, str, str], str] = {}
    lines: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = {}
    for raw in claim.get("lines", []) or []:
        line_id = str(raw["line_id"])
        supplier = str(raw["supplier_ref"])
        number = str(raw["invoice_number"])
        vat = parse_money(raw["vat_amount"], field=f"line {line_id}.vat_amount")
        net = parse_money(raw["net_amount"], field=f"line {line_id}.net_amount")
        notes: list[str] = []
        questions: list[str] = []
        evidence: list[str] = [line_id]
        dup_key = (supplier, number, str(raw["vat_amount"]))
        if dup_key in seen:
            status = UNMATCHED
            notes.append("duplicate_of_line:" + seen[dup_key])
            questions.append("confirm_line_is_not_a_duplicate")
        elif raw.get("kind") == "import":
            seen[dup_key] = line_id
            entry = entries.get(number)
            if entry is not None and parse_money(entry["import_vat"], field="entry") == vat:
                status = MATCHED
                evidence.append("asycuda:" + number)
            else:
                status = UNMATCHED
                questions.append("provide_customs_entry_for_import_vat")
        else:
            seen[dup_key] = line_id
            tin = supplier
            if tin not in registered:
                near = sorted(t for t in transpositions(tin) if t in registered)
                if len(near) == 1:
                    notes.append("tin_probable_typing_slip:" + near[0])
                    tin = near[0]
                else:
                    notes.append("supplier_not_registered")
            status, extra_evidence, extra_questions = _domestic(
                raw,
                tin,
                net,
                vat,
                by_key,
                idms_available,
                integrated,
                uploads.get(line_id, []),
                docs,
            )
            evidence.extend(extra_evidence)
            questions.extend(extra_questions)
            if "supplier_not_registered" in notes and status != MATCHED:
                status = UNMATCHED
                questions = ["confirm_supplier_identity_and_registration"]
        treatment = (
            "accepted_as_evidence"
            if status in {MATCHED, UNINTEGRATED}
            else ("targeted_question" if not hard_rule_mode else "refusal_candidate_pending_legal")
        )
        totals[status] = totals.get(status, Decimal("0.00")) + vat
        lines.append(
            {
                "line_id": line_id,
                "status": status,
                "vat": money(vat, currency),
                "treatment": treatment,
                "notes": notes,
                "questions": questions,
                "evidence_ids": evidence,
            }
        )
    return {
        "method": "deterministic_decimal",
        "match_version": MATCH_VERSION,
        "mode": "hard_rule" if hard_rule_mode else "hybrid",
        "idms_available": idms_available,
        "lines": lines,
        "vat_by_status": {k: money(v, currency) for k, v in sorted(totals.items())},
        "decision": None,
    }


def _domestic(
    raw: Mapping[str, Any],
    tin: str,
    net: Decimal,
    vat: Decimal,
    by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    idms_available: bool,
    integrated: frozenset[str],
    uploads: list[str],
    docs: Mapping[str, Mapping[str, Any]],
) -> tuple[str, list[str], list[str]]:
    number = str(raw["invoice_number"])
    invoice = by_key.get((tin, number)) if idms_available else None
    if invoice is not None:
        same = (
            parse_money(invoice["vat_amount"], field="idms.vat") == vat
            and parse_money(invoice["net_amount"], field="idms.net") == net
            and _days(str(invoice["issue_date"]), str(raw["invoice_date"])) <= DATE_WINDOW_DAYS
        )
        if not same:
            return UNMATCHED, ["idms:" + str(invoice["idms_id"])], ["explain_difference_to_idms"]
        if invoice.get("supplier_output_declared") is True:
            return MATCHED, ["idms:" + str(invoice["idms_id"])], []
        return SUPPLIER_GAP, ["idms:" + str(invoice["idms_id"])], ["confirm_supply_with_supplier"]
    if idms_available and tin in integrated:
        return UNMATCHED, [], ["provide_invoice_absent_from_idms"]
    for doc_id in uploads:
        doc = docs.get(doc_id)
        if doc is None:
            continue
        if (
            doc.get("supplier_tin") == tin
            and doc.get("invoice_number") == number
            and parse_money(doc.get("vat_amount"), field="doc.vat") == vat
        ):
            return UNINTEGRATED, ["document:" + doc_id], []
        return UNMATCHED, ["document:" + doc_id], ["explain_difference_to_uploaded_invoice"]
    return UNMATCHED, [], ["upload_invoice_copy"]
