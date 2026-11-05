"""Case-scoped search over extracted evidence items (TAX-053).

Pure functions, no LangGraph, Frappe or network imports, so the rules are testable
anywhere. Documents are the Vati ``Evidence`` records of one case together with
their latest extraction items (TAX-032 provenance items produced by the TAX-038
Docling adapter): every item has a page and/or record locator, so a search hit
can always be cited as a contract ``EvidenceRef``.

Access is decided here, before any document is read:

- The caller must be authenticated, a member of the requested tenant and hold a
  role that may perform ``evidence.read``. ``EVIDENCE_READ_ROLES`` mirrors
  ``vati_tax.identity.policy.ACTION_ROLES["evidence.read"]`` (a test keeps them
  equal); taxpayers never reach this search.
- Only documents of the requested tenant *and* case are searched, whatever the
  source returns. Superseded versions and documents whose extraction has not
  completed are not searched; the latter are reported so a briefing can mark
  them unresolved.

Document text is untrusted data. It is matched and excerpted, never executed or
used to change the search scope, the question list or any limit. Text that reads
like an instruction to an automated system is flagged so an officer sees it.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import TypedDict

# Mirrors vati_tax.identity.policy.ACTION_ROLES[EVIDENCE_READ]; see module docstring.
EVIDENCE_READ_ROLES = frozenset({"auditor", "specialist", "supervisor", "service"})

MAX_QUESTIONS = 12
MAX_TERMS = 8
MAX_HITS_PER_QUESTION = 5
MAX_DOCUMENTS = 500
MAX_ITEMS_PER_DOCUMENT = 5_000
EXCERPT_CHARS = 280

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}")
_TERM = re.compile(r"[a-z0-9]{2,32}")
_WORD = re.compile(r"[a-z0-9]+")
_TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ,.'()/-]{0,119}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Phrases addressed to a model or agent rather than describing a document. Used to
# flag text for an officer; it never changes what the search or briefing does.
_INSTRUCTION = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+)*"
    r"(previous|prior|above|earlier|system|instructions?|rules?)"
    r"|you\s+are\s+now|system\s+prompt|\bassistant\s*:|\bas\s+an\s+ai\b"
    r"|\b(approve|grant|release)\s+(the\s+)?refund"
    r"|\b(mark|flag|label)\b.{0,40}\b(fraud\w*|cleared|compliant|approved)"
    r"|\bcite\s+evidence\b|\bnew\s+instructions?\b",
    re.IGNORECASE,
)
COMPLETED = "completed"


class SearchDenied(Exception):
    """The caller may not read evidence in this tenant; no document was read."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SearchInputError(ValueError):
    """Questions or scope are malformed; nothing was searched."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Actor(TypedDict):
    """The caller as authenticated by the Vati session layer (same shape as TAX-047)."""

    actor_id: str
    tenant_id: str
    roles: list[str]
    authenticated: bool


class Question(TypedDict):
    question_id: str
    topic: str
    terms: list[str]


def check_read_access(actor: object, tenant_id: str) -> None:
    """Fail closed unless ``actor`` may read evidence in ``tenant_id``."""
    if not isinstance(actor, Mapping) or actor.get("authenticated") is not True:
        raise SearchDenied("actor_not_authenticated")
    if not isinstance(actor.get("actor_id"), str) or not str(actor.get("actor_id")).strip():
        raise SearchDenied("actor_not_authenticated")
    if actor.get("tenant_id") != tenant_id:
        raise SearchDenied("tenant_not_allowed")
    roles = actor.get("roles")
    if not isinstance(roles, list) or not set(roles) & EVIDENCE_READ_ROLES:
        raise SearchDenied("evidence_read_not_allowed")
    if "taxpayer" in roles:
        # A taxpayer login is never also a staff login (TAX-029); refuse a mixed scope.
        raise SearchDenied("evidence_read_not_allowed")


def normalise_questions(raw: object) -> list[Question]:
    """Validate officer/playbook questions; terms are lower-case words only."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise SearchInputError("questions_required")
    if len(raw) > MAX_QUESTIONS:
        raise SearchInputError("too_many_questions")
    seen: set[str] = set()
    out: list[Question] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise SearchInputError("invalid_question")
        qid, topic, terms = item.get("question_id"), item.get("topic"), item.get("terms")
        if not isinstance(qid, str) or not _ID.fullmatch(qid) or qid in seen:
            raise SearchInputError("invalid_question_id")
        if not isinstance(topic, str) or not _TOPIC.fullmatch(topic):
            raise SearchInputError("invalid_question_topic")
        if (
            not isinstance(terms, Sequence)
            or isinstance(terms, (str, bytes))
            or not 1 <= len(terms) <= MAX_TERMS
        ):
            raise SearchInputError("invalid_question_terms")
        clean = []
        for term in terms:
            if not isinstance(term, str) or not _TERM.fullmatch(term.lower()):
                raise SearchInputError("invalid_question_terms")
            clean.append(term.lower())
        seen.add(qid)
        out.append({"question_id": qid, "topic": topic, "terms": sorted(set(clean))})
    return out


def looks_like_instruction(text: str) -> bool:
    return bool(_INSTRUCTION.search(text))


def _clean(text: object, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(_CONTROL.sub(" ", text).split())[:limit]


def _words(*parts: object) -> set[str]:
    words: set[str] = set()
    for part in parts:
        if isinstance(part, str):
            words.update(w for w in _WORD.findall(part.lower()) if len(w) >= 2)
    return words


def _document(raw: object, tenant_id: str, case_id: str) -> dict[str, object] | None:
    """A well-formed document of this tenant and case, or ``None`` (never searched)."""
    if not isinstance(raw, Mapping):
        return None
    if raw.get("tenant_id") != tenant_id or raw.get("case_id") != case_id:
        return None
    evidence_id, sha = raw.get("evidence_id"), raw.get("document_sha256")
    if not isinstance(evidence_id, str) or not _ID.fullmatch(evidence_id):
        return None
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        return None
    items = raw.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        items = []
    return {
        "evidence_id": evidence_id,
        "document_sha256": sha,
        "document_type": _clean(raw.get("document_type"), 32) or "other",
        "supersedes_evidence_id": raw.get("supersedes_evidence_id"),
        "extraction_status": raw.get("extraction_status"),
        "items": list(items)[:MAX_ITEMS_PER_DOCUMENT],
    }


def search_case_evidence(
    documents: Iterable[object],
    *,
    actor: object,
    tenant_id: str,
    case_id: str,
    questions: object,
) -> dict[str, object]:
    """Search one case's readable evidence for each question.

    Returns ``searches`` (one per question, in order, each with a ``search_id``
    and the citation IDs it found), ``hits`` keyed by citation ID (each with the
    contract ``EvidenceRef`` fields), ``documents`` (in-scope evidence IDs),
    ``unsearchable`` documents and the number of instruction-like items seen.
    """
    check_read_access(actor, tenant_id)
    for name, value in (("tenant_id", tenant_id), ("case_id", case_id)):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise SearchInputError(f"invalid_{name}")
    qs = normalise_questions(questions)

    in_scope: list[dict[str, object]] = []
    for raw in documents:
        doc = _document(raw, tenant_id, case_id)
        if doc is not None:
            in_scope.append(doc)
        if len(in_scope) > MAX_DOCUMENTS:
            raise SearchInputError("too_many_documents")
    superseded = {d["supersedes_evidence_id"] for d in in_scope if d["supersedes_evidence_id"]}
    current = [d for d in in_scope if d["evidence_id"] not in superseded]
    current.sort(key=lambda d: str(d["evidence_id"]))
    searchable = [d for d in current if d["extraction_status"] == COMPLETED]
    unsearchable = [
        {
            "evidence_id": d["evidence_id"],
            "extraction_status": _clean(d["extraction_status"], 32) or "unknown",
        }
        for d in current
        if d["extraction_status"] != COMPLETED
    ]

    # Index every citable item once; an item needs an ID and a page or record locator.
    index: list[tuple[dict[str, object], set[str]]] = []
    flagged = 0
    for doc in searchable:
        items = doc["items"]
        assert isinstance(items, list)
        for position, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            page = item.get("page")
            record = item.get("record")
            if isinstance(page, bool) or not (isinstance(page, int) and page >= 1):
                page = None
            record = _clean(record, 255) or None
            if page is None and record is None:
                continue  # not citable
            item_id = item.get("item_id")
            if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
                item_id = f"item-{position + 1}"
            text = item.get("text")
            instruction_like = isinstance(text, str) and looks_like_instruction(text)
            flagged += int(instruction_like)
            hit: dict[str, object] = {
                "citation_id": f"{doc['evidence_id']}#{item_id}",
                "evidence_id": doc["evidence_id"],
                "document_sha256": doc["document_sha256"],
                "document_type": doc["document_type"],
                "locator": {"page": page, "record": record},
                "field": _clean(item.get("field"), 64) or None,
                "label": _clean(item.get("label"), 32) or "text",
                "excerpt": _clean(text, EXCERPT_CHARS),
                "instruction_like": instruction_like,
            }
            index.append((hit, _words(text, item.get("field"), record)))

    searches: list[dict[str, object]] = []
    hits: dict[str, dict[str, object]] = {}
    for number, question in enumerate(qs, start=1):
        terms = set(question["terms"])
        ranked = sorted(
            ((len(terms & words), hit) for hit, words in index if terms & words),
            key=lambda pair: (-pair[0], str(pair[1]["citation_id"])),
        )[:MAX_HITS_PER_QUESTION]
        found = []
        for _, hit in ranked:
            hits[str(hit["citation_id"])] = hit
            found.append(hit["citation_id"])
        searches.append(
            {
                "search_id": f"search-{number:02d}",
                "question_id": question["question_id"],
                "topic": question["topic"],
                "terms": question["terms"],
                "documents_searched": len(searchable),
                "citation_ids": found,
            }
        )
    return {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "searches": searches,
        "hits": hits,
        "documents": [d["evidence_id"] for d in current],
        "unsearchable": unsearchable,
        "instruction_like_items": flagged,
    }


# --- synthetic corpus ---------------------------------------------------------------

_NAMESPACE = uuid.UUID("6f1c2f0e-53a1-4c7e-9b8e-0a5300000053")

_RECORD_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "contracts": ("contract", ("counterparty_id", "description", "related_party")),
    "invoices": (
        "invoice",
        ("contract_id", "counterparty_id", "direction", "net_amount", "vat_amount", "gross_amount"),
    ),
    "payments": ("bank_statement", ("invoice_id", "amount")),
}
_LABELS = {"contracts": "Contract", "invoices": "Invoice", "payments": "Payment"}


def _synthetic_document(
    tenant_id: str, case_id: str, record_id: str, document_type: str, lines: list[tuple[str, str]]
) -> dict[str, object]:
    body = json.dumps([tenant_id, case_id, record_id, lines], sort_keys=True).encode()
    return {
        "evidence_id": str(uuid.uuid5(_NAMESPACE, f"{tenant_id}/{case_id}/{record_id}")),
        "tenant_id": tenant_id,
        "case_id": case_id,
        "document_sha256": hashlib.sha256(body).hexdigest(),
        "document_type": document_type,
        "supersedes_evidence_id": None,
        "extraction_status": COMPLETED,
        "synthetic": True,
        "items": [
            {
                "item_id": f"{record_id}.{field}",
                "page": 1,
                "record": record_id,
                "field": field,
                "label": "key_value",
                "text": text,
            }
            for field, text in lines
        ],
    }


def synthetic_corpus_from_records(
    records: Iterable[Mapping[str, object]], tenant_id: str
) -> list[dict[str, object]]:
    """Fixture-only corpus: one synthetic document per TAX-024 record, items cite its ID.

    Never use for real cases; real documents come from TAX-032 evidence records and
    their TAX-038 extraction items.
    """
    corpus: list[dict[str, object]] = []
    for case in records:
        if case.get("synthetic") is not True:
            raise SearchInputError("synthetic_records_only")
        case_id = str(case["case_id"])
        ret = case.get("return")
        if isinstance(ret, Mapping):
            rid = str(ret["id"])
            lines = [
                (field, f"VAT return {rid} {field.replace('_', ' ')}: {ret[field]}")
                for field in ("output_vat", "input_vat_claimed", "refund_claim")
                if field in ret
            ]
            if case.get("period"):
                lines.append(("period", f"VAT return {rid} tax period: {case['period']}"))
            corpus.append(_synthetic_document(tenant_id, case_id, rid, "other", lines))
        for key, (document_type, fields) in _RECORD_FIELDS.items():
            entries = case.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                rid = str(entry["id"])
                lines = []
                for field in fields:
                    if field not in entry:
                        continue
                    value = entry[field]
                    if isinstance(value, bool):
                        value = "yes" if value else "no"
                    label = field.replace("_", " ")
                    lines.append((field, f"{_LABELS[key]} {rid} {label}: {value}"))
                corpus.append(_synthetic_document(tenant_id, case_id, rid, document_type, lines))
    return corpus
