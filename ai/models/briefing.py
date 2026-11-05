"""TAX-053 briefing port on the router, drafted section by section (ADR-021, ADR-031, ADR-039).

The TAX-097 live run drafted the whole briefing in one call; with gpt-oss
reasoning tokens counted against the 2,048-token output budget every hosted
briefing was truncated (TAX-110). The draft is now one bounded call per section:

- each section call gets only the request data that section needs (document text
  stays fenced in ``untrusted_evidence``; text flagged as instruction-like is not
  sent at all, only its citation ID and flag) and at most
  ``SECTION_MAX_OUTPUT_TOKENS`` of output;
- each section's output must pass ``case_briefing.validate_draft`` against the
  request's own references with no statement rejected, must contain only that
  section, must not be empty when the template has statements, and must cover
  the references and actions the deterministic template requires for that
  section (every search checked, every unresolved item, the same next actions);
- a section that fails falls back to the template's section on its own, and the
  fallback is recorded per section in the merged provenance (``sections`` and
  ``section_fallbacks``);
- a section whose template is empty is not sent to a model (no call is spent).

The graph still validates the merged draft against the verified search, so these
checks only decide which level's text each section uses.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from ai.models.router import LEVELS, ROUTER_VERSION, CompletionTask, ModelOutput

# The prompt version is case_briefing.PROMPT_VERSION (recorded on every briefing).
# Per-call output budget for one section (reasoning tokens included). Sized so a
# section call stays well inside Groq's 8,000 tokens/min with its input.
SECTION_MAX_OUTPUT_TOKENS = 1536
MAX_MODEL_STATEMENTS = 10

STATEMENT_SCHEMA = (
    'A statement is {"kind": <kind>, "text": <one plain sentence, at most 300 characters>, '
    '"refs": [{"citation": <citation_id>} | {"search": <search_id>} | {"document": '
    "<evidence_id>}]}. Cite only IDs present in this request."
)
SECTION_GUIDANCE = {
    "what_happened": (
        "Observed facts that the cited evidence shows. Kind observed_fact. Each statement "
        'cites one or more evidence citations ({"citation": citation_id}) and nothing else.'
    ),
    "what_was_checked": (
        "One check statement for every item in required: say which topic was searched and "
        'how many cited items were found. Kind check. Each cites {"search": search_id} and '
        "may also cite that search's citation_ids."
    ),
    "what_is_explained": (
        "For each search that found evidence, what the cited evidence addresses. Kind "
        'explained. Each statement cites evidence citations ({"citation": citation_id}) only.'
    ),
    "what_is_unresolved": (
        "One statement for every item in required, with the kind and refs given there: a "
        "search with no evidence is missing_information (missing information, never a "
        "discrepancy, and it cites only the search); an unsearchable document is "
        "unsearchable; a flagged document is untrusted_content and is described without "
        "repeating any of its text."
    ),
    "next_action": (
        "Exactly the proposals in required, in that order, each with the given action and "
        'refs. Kind proposed_action with "action". These are proposals for an officer.'
    ),
}
# Sections whose template references must all be covered by a model draft.
COVERED_REF_TYPES: dict[str, frozenset[str]] = {
    "what_was_checked": frozenset({"search"}),
    "what_is_unresolved": frozenset({"search", "document", "citation"}),
    "next_action": frozenset({"search", "document", "citation"}),
}


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


def briefing_validator(request: Mapping[str, Any]) -> Callable[[object], str | None]:
    """Whole-draft check: ``None`` when ``validate_draft`` rejects nothing."""
    from ai.workflows.case_briefing import validate_draft

    scope = _request_scope(request)

    def validate(draft: object) -> str | None:
        _, rejected = validate_draft(draft, scope)
        if not rejected:
            return None
        return f"rejected:{rejected[0]['reason']}"

    return validate


# --- one section ------------------------------------------------------------------------


def _ref_keys(statement: Mapping[str, Any], types: frozenset[str]) -> set[tuple[str, str]]:
    keys = set()
    for ref in statement.get("refs") or []:
        if isinstance(ref, Mapping) and len(ref) == 1:
            ((ref_type, ref_id),) = ref.items()
            if ref_type in types:
                keys.add((str(ref_type), str(ref_id)))
    return keys


def _required(section: str, template: list[dict[str, object]]) -> list[dict[str, object]]:
    """What a model draft of ``section`` must cover, from the deterministic template."""
    types = COVERED_REF_TYPES.get(section)
    if not types:
        return []
    required = []
    for statement in template:
        item: dict[str, object] = {
            "kind": statement["kind"],
            "refs": [
                {ref_type: ref_id} for ref_type, ref_id in sorted(_ref_keys(statement, types))
            ],
        }
        if section == "next_action":
            item["action"] = statement.get("action")
        required.append(item)
    return required


def _coverage_reason(
    section: str, statements: list[Mapping[str, Any]], template: list[dict[str, object]]
) -> str | None:
    types = COVERED_REF_TYPES.get(section)
    if not types:
        return None
    if section == "next_action":
        if [s.get("action") for s in statements] != [s.get("action") for s in template]:
            return "actions_changed"
        for drafted, expected in zip(statements, template, strict=True):
            if not _ref_keys(expected, types) <= _ref_keys(drafted, types):
                return "required_not_covered"
        return None
    covered = {(str(s.get("kind")), key) for s in statements for key in _ref_keys(s, types)}
    for statement in template:
        kind = str(statement["kind"])
        if not {(kind, key) for key in _ref_keys(statement, types)} <= covered:
            return "required_not_covered"
    return None


def _section_payload(
    request: Mapping[str, Any], section: str, required: list[dict[str, object]]
) -> dict[str, object]:
    """Only what ``section`` needs; flagged document text is never sent (TAX-110)."""
    evidence = [e for e in request.get("untrusted_evidence") or [] if isinstance(e, Mapping)]
    payload: dict[str, object] = {
        "synthetic": True,
        "case_id": request.get("case_id"),
        "section": section,
    }
    if section in ("what_happened", "what_is_explained"):
        payload["untrusted_evidence"] = [dict(e) for e in evidence if not e.get("instruction_like")]
    if section != "what_happened":
        payload["searches"] = [dict(s) for s in request.get("searches") or []]
    if section == "what_is_unresolved":
        payload["unsearchable"] = [dict(u) for u in request.get("unsearchable") or []]
        payload["flagged_evidence"] = [
            {
                "citation_id": e.get("citation_id"),
                "document_type": e.get("document_type"),
                "instruction_like": True,
            }
            for e in evidence
            if e.get("instruction_like")
        ]
    if required:
        payload["required"] = required
    return payload


def section_task(
    request: Mapping[str, Any], section: str, template_sections: Mapping[str, Any]
) -> CompletionTask:
    """One bounded completion for ``section``; the template's section is its fallback."""
    from ai.workflows import case_briefing as cb

    template = cast(list[dict[str, object]], list(template_sections.get(section) or []))
    required = _required(section, template)
    validate_whole = briefing_validator(request)

    def validate(output: object) -> str | None:
        raw = output.get("sections") if isinstance(output, Mapping) else None
        if not isinstance(raw, Mapping) or set(raw) != {section}:
            return "wrong_section"
        items = raw[section]
        if not isinstance(items, list):
            return "invalid_section"
        reason = validate_whole({"sections": {section: items}})
        if reason is not None:
            return reason
        if template and not items:
            return "section_empty"
        return _coverage_reason(section, items, template)

    kinds = ", ".join(sorted(cb.SECTION_KINDS[section]))
    instructions = (
        f"{cb.INSTRUCTIONS} Draft only the section {section} now. {SECTION_GUIDANCE[section]} "
        f'Return {{"sections": {{"{section}": [statement, ...]}}}} with at most '
        f"{max(MAX_MODEL_STATEMENTS, len(template))} statements. Allowed kinds: {kinds}. "
        f"{STATEMENT_SCHEMA}"
    )
    if section == "next_action":
        instructions += (
            ' next_action statements also carry "action": one of '
            + ", ".join(sorted(cb.NEXT_ACTIONS))
            + "."
        )
    return CompletionTask(
        name=f"case_briefing_{section}",
        prompt_version=cb.PROMPT_VERSION,
        instructions=instructions,
        payload=_section_payload(request, section, required),
        template=lambda _payload: {"sections": {section: [dict(s) for s in template]}},
        validate=validate,
        max_output_tokens=SECTION_MAX_OUTPUT_TOKENS,
        template_id=cb.DeterministicBriefingModel.model_id,
    )


# --- the whole briefing -----------------------------------------------------------------


def _skipped(section: str, template_id: str) -> dict[str, object]:
    return {
        "section": section,
        "called": False,
        "skipped": "template_empty",
        "provider": "template",
        "model_id": template_id,
        "fallback_level": LEVELS["template"],
        "fallback_level_name": "template",
        "fell_back": False,
        "attempts": [],
        "latency_ms": 0,
        "tokens": None,
        "validation": {"status": "not_run", "reason": "template_empty"},
    }


def _sum_tokens(records: Sequence[Mapping[str, Any]]) -> dict[str, int | None] | None:
    totals: dict[str, int | None] = {"input": None, "output": None, "total": None}
    seen = False
    for record in records:
        tokens = record.get("tokens")
        if not isinstance(tokens, Mapping):
            continue
        for key in totals:
            value = tokens.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = (totals[key] or 0) + value
                seen = True
    return totals if seen else None


def draft_by_section(
    complete: Callable[[CompletionTask], ModelOutput],
    request: Mapping[str, Any],
    *,
    role: str = "reasoning",
) -> tuple[dict[str, object], dict[str, object]]:
    """The merged draft and a router-shaped provenance with one record per section.

    The merged record's level is the most capable level any section used (so a
    disclosure never hides model-written text); ``fell_back`` is true when any
    section fell back, and ``section_fallbacks`` names those sections.
    """
    from ai.workflows import case_briefing as cb

    template_model = cb.DeterministicBriefingModel()
    typed = cast(cb.BriefingRequest, request)
    template_draft = cast(Mapping[str, Any], template_model.draft(typed))
    template_sections = cast(Mapping[str, Any], template_draft["sections"])
    started = time.monotonic()
    sections: dict[str, list[object]] = {}
    records: dict[str, dict[str, object]] = {}
    attempts: list[dict[str, object]] = []
    rate_limit: Mapping[str, Any] = {}
    for section in cb.SECTIONS:
        template = list(template_sections.get(section) or [])
        if not template:
            sections[section] = []
            records[section] = _skipped(section, template_model.model_id)
            continue
        task = section_task(request, section, template_sections)
        called_provenance: dict[str, Any]
        try:
            result = complete(task)
            output, called_provenance = result.output, dict(result.provenance)
        except Exception:  # a failing port costs this section only
            output = task.template(task.payload)
            called_provenance = {
                "provider": "template",
                "model_id": task.template_id,
                "configured_model_id": task.template_id,
                "settings": {"kind": "template", "temperature": None, "sampling": None},
                "fallback_level": LEVELS["template"],
                "fallback_level_name": "template",
                "fell_back": True,
                "attempts": [
                    {"level": 0, "name": "hosted", "outcome": "provider_error"},
                    {"level": 2, "name": "template", "outcome": "ok"},
                ],
                "latency_ms": 0,
                "tokens": None,
                "validation": {"status": "passed", "reason": None},
            }
        raw = output.get("sections") if isinstance(output, Mapping) else None
        items = raw.get(section) if isinstance(raw, Mapping) else None
        sections[section] = list(items) if isinstance(items, list) else []
        for attempt in called_provenance.get("attempts") or []:
            if isinstance(attempt, Mapping):
                attempts.append({**attempt, "section": section})
        rate_limit = called_provenance.get("rate_limit") or rate_limit
        records[section] = {
            "section": section,
            "called": True,
            **{
                key: called_provenance.get(key)
                for key in (
                    "provider",
                    "model_id",
                    "configured_model_id",
                    "settings",
                    "fallback_level",
                    "fallback_level_name",
                    "fell_back",
                    "attempts",
                    "latency_ms",
                    "tokens",
                    "validation",
                )
            },
        }

    called = [r for r in records.values() if r["called"]]
    primary = min(
        called or list(records.values()),
        key=lambda r: (int(cast(int, r["fallback_level"])), cb.SECTIONS.index(str(r["section"]))),
    )
    failed = [
        r for r in called if cast(Mapping[str, Any], r["validation"]).get("status") == "failed"
    ]
    fallbacks = [str(r["section"]) for r in called if r["fell_back"]]
    provenance: dict[str, object] = {
        "router_version": ROUTER_VERSION,
        "provider": primary["provider"],
        "model_id": primary["model_id"],
        "configured_model_id": primary["configured_model_id"],
        "role": role,
        "task": "case_briefing",
        "prompt_version": cb.PROMPT_VERSION,
        "settings": primary["settings"],
        "fallback_level": primary["fallback_level"],
        "fallback_level_name": primary["fallback_level_name"],
        "fell_back": bool(fallbacks),
        "attempts": attempts,
        "latency_ms": int(round((time.monotonic() - started) * 1000)),
        "tokens": _sum_tokens(called),
        "rate_limit": dict(rate_limit),
        "validation": {
            "status": "failed" if failed else "passed",
            "reason": cast(Mapping[str, Any], failed[0]["validation"]).get("reason")
            if failed
            else None,
        },
        "notes": [],
        "drafting": "by_section",
        "sections": records,
        "section_fallbacks": fallbacks,
    }
    return {"sections": sections}, provenance
