"""Explainable taxpayer baselines and peer-group comparisons (TAX-067).

Compares one taxpayer-period with (a) the taxpayer's own earlier periods and (b) a
peer group of other taxpayers in the same sector, size band and period. Every metric
is a Decimal ratio of filed amounts; statistics are robust (median and interquartile
range, nearest-rank quantiles) and every comparison carries a plain-language
explanation, the sample size and the data-quality notes behind it.

Guards turn weak evidence into an explicit ``insufficient_evidence`` state:

- too few prior periods (``MIN_HISTORY_PERIODS``) or peers (``MIN_PEERS``);
- data quality: records that are incomplete, amended-and-unreconciled or from an
  unverified source are excluded, and if more than ``MAX_EXCLUDED_SHARE`` of the
  candidate sample is excluded the comparison is not made (the remainder may be biased);
- skew: if one region supplies more than ``MAX_SINGLE_SOURCE_SHARE`` of peers, the peer
  group is not representative (a single authoritative data source is not skew; source
  quality is handled by the data-quality guard above);
- a zero denominator makes the ratio undefined rather than zero.

A value outside the typical range is described as ``outside_typical_range`` together
with ordinary alternative explanations. It is never a risk score, a fraud indicator or
a reason for action; an officer decides what, if anything, to look at. Only aggregate
statistics are returned (never peer taxpayer identifiers), and the minimum peer count
also limits re-identification. Thresholds are prototype choices documented here.
"""

import re
from collections.abc import Iterable, Mapping
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Final

from ai.tools.evidence_search import check_read_access

BASELINE_VERSION: Final = "tax067-baselines-v1"
MIN_HISTORY_PERIODS: Final = 4
MIN_PEERS: Final = 10
MAX_EXCLUDED_SHARE: Final = Decimal("0.20")
MAX_SINGLE_SOURCE_SHARE: Final = Decimal("0.80")
IQR_FENCE: Final = Decimal("1.5")
RATIO_QUANTUM: Final = Decimal("0.0001")
MAX_RECORDS: Final = 100_000
# metric -> (numerator field, denominator field, description)
METRICS: Final[dict[str, tuple[str, str, str]]] = {
    "refund_to_turnover": ("refund_claim", "turnover", "refund claimed per unit of turnover"),
    "input_vat_to_turnover": (
        "input_vat_claimed",
        "turnover",
        "input VAT claimed per unit of turnover",
    ),
}
ALTERNATIVE_EXPLANATIONS: Final = (
    "start-up, expansion or capital investment in the period",
    "seasonal or one-off trading",
    "export or zero-rated sales that legitimately produce refunds",
    "a business model that differs from typical peers in the same sector code",
)
NOTICE: Final = (
    "Descriptive comparison for officer context only. Being outside the typical range is "
    "not a risk score, a fraud indicator or a finding against the taxpayer."
)
_PERIOD = re.compile(r"([0-9]{4})-Q([1-4])")
_MONEY = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,2})?")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,63}")


class BaselineInputError(ValueError):
    """A record is malformed; carries a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _period_index(period: object) -> int:
    match = _PERIOD.fullmatch(period) if isinstance(period, str) else None
    if not match:
        raise BaselineInputError("invalid_period")
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def _money(value: object) -> Decimal:
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise BaselineInputError("invalid_money")
    return Decimal(value)


def _quality_issue(record: Mapping[str, object]) -> str | None:
    quality = record.get("data_quality")
    if not isinstance(quality, Mapping):
        return "data_quality_unknown"
    if quality.get("complete") is not True:
        return "incomplete_record"
    if quality.get("amended_unreconciled") is True:
        return "amended_unreconciled"
    if quality.get("source_verified") is not True:
        return "unverified_source"
    return None


def _ratio(record: Mapping[str, object], metric: str) -> Decimal | None:
    numerator, denominator, _ = METRICS[metric]
    amounts = record.get("amounts")
    if not isinstance(amounts, Mapping):
        raise BaselineInputError("invalid_record")
    top = _money(amounts.get(numerator))
    bottom = _money(amounts.get(denominator))
    if bottom == 0:
        return None  # undefined, not zero
    try:
        return (top / bottom).quantize(RATIO_QUANTUM)
    except InvalidOperation as error:  # pragma: no cover - guarded by _MONEY
        raise BaselineInputError("invalid_money") from error


def _quantile(sorted_values: list[Decimal], fraction: Decimal) -> Decimal:
    """Nearest-rank quantile: deterministic and always an observed value."""
    rank = max(1, int((fraction * len(sorted_values)).to_integral_value(rounding=ROUND_CEILING)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _describe(values: list[Decimal], subject: Decimal) -> dict[str, object]:
    ordered = sorted(values)
    q1 = _quantile(ordered, Decimal("0.25"))
    median = _quantile(ordered, Decimal("0.5"))
    q3 = _quantile(ordered, Decimal("0.75"))
    iqr = q3 - q1
    low, high = q1 - IQR_FENCE * iqr, q3 + IQR_FENCE * iqr
    below = sum(1 for v in ordered if v < subject)
    equal = sum(1 for v in ordered if v == subject)
    percentile = ((Decimal(below) + Decimal(equal) / 2) / len(ordered) * 100).quantize(
        Decimal("0.1")
    )
    position = "within_typical_range" if low <= subject <= high else "outside_typical_range"
    return {
        "n": len(ordered),
        "median": str(median),
        "q1": str(q1),
        "q3": str(q3),
        "typical_range": [str(low.quantize(RATIO_QUANTUM)), str(high.quantize(RATIO_QUANTUM))],
        "subject_value": str(subject),
        "percentile_rank": str(percentile),
        "position": position,
        "direction": "above" if subject > median else "below" if subject < median else "at_median",
    }


def _insufficient(reasons: list[str], **extra: object) -> dict[str, object]:
    return {"state": "insufficient_evidence", "reasons": reasons, **extra}


def _guarded_sample(
    candidates: list[Mapping[str, object]], metric: str, minimum: int, kind: str
) -> tuple[list[Decimal], dict[str, object], list[str]]:
    excluded: dict[str, int] = {}
    values: list[Decimal] = []
    for record in candidates:
        issue = _quality_issue(record)
        value = None if issue else _ratio(record, metric)
        if value is None:
            reason = issue or "undefined_ratio"
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        values.append(value)
    total = len(candidates)
    share = Decimal(sum(excluded.values())) / total if total else Decimal(0)
    notes: dict[str, object] = {
        "candidates": total,
        "used": len(values),
        "excluded": dict(sorted(excluded.items())),
    }
    reasons: list[str] = []
    if total and share > MAX_EXCLUDED_SHARE:
        reasons.append("data_quality_exclusions_too_high")
    if len(values) < minimum:
        reasons.append(f"{kind}_sample_too_small")
    return values, notes, reasons


def compare(
    records: Iterable[Mapping[str, object]],
    *,
    actor: Mapping[str, object],
    tenant_id: str,
    taxpayer_id: str,
    period: str,
) -> dict[str, object]:
    """Baseline and peer comparison for one taxpayer-period; aggregates only."""
    check_read_access(actor, tenant_id)
    rows = list(records)
    if len(rows) > MAX_RECORDS:
        raise BaselineInputError("record_budget_exceeded")
    target_index = _period_index(period)
    seen: set[tuple[str, int]] = set()
    clean: list[Mapping[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise BaselineInputError("invalid_record")
        if row.get("tenant_id") != tenant_id:
            raise BaselineInputError("tenant_mismatch")
        tid = row.get("taxpayer_id")
        if not isinstance(tid, str) or not _CODE.fullmatch(tid):
            raise BaselineInputError("invalid_taxpayer")
        key = (tid, _period_index(row.get("period")))
        if key in seen:
            raise BaselineInputError("duplicate_taxpayer_period")
        seen.add(key)
        clean.append(row)

    subject = next(
        (
            r
            for r in clean
            if r["taxpayer_id"] == taxpayer_id and _period_index(r["period"]) == target_index
        ),
        None,
    )
    if subject is None:
        raise BaselineInputError("subject_not_found")
    subject_issue = _quality_issue(subject)
    sector, size_band = subject.get("sector"), subject.get("size_band")

    history = [
        r
        for r in clean
        if r["taxpayer_id"] == taxpayer_id and _period_index(r["period"]) < target_index
    ]
    peers = [
        r
        for r in clean
        if r["taxpayer_id"] != taxpayer_id
        and _period_index(r["period"]) == target_index
        and r.get("sector") == sector
        and r.get("size_band") == size_band
    ]

    comparisons: dict[str, object] = {}
    for metric, (_, _, description) in METRICS.items():
        subject_value = None if subject_issue else _ratio(subject, metric)
        if subject_issue or subject_value is None:
            reason = subject_issue or "undefined_ratio"
            comparisons[metric] = {
                "description": description,
                "baseline": _insufficient([f"subject_{reason}"]),
                "peer_group": _insufficient([f"subject_{reason}"]),
            }
            continue
        own_values, own_notes, own_reasons = _guarded_sample(
            history, metric, MIN_HISTORY_PERIODS, "history"
        )
        if own_reasons:
            baseline: dict[str, object] = _insufficient(own_reasons, data_quality=own_notes)
        else:
            stats = _describe(own_values, subject_value)
            baseline = {
                "state": "compared",
                **stats,
                "data_quality": own_notes,
                "explanation": f"{description.capitalize()} is {str(stats['direction']).replace('_', ' ')} "
                f"the taxpayer's own median over {stats['n']} earlier periods "
                f"({str(stats['position']).replace('_', ' ')}).",
            }
        peer_values, peer_notes, reasons = _guarded_sample(peers, metric, MIN_PEERS, "peer")
        for field in ("region",):
            counts: dict[str, int] = {}
            for record in peers:
                counts[str(record.get(field))] = counts.get(str(record.get(field)), 0) + 1
            if peers and Decimal(max(counts.values())) / len(peers) > MAX_SINGLE_SOURCE_SHARE:
                reasons.append(f"peer_group_skewed_by_{field}")
        if reasons:
            peer: dict[str, object] = _insufficient(reasons, data_quality=peer_notes)
        else:
            stats = _describe(peer_values, subject_value)
            peer = {
                "state": "compared",
                **stats,
                "data_quality": peer_notes,
                "group": {"sector": sector, "size_band": size_band, "period": period},
                "explanation": f"{description.capitalize()} is {str(stats['direction']).replace('_', ' ')} "
                f"the median of {stats['n']} peers in the same sector and size band "
                f"(percentile {stats['percentile_rank']}; {str(stats['position']).replace('_', ' ')}).",
            }
        entry: dict[str, object] = {
            "description": description,
            "baseline": baseline,
            "peer_group": peer,
        }
        if "outside_typical_range" in (baseline.get("position"), peer.get("position")):
            entry["alternative_explanations"] = list(ALTERNATIVE_EXPLANATIONS)
        comparisons[metric] = entry

    return {
        "baseline_version": BASELINE_VERSION,
        "tenant_id": tenant_id,
        "taxpayer_id": taxpayer_id,
        "period": period,
        "comparisons": comparisons,
        "thresholds": {
            "min_history_periods": MIN_HISTORY_PERIODS,
            "min_peers": MIN_PEERS,
            "max_excluded_share": str(MAX_EXCLUDED_SHARE),
            "max_single_source_share": str(MAX_SINGLE_SOURCE_SHARE),
            "iqr_fence": str(IQR_FENCE),
        },
        "risk_score": None,
        "fraud_conclusion": None,
        "notice": NOTICE,
    }
