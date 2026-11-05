"""TAX-053 briefing port on the router: request -> ``CompletionTask`` (ADR-021, ADR-031).

The hosted or local draft must pass ``case_briefing.validate_draft`` against the
references in the request itself with no statement rejected; otherwise the call
falls back. The template level is ``DeterministicBriefingModel``. The graph still
validates whatever is returned against the verified search, so this check only
decides which level's draft is used.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ai.models.router import CompletionTask

OUTPUT_SCHEMA = (
    'Return {"sections": {<section>: [statement, ...]}} with every section listed above. '
    'A statement is {"kind": <kind>, "text": <plain sentence, at most 600 characters>, '
    '"refs": [{"citation": <citation_id>} | {"search": <search_id>} | {"document": '
    '<evidence_id>}]}; next_action statements also carry "action": one of request_evidence, '
    "human_clearance, specialist_review. Allowed kinds: what_happened observed_fact; "
    "what_was_checked check; what_is_explained explained; what_is_unresolved "
    "missing_information, unsearchable or untrusted_content; next_action proposed_action. "
    "Cite only IDs present in this request."
)


def _request_scope(request: Mapping[str, Any]) -> dict[str, object]:
    """A search-shaped view of the request, so references can be checked before the graph."""
    hits = {}
    for item in request.get("untrusted_evidence") or []:
        if isinstance(item, Mapping) and isinstance(item.get("citation_id"), str):
            locator = item.get("locator")
            hits[item["citation_id"]] = {
                "evidence_id": "request-scoped",
                "document_sha256": "request-scoped",
                "locator": dict(locator) if isinstance(locator, Mapping) else {},
                # Figures a statement may state come from the text it cites (TAX-097).
                "excerpt": item.get("untrusted_text"),
            }
    return {
        "hits": hits,
        "searches": list(request.get("searches") or []),
        "documents": list(request.get("documents") or []),
    }


def briefing_validator(request: Mapping[str, Any]):
    from ai.workflows.case_briefing import validate_draft

    scope = _request_scope(request)

    def validate(draft: object) -> str | None:
        _, rejected = validate_draft(draft, scope)
        if not rejected:
            return None
        return f"rejected:{rejected[0]['reason']}"

    return validate


def briefing_task(request: Mapping[str, Any]) -> CompletionTask:
    from ai.workflows import case_briefing as cb

    template = cb.DeterministicBriefingModel()
    typed = cast(cb.BriefingRequest, request)
    payload = {k: v for k, v in request.items() if k != "instructions"}
    return CompletionTask(
        name="case_briefing",
        prompt_version=cb.PROMPT_VERSION,
        instructions=f"{cb.INSTRUCTIONS} {OUTPUT_SCHEMA}",
        payload=payload,
        template=lambda _payload: template.draft(typed),
        validate=briefing_validator(request),
        template_id=template.model_id,
    )
