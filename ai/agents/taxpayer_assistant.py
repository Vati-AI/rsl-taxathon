"""Taxpayer compliance assistant (TAX-098; ADR-031 taxpayer permission context).

A LangGraph assistant that answers two TAX-096 taxpayer-assistance operations for
one taxpayer:

- ``explainValidationResults``: the deterministic pre-submission result for the
  taxpayer's own draft claim (``ai.tools.submission_checks``), each issue explained
  with steps the taxpayer can take;
- ``reviewSubmissionDocuments``: each of the claim's uploaded documents with the
  fields where what Docling read differs from what was declared, for the taxpayer to
  confirm, and the flagged invoices with their document references.

``draftEvidenceResponse`` belongs to TAX-099 and is answered as not available yet.

**Permission context.** Every call starts by building a :class:`TaxpayerView` for the
caller's own (tenant, taxpayer): it copies only that taxpayer's submissions, the
taxpayer-facing fields of its documents and its taxpayer-facing requests. Officer-side
records (risk scores, rule hits, lanes, notes, findings) and other taxpayers' records
are never copied, so nothing downstream can read them. The graph reaches data only
through :data:`TOOL_ALLOWLIST`; any other tool name is refused. A path naming another
taxpayer is ``404 not_found``; another taxpayer's submission or document ID is
``422 invalid_domain_input``, reported exactly like an unknown ID.

**What the model does.** Flags, figures, issue codes and document references are
deterministic. The ``fast`` role (``ai.models.router``) only words the explanation and
the suggested steps for each issue, from structured facts; it never sees document text,
so instructions planted in a document cannot reach it. Its output is validated (same
issues, allowlisted shape, bounded text) and otherwise falls back to the deterministic
template. The ``document`` role is not used: nothing here needs long-context reasoning.

**What it never does.** It never changes the store, declared figures or documents,
never sends or submits anything, never cites a document that is not the caller's own
and never invents evidence. Every suggestion has ``requires_taxpayer_confirmation:
true``; the response says ``changes_declared_figures: false`` and carries an
``AiDisclosure`` (``advisory_only: true``) from the router's provenance.
"""

from __future__ import annotations

import copy
import datetime
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ai.models import CompletionTask, RoutedModel, get_model, to_disclosure
from ai.tools import submission_checks as checks

ASSISTANT_VERSION = "tax098-taxpayer-assistant-v1"
PROMPT_VERSION = "tax098-explain-v1"
NAMESPACE = uuid.UUID("10980000-0000-4000-8000-000000000098")
OPERATIONS = ("explainValidationResults", "reviewSubmissionDocuments", "draftEvidenceResponse")
TOOL_ALLOWLIST = (
    "read_own_submission",
    "read_own_documents",
    "validate_submission",
    "compare_document_fields",
)
# Taxpayer-facing document fields the view copies; text and anything else stay behind.
DOCUMENT_FIELDS = (
    "evidence_id",
    "document_sha256",
    "document_type",
    "file_name",
    "extraction_status",
    "extracted",
)
FIELD_LABELS = {
    "invoice_number": "invoice number",
    "invoice_date": "invoice date",
    "supplier_name": "supplier name",
    "supplier_tin": "supplier TIN",
    "net_amount": "amount excluding VAT",
    "vat_amount": "VAT amount",
    "gross_amount": "total",
}
MAX_EXPLANATION = 1000
MAX_STEP = 300
MAX_STEPS = 5

Clock = Callable[[], datetime.datetime]


class ToolNotAllowed(PermissionError):
    """A tool outside the assistant's allowlist was asked for."""


class Refusal(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class TaxpayerContext:
    tenant_id: str
    taxpayer_id: str


@dataclass
class TaxpayerView:
    """Only the caller's own records; built once per call from the server store."""

    context: TaxpayerContext
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Issue codes from this taxpayer's own earlier validation results (recurring mistakes).
    earlier_codes: frozenset[str] = frozenset()
    calls: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, store: Mapping[str, Any], context: TaxpayerContext) -> TaxpayerView:
        own = context.taxpayer_id
        return cls(
            context,
            submissions={
                sid: copy.deepcopy(dict(s))
                for sid, s in (store.get("submissions") or {}).items()
                if s.get("taxpayer_id") == own
            },
            documents={
                eid: {k: copy.deepcopy(d[k]) for k in DOCUMENT_FIELDS if k in d}
                for eid, d in (store.get("documents") or {}).items()
                if d.get("taxpayer_id") == own
            },
            earlier_codes=frozenset(
                str(code)
                for result in (store.get("validation_history") or [])
                if result.get("taxpayer_id") == own
                for code in result.get("issue_codes") or []
            ),
        )

    def tool(self, name: str, **kwargs: Any) -> Any:
        """The only way the graph reads data or runs a check."""
        if name not in TOOL_ALLOWLIST:
            raise ToolNotAllowed(name)
        self.calls.append(name)
        if name == "read_own_submission":
            submission = self.submissions.get(str(kwargs["submission_id"]))
            if submission is None:  # missing and foreign look the same
                raise Refusal(422, "invalid_domain_input", "The submission was not found.")
            return submission
        if name == "read_own_documents":
            ids = [str(e) for e in kwargs.get("evidence_ids") or []]
            if any(e not in self.documents for e in ids):
                raise Refusal(422, "invalid_domain_input", "A document was not found.")
            return {e: self.documents[e] for e in ids}
        if name == "validate_submission":
            return checks.validate_submission(kwargs["submission"])
        return checks.compare_document_fields(kwargs["row"], kwargs["document"])


class State(TypedDict, total=False):
    request: dict[str, Any]
    view: TaxpayerView
    submission: dict[str, Any]
    validation: dict[str, Any]
    documents: list[dict[str, Any]]
    explained: list[dict[str, Any]]
    disclosure: dict[str, Any]
    response: dict[str, Any]


# --- explanations (fast role) ---------------------------------------------------------


def _fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


def issue_facts(
    issue: Mapping[str, Any],
    submission: Mapping[str, Any],
    duplicates: Mapping[int, int],
    earlier: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Structured, non-sensitive facts the model words; never document text."""
    rows = {r["invoice_number"]: r for r in submission.get("invoices") or []}
    row = rows.get(issue["invoice_number"]) or {}
    period = submission.get("period") or {}
    facts: dict[str, Any] = {"invoice_number": issue["invoice_number"], "issue_code": issue["code"]}
    if issue["code"] in earlier:
        facts["seen_in_an_earlier_claim"] = True
    if issue["code"] == "invoice_total_mismatch":
        net, vat = checks.money(row["net_amount"], "net"), checks.money(row["vat_amount"], "vat")
        facts.update(net=_fmt(net), vat=_fmt(vat), sum=_fmt(net + vat),
                     entered_total=_fmt(checks.money(row["gross_amount"], "gross")))  # fmt: skip
    elif issue["code"] == "invoice_period_mismatch":
        facts.update(date=row.get("invoice_date"), start=period.get("start"),
                     end=period.get("end"))  # fmt: skip
    elif issue["code"] == "duplicate_invoice":
        facts.update(first_row=duplicates.get(issue["invoice_number"]),
                     supplier_invoice_number=row.get("supplier_invoice_number"))  # fmt: skip
    return facts


def template_explanation(facts: Mapping[str, Any]) -> dict[str, Any]:
    """The deterministic wording (last fallback and the mock fixture)."""
    n, code = facts["invoice_number"], facts["issue_code"]
    if code == "invoice_total_mismatch":
        text = (
            f"Invoice {n}: {facts['net']} excluding VAT plus {facts['vat']} VAT is "
            f"{facts['sum']}, but the total entered is {facts['entered_total']}."
        )
        steps = [
            "Compare the total with the uploaded invoice.",
            "If the row was mistyped, correct it in the form yourself.",
        ]
    elif code == "duplicate_invoice":
        text = (
            f"Invoice {n} has the same supplier and invoice number "
            f"({facts['supplier_invoice_number']}) as invoice {facts['first_row']}."
        )
        steps = ["If the invoice was entered twice, remove the repeated row yourself."]
    elif code == "invoice_period_mismatch":
        text = (
            f"Invoice {n} is dated {facts['date']}, outside the claim period "
            f"{facts['start']} to {facts['end']}."
        )
        steps = ["Check the date on the invoice, or claim it in the period it belongs to."]
    elif code == "supporting_document_missing":
        text = f"Invoice {n} has no uploaded copy, so it cannot be checked against a document."
        steps = ["Upload a copy of the invoice, or remove the row if you cannot provide it."]
    elif code == "return_arithmetic_mismatch":
        text = "The refund claimed does not equal the input VAT less the output VAT."
        steps = ["Check the period's VAT figures before you send the claim."]
    else:
        text = "The input VAT claimed is more than the VAT on the invoices listed."
        steps = ["List every purchase invoice for the period, or check the input VAT figure."]
    if facts.get("seen_in_an_earlier_claim"):
        # Recurring-mistake guidance from the taxpayer's own earlier validation results.
        text += " The same kind of issue came up in one of your earlier claims."
    return {"invoice_number": n, "issue_code": code, "explanation": text, "suggested_steps": steps}


def _template(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"explanations": [template_explanation(f) for f in payload["issues"]]}


def explanation_validator(payload: Mapping[str, Any]) -> Callable[[object], str | None]:
    wanted = [(f["invoice_number"], f["issue_code"]) for f in payload["issues"]]

    def validate(output: object) -> str | None:
        if not isinstance(output, Mapping) or set(output) != {"explanations"}:
            return "invalid_shape"
        entries = output["explanations"]
        if not isinstance(entries, list):
            return "invalid_shape"
        got = []
        for e in entries:
            if not isinstance(e, Mapping) or set(e) != {
                "invoice_number", "issue_code", "explanation", "suggested_steps"
            }:  # fmt: skip
                return "invalid_shape"
            got.append((e["invoice_number"], e["issue_code"]))
            text, steps = e["explanation"], e["suggested_steps"]
            if not isinstance(text, str) or not 0 < len(text.strip()) <= MAX_EXPLANATION:
                return "invalid_explanation"
            if not isinstance(steps, list) or not 0 < len(steps) <= MAX_STEPS:
                return "invalid_steps"
            if any(not isinstance(s, str) or not 0 < len(s.strip()) <= MAX_STEP for s in steps):
                return "invalid_steps"
        return None if got == wanted else "issues_changed"

    return validate


def explain_task(issues: list[dict[str, Any]]) -> CompletionTask:
    payload = {"issues": issues}
    return CompletionTask(
        name="taxpayer_validation_explanations",
        prompt_version=PROMPT_VERSION,
        instructions=(
            "You help a taxpayer correct their own VAT refund claim before they send it. "
            "For each issue in the payload, write one short, neutral explanation using only "
            "the facts given, and one to three steps the taxpayer can take themselves. Do "
            "not accuse, do not decide anything, do not change any figure, and do not "
            'mention anything that is not in the payload. Return JSON: {"explanations": '
            '[{"invoice_number", "issue_code", "explanation", "suggested_steps"}]} '
            "with exactly the issues given, in the same order."
        ),
        payload=payload,
        template=_template,
        validate=explanation_validator(payload),
    )


def disclosure(provenance: Mapping[str, Any] | None) -> dict[str, Any]:
    """``AiDisclosure`` from the router's provenance (shared projection, ADR-034)."""
    return dict(to_disclosure(provenance or {}))


# --- graph ----------------------------------------------------------------------------


def _uuid(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(str(p) for p in parts)))


def _ref(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": doc["evidence_id"],
        "document_sha256": doc["document_sha256"],
        "locator": {"page": None, "record": None},
        "file_name": doc.get("file_name"),
    }


def _money(value: Decimal, currency: str) -> dict[str, str]:
    return {"amount": f"{value:.2f}", "currency": currency}


class TaxpayerAssistant:
    """``assistant(request) -> {"status", "body"}`` over one server store."""

    def __init__(
        self,
        store: Mapping[str, Any],
        *,
        model: RoutedModel | None = None,
        clock: Clock | None = None,
        currency: str = "LSL",
    ) -> None:
        self._store = store
        self._model = model or get_model("fast", offline=True)
        self._clock = clock or (lambda: datetime.datetime.now(datetime.UTC))
        self._currency = currency
        self._graph = self._build()

    def _build(self) -> Any:
        graph = StateGraph(State)
        graph.add_node("check", self._check)
        graph.add_node("explain", self._explain)
        graph.add_node("respond", self._respond)
        graph.add_edge(START, "check")
        graph.add_edge("check", "explain")
        graph.add_edge("explain", "respond")
        graph.add_edge("respond", END)
        return graph.compile()

    # Nodes read and write only the state; data comes through ``view.tool``.

    def _check(self, state: State) -> State:
        view, body = state["view"], state["request"]["body"]
        submission = view.tool("read_own_submission", submission_id=body.get("submission_id"))
        wanted = view.tool("read_own_documents", evidence_ids=body.get("evidence_ids") or [])
        validation = view.tool("validate_submission", submission=submission)
        documents: list[dict[str, Any]] = []
        if state["request"]["operation_id"] == "reviewSubmissionDocuments":
            rows_by_doc: dict[str, dict[str, Any]] = {}
            ordered: list[str] = []
            for row in submission.get("invoices") or []:
                for key in ("invoice_evidence_id", "contract_evidence_id"):
                    eid = row.get(key)
                    if eid and eid in view.documents and eid not in ordered:
                        if not wanted or eid in wanted:
                            ordered.append(eid)
                    if key == "invoice_evidence_id" and eid:
                        rows_by_doc.setdefault(eid, row)
            for eid in ordered:
                doc = view.documents[eid]
                row = rows_by_doc.get(eid)
                fields = (
                    view.tool("compare_document_fields", row=row, document=doc)
                    if row is not None and doc.get("document_type") == "invoice"
                    else []
                )
                documents.append({"document": doc, "fields": fields})
        return {"submission": submission, "validation": validation, "documents": documents}

    def _explain(self, state: State) -> State:
        submission = state["submission"]
        validation = state["validation"]
        issues = [
            issue_facts(i, submission, validation["duplicates"], state["view"].earlier_codes)
            for i in validation["issues"]
        ]
        output = self._model.complete(explain_task(issues))
        entries = output.output["explanations"] if isinstance(output.output, Mapping) else []
        return {
            "explained": list(entries),
            "disclosure": disclosure(output.provenance),
        }

    def _respond(self, state: State) -> State:
        request, view = state["request"], state["view"]
        submission, validation = state["submission"], state["validation"]
        rows = {r["invoice_number"]: r for r in submission.get("invoices") or []}

        def refs(n: int | None) -> list[dict[str, Any]]:
            eid = (rows.get(n) or {}).get("invoice_evidence_id") if n is not None else None
            return [_ref(view.documents[eid])] if eid and eid in view.documents else []

        explanations = [
            {
                "issue_code": e["issue_code"],
                "invoice_number": e["invoice_number"],
                "explanation": e["explanation"],
                "suggested_steps": list(e["suggested_steps"]),
                "document_refs": refs(e["invoice_number"]),
                "requires_taxpayer_confirmation": True,
            }
            for e in state["explained"]
        ]
        common = {
            "tenant_id": view.context.tenant_id,
            "taxpayer_id": view.context.taxpayer_id,
            "submission_id": submission["submission_id"],
            "disclosure": state["disclosure"],
            "changes_declared_figures": False,
            "generated_at": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        key = request["headers"].get("Idempotency-Key") or request["headers"]["X-Correlation-ID"]
        if request["operation_id"] == "explainValidationResults":
            figures = validation["figures"]
            data = {
                "explanation_id": _uuid(view.context.taxpayer_id, "explain", key),
                **common,
                "validation": {
                    "calculation_version": validation["calculation_version"],
                    "complete": validation["complete"],
                    "figures": {
                        **{k: _money(v, self._currency) for k, v in figures.items()},
                        "explained_amount": None,
                        "unresolved_amount": None,
                    },
                    "issues": [dict(i) for i in validation["issues"]],
                },
                "explanations": explanations,
            }
            return {"response": {"status": 200, "body": {"data": data}}}
        flagged: dict[int, dict[str, Any]] = {}
        for e in explanations:
            n = e["invoice_number"]
            if n is None:
                continue  # claim-level issues belong to the validation explanation
            item = flagged.setdefault(n, {"invoice_number": n, "issue_codes": [],
                                          "explanation": "", "suggested_steps": [],
                                          "document_refs": e["document_refs"],
                                          "requires_taxpayer_confirmation": True})  # fmt: skip
            item["issue_codes"].append(e["issue_code"])
            item["explanation"] = f"{item['explanation']} {e['explanation']}".strip()[
                :MAX_EXPLANATION
            ]
            for step in e["suggested_steps"]:
                if step not in item["suggested_steps"] and len(item["suggested_steps"]) < MAX_STEPS:
                    item["suggested_steps"].append(step)
        documents = [
            {
                "evidence_id": d["document"]["evidence_id"],
                "document_sha256": d["document"]["document_sha256"],
                "document_type": d["document"]["document_type"],
                "file_name": d["document"].get("file_name"),
                "extraction_status": d["document"].get("extraction_status", "pending"),
                "fields_to_confirm": [
                    {
                        **f,
                        "locator": {"page": None, "record": None},
                        "explanation": (
                            f"The {FIELD_LABELS.get(f['field'], f['field'])} on the document "
                            f"({f['extracted_value']}) differs from what you entered "
                            f"({f['declared_value']}). Check which is correct and change the "
                            "form yourself if needed."
                        )[:600],
                        "requires_taxpayer_confirmation": True,
                    }
                    for f in d["fields"]
                ],
            }
            for d in state["documents"]
        ]
        data = {
            "assistance_id": _uuid(view.context.taxpayer_id, "review", key),
            **common,
            "documents": documents,
            "flagged_invoices": [flagged[n] for n in sorted(flagged)],
        }
        return {"response": {"status": 200, "body": {"data": data}}}

    # --- entry point ------------------------------------------------------------------

    def __call__(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request = copy.deepcopy(dict(request))
        try:
            view = self._authorise(request)
            if request.get("operation_id") == "draftEvidenceResponse":
                raise Refusal(503, "upstream_unavailable",
                              "Drafting evidence responses is not available yet.")  # fmt: skip
            if request.get("operation_id") not in OPERATIONS:
                raise Refusal(400, "invalid_request", "Unknown operation.")
            result = self._graph.invoke({"request": request, "view": view})
            return dict(result["response"])
        except Refusal as refusal:
            return self._error(request, refusal)
        except checks.SubmissionDataError:
            return self._error(
                request,
                Refusal(422, "invalid_domain_input", "The submission could not be checked."),
            )

    def _authorise(self, request: Mapping[str, Any]) -> TaxpayerView:
        caller = request.get("caller") or {}
        if caller.get("audience") != "taxpayer":
            raise Refusal(403, "forbidden", "This operation is for taxpayers.")
        context = TaxpayerContext(str(caller.get("tenant_id")), str(caller.get("taxpayer_id")))
        path_taxpayer = str((request.get("path_params") or {}).get("taxpayer_id"))
        taxpayers = self._store.get("taxpayers") or {}
        own = taxpayers.get(context.taxpayer_id) or {}
        if (
            path_taxpayer != context.taxpayer_id
            or not own
            or own.get("tenant_id", context.tenant_id) != context.tenant_id
        ):
            # Another taxpayer is reported exactly like a missing one.
            raise Refusal(404, "not_found", "Not found.")
        return TaxpayerView.build(self._store, context)

    @staticmethod
    def _error(request: Mapping[str, Any], refusal: Refusal) -> dict[str, Any]:
        headers = request.get("headers") or {}
        return {
            "status": refusal.status,
            "body": {
                "error": {
                    "code": refusal.code,
                    "message": refusal.message,
                    "retryable": False,
                    "correlation_id": headers.get("X-Correlation-ID") or str(uuid.uuid4()),
                    "service": "vati",
                }
            },
        }


def make_assistant(store: Mapping[str, Any]) -> TaxpayerAssistant:
    """TAX-102 harness factory: the offline mock ``fast`` chain, so gates stay keyless."""
    return TaxpayerAssistant(store)
