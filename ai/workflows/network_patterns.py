"""Emerging network pattern review (TAX-075): explainable candidates for analysts only.

Reads one tenant's TAX-074 case graphs (``scheme_graph.build_case_graph`` output) and
finds structural patterns that recur across cases:

- ``shared_intermediary``: one party is adviser to / intermediary for parties in at
  least ``MIN_CASES`` distinct cases;
- ``common_beneficial_owner``: one party holds beneficial ownership in parties of at
  least ``MIN_OWNER_CASES`` distinct cases;
- ``multi_case_scheme``: one scheme is linked to at least ``MIN_CASES`` distinct cases;
- ``circular_ownership``: ownership edges inside one case graph form a cycle.

Each pattern is explained by the exact graph edges behind it, with their provenance and
evidence citations. A pattern is *novel* when the same pattern key is absent from the
baseline graphs; novelty is reported but never enough on its own: a candidate is
queued for analyst review only when every supporting case has at least one cited edge,
otherwise it is ``insufficient_evidence`` with its source gaps (uncited edges, unstated
ownership types, missing percentages). Nothing here labels a party, scores risk or
triggers an action (``enforcement_action: None``). ``evaluate_reviews`` measures how
often analysts found queued patterns explained (false positives) and how often source
gaps were present, per pattern type. Thresholds are prototype choices.
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Final

from ai.tools.evidence_search import check_read_access

PATTERN_VERSION: Final = "tax075-network-patterns-v1"
MIN_CASES: Final = 3
MIN_OWNER_CASES: Final = 2
MAX_GRAPHS: Final = 2_000
MAX_EDGES_PER_PATTERN: Final = 100
OWNERSHIP_TYPES: Final = frozenset(
    {"legal_ownership", "beneficial_ownership", "indirect_ownership", "ownership_type_unstated"}
)
REVIEW_OUTCOMES: Final = frozenset(
    {"explained_legitimate", "warranted_further_review", "inconclusive"}
)
NOTICE: Final = (
    "Structural pattern for analyst review only. A recurring relationship or a new pattern "
    "is not evidence of wrongdoing by any party and triggers no action; an analyst decides "
    "whether anything needs looking at."
)


class PatternInputError(ValueError):
    """A graph or review is malformed; carries a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _graphs(raw: Iterable[Mapping[str, object]], tenant_id: str) -> list[Mapping[str, object]]:
    graphs = list(raw)
    if len(graphs) > MAX_GRAPHS:
        raise PatternInputError("graph_budget_exceeded")
    seen: set[str] = set()
    out = []
    for graph in graphs:
        if not isinstance(graph, Mapping) or not isinstance(graph.get("edges"), Mapping):
            raise PatternInputError("invalid_graph")
        if graph.get("tenant_id") != tenant_id:
            raise PatternInputError("tenant_mismatch")
        case_id = graph.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise PatternInputError("invalid_graph")
        if case_id in seen:
            continue  # duplicate delivery
        seen.add(case_id)
        out.append(graph)
    return out


def _edges(graph: Mapping[str, object]) -> list[Mapping[str, object]]:
    edges = graph.get("edges")
    if not isinstance(edges, Mapping):
        return []
    return [e for e in edges.values() if isinstance(e, Mapping)]


def _edge_view(edge: Mapping[str, object], case_id: str) -> dict[str, object]:
    provenance = edge.get("provenance")
    evidence = provenance.get("evidence") if isinstance(provenance, Mapping) else None
    source = provenance.get("source") if isinstance(provenance, Mapping) else None
    return {
        "case_id": case_id,
        "edge_id": edge.get("edge_id"),
        "type": edge.get("type"),
        "source_node": edge.get("source"),
        "target_node": edge.get("target"),
        "percentage": edge.get("percentage"),
        "evidence_status": edge.get("evidence_status"),
        "source_record": source.get("record_id") if isinstance(source, Mapping) else None,
        "evidence": [
            {
                "evidence_id": c.get("evidence_id"),
                "document_sha256": c.get("document_sha256"),
                "locator": c.get("locator"),
            }
            for c in (evidence if isinstance(evidence, list) else [])
            if isinstance(c, Mapping)
        ],
    }


def _find_cycles(edges: list[Mapping[str, object]]) -> list[list[str]]:
    """Ownership cycles (owner -> asset) in one case graph, canonicalised."""
    graph: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        if e.get("type") in OWNERSHIP_TYPES:
            graph[str(e.get("source"))].add(str(e.get("target")))
    cycles: set[tuple[str, ...]] = set()

    def walk(start: str, node: str, path: list[str]) -> None:
        for nxt in sorted(graph.get(node, ())):
            if nxt == start:
                rotate = path.index(min(path))
                cycles.add(tuple(path[rotate:] + path[:rotate]))
            elif nxt not in path and len(path) < 8:
                walk(start, nxt, path + [nxt])

    for start in sorted(graph):
        walk(start, start, [start])
    return [list(c) for c in sorted(cycles)]


def _collect(graphs: list[Mapping[str, object]]) -> dict[tuple[str, str], list[dict[str, object]]]:
    """Pattern key -> supporting edges (with case IDs)."""
    found: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for graph in graphs:
        case_id = str(graph["case_id"])
        edges = _edges(graph)
        for e in edges:
            kind = e.get("type")
            if kind in ("adviser_to", "intermediary_for"):
                found[("shared_intermediary", str(e.get("source")))].append(_edge_view(e, case_id))
            elif kind == "beneficial_ownership":
                found[("common_beneficial_owner", str(e.get("source")))].append(
                    _edge_view(e, case_id)
                )
            elif kind == "case_scheme_link":
                found[("multi_case_scheme", str(e.get("target")))].append(_edge_view(e, case_id))
        for cycle in _find_cycles(edges):
            key = ("circular_ownership", ">".join(cycle))
            cycle_edges = [
                e
                for e in edges
                if e.get("type") in OWNERSHIP_TYPES
                and str(e.get("source")) in cycle
                and str(e.get("target")) in cycle
            ]
            found[key].extend(_edge_view(e, case_id) for e in cycle_edges)
    return found


def _minimum(pattern_type: str) -> int:
    return {
        "shared_intermediary": MIN_CASES,
        "multi_case_scheme": MIN_CASES,
        "common_beneficial_owner": MIN_OWNER_CASES,
        "circular_ownership": 1,
    }[pattern_type]


def _source_gaps(edges: list[dict[str, object]]) -> list[dict[str, object]]:
    gaps = []
    for e in edges:
        if e.get("evidence_status") != "cited":
            gaps.append(
                {
                    "gap": "edge_without_cited_evidence",
                    "edge_id": e["edge_id"],
                    "case_id": e["case_id"],
                }
            )
        if e.get("type") == "ownership_type_unstated":
            gaps.append(
                {"gap": "ownership_type_unstated", "edge_id": e["edge_id"], "case_id": e["case_id"]}
            )
        if e.get("type") in OWNERSHIP_TYPES and e.get("percentage") is None:
            gaps.append(
                {
                    "gap": "ownership_percentage_missing",
                    "edge_id": e["edge_id"],
                    "case_id": e["case_id"],
                }
            )
    return gaps


def detect_patterns(
    graphs: Iterable[Mapping[str, object]],
    *,
    baseline_graphs: Iterable[Mapping[str, object]] = (),
    actor: Mapping[str, object],
    tenant_id: str,
) -> dict[str, object]:
    """Find recurring structural patterns and queue explainable ones for analyst review."""
    check_read_access(actor, tenant_id)
    current = _graphs(graphs, tenant_id)
    baseline = _graphs(baseline_graphs, tenant_id)
    baseline_keys = {
        key
        for key, edges in _collect(baseline).items()
        if len({e["case_id"] for e in edges}) >= _minimum(key[0])
    }
    ranked: list[tuple[int, int, str, dict[str, object]]] = []
    insufficient: list[dict[str, object]] = []
    for key, edges in sorted(_collect(current).items()):
        pattern_type, anchor = key
        cases = sorted({str(e["case_id"]) for e in edges})
        if len(cases) < _minimum(pattern_type):
            continue
        edges = sorted(edges, key=lambda e: (str(e["case_id"]), str(e["edge_id"])))[
            :MAX_EDGES_PER_PATTERN
        ]
        cited_cases = {str(e["case_id"]) for e in edges if e.get("evidence_status") == "cited"}
        gaps = _source_gaps(edges)
        item: dict[str, object] = {
            "pattern_id": "np-"
            + hashlib.sha256(json.dumps([tenant_id, *key]).encode()).hexdigest()[:16],
            "pattern_type": pattern_type,
            "anchor": anchor,
            "case_ids": cases,
            "case_count": len(cases),
            "novel": key not in baseline_keys,
            "supporting_edges": edges,
            "source_gaps": gaps,
            "explanation": f"{pattern_type.replace('_', ' ').capitalize()} observed across "
            f"{len(cases)} case(s); see the supporting edges and their cited evidence.",
            "enforcement_action": None,
            "novelty_alone_sufficient": False,
            "notice": NOTICE,
        }
        if set(cases) <= cited_cases:
            item["status"] = "queued_for_analyst_review"
            item["review"] = {"route": "analyst_review", "decided_by": "analyst", "outcome": None}
            ranked.append((len(gaps), -len(cases), str(item["pattern_id"]), item))
        else:
            item["status"] = "insufficient_evidence"
            item["reason"] = "not_every_case_has_cited_evidence"
            insufficient.append(item)
    # Analysts see the most completely evidenced patterns first; never a risk ordering.
    queue = [item for *_, item in sorted(ranked, key=lambda r: r[:3])]
    return {
        "pattern_version": PATTERN_VERSION,
        "tenant_id": tenant_id,
        "cases_observed": len(current),
        "baseline_cases": len(baseline),
        "review_queue": queue,
        "insufficient_evidence": insufficient,
        "thresholds": {"min_cases": MIN_CASES, "min_owner_cases": MIN_OWNER_CASES},
        "notice": NOTICE,
    }


def evaluate_reviews(
    queue_items: Iterable[Mapping[str, object]], reviews: Mapping[str, str]
) -> dict[str, object]:
    """False-positive and source-gap shares per pattern type from analyst outcomes."""
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "queued": 0,
            "reviewed": 0,
            "explained_legitimate": 0,
            "warranted_further_review": 0,
            "inconclusive": 0,
            "with_source_gaps": 0,
        }
    )
    for item in queue_items:
        kind = str(item.get("pattern_type"))
        row = stats[kind]
        row["queued"] += 1
        gaps = item.get("source_gaps")
        if isinstance(gaps, list) and gaps:
            row["with_source_gaps"] += 1
        outcome = reviews.get(str(item.get("pattern_id")))
        if outcome is None:
            continue
        if outcome not in REVIEW_OUTCOMES:
            raise PatternInputError("invalid_review_outcome")
        row["reviewed"] += 1
        row[outcome] += 1
    return {
        kind: {
            **row,
            "false_positive_share": f"{row['explained_legitimate']}/{row['reviewed']}"
            if row["reviewed"]
            else None,
            "source_gap_share": f"{row['with_source_gaps']}/{row['queued']}",
        }
        for kind, row in sorted(stats.items())
    }
