"""Losses created by intra-group financing (TAX-140, Challenge 2; extends TAX-049).

A loss can come from trading (an operating loss) or from paying interest to a related
party on money the group lent (a financing-driven loss). The two point to different
questions, so this review keeps them apart, period by period, from the taxpayer's own
figures, and tests the ordinary explanations before anything is raised.

Optional record fields read here (absent fields mean the review does not run):

- ``financial_periods``: ``[{"period", "operating_result", "related_party_interest",
  "other_finance_cost", "related_party_debt"?, "equity"?, "evidence_ids"}]``. Amounts are
  decimal strings; ``operating_result`` may be negative (an operating loss).
- ``loss_carry_forward``: ``[{"period", "closing_balance", "evidence_ids"}]``.
- related-party ``contracts`` with ``kind: "loan"``, ``principal`` and
  ``annual_interest_rate`` (and optionally ``purpose`` with ``purpose_evidence_ids``).

For each loss period (operating result less all finance cost below zero):

- ``financing_driven``: there is an operating profit after third-party finance cost, and
  the related-party interest turns it into a loss;
- ``mixed``: there is an operating loss, and the related-party interest is at least half
  of the period's loss;
- ``operating``: the loss comes mainly from trading.

Alternative explanations tested and recorded with figures (``tests``):

1. ``interest_matches_documented_loan_terms``: the related-party interest charged is
   compared with principal x documented rate of the related-party loans. A difference is
   a discrepancy to ask about, not a conclusion.
2. ``third_party_finance_share``: how much of the finance cost is owed to unrelated
   lenders.
3. ``loan_funded_capital_investment``: a related-party loan documented as funding capital
   investment (with evidence) is counter-evidence for a business reason.
4. ``documented_loss_explanation``: the TAX-049 documented explanations (start-up,
   restructuring and so on) with evidence.

Findings (codes the TAX-116 register routes; ``legal_conclusion`` is always ``None``):

- ``financing_driven_losses``: at least ``MIN_FINANCING_LOSS_PERIODS`` loss periods that
  are financing-driven or mixed, and no documented capital-investment or loss explanation
  covers them;
- ``related_party_debt_above_reported_ratio``: related-party debt above the reported 3:1
  debt-to-equity ratio in any period (thin capitalisation; the provision's scope is
  unverified).

The loss carry-forward pattern (a growing carried-forward balance alongside
financing-driven losses, with the related-party interest it contains) is recorded as an
observation on the financing finding. Nothing here prices a loan, imputes interest or
decides deductibility.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from ai.tools.refund_reconciliation import RefundDataError, money

FINANCING_VERSION = "tax140-financing-losses-v1"
MIN_FINANCING_LOSS_PERIODS = 2
REPORTED_DEBT_EQUITY_RATIO = Decimal("3")
INTEREST_TOLERANCE = Decimal("0.01")  # 1% of the expected interest
CENT = Decimal("0.01")
_SIGNED = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,2})?$")


class Review(Protocol):
    known_ids: frozenset[str]
    observations: list[dict[str, object]]
    counter_evidence: list[dict[str, object]]
    gaps: list[dict[str, object]]
    discrepancies: list[dict[str, object]]
    questions: list[dict[str, object]]

    def cite(self, evidence_ids: list[str]) -> list[str]: ...

    def add(
        self, bucket: list[dict[str, object]], code: str, evidence_ids: list[str], **extra: object
    ) -> dict[str, object]: ...


def _amount(value: object, field: str, *, signed: bool = False) -> Decimal:
    if not isinstance(value, str) or not _SIGNED.fullmatch(value):
        raise RefundDataError("invalid_money", f"{field} must be a decimal string")
    amount = Decimal(value).quantize(CENT)
    if amount < 0 and not signed:
        raise RefundDataError("invalid_money", f"{field} must not be negative")
    return amount


def _ids(item: Mapping[str, object], key: str = "evidence_ids") -> list[str]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise RefundDataError("invalid_record", f"{key} must be a list of evidence IDs")
    return list(value)


def _resolved(review: Review, cited: list[str]) -> list[str]:
    return [item for item in cited if item in review.known_ids]


def review_financing_losses(
    review: Review,
    record: Mapping[str, object],
    *,
    return_id: str,
    related_contracts: list[tuple[str, Mapping[str, object]]],
    documented_explanations: int,
    currency: str,
) -> dict[str, Any] | None:
    """Separate financing-driven from operating losses; None when no figures were supplied."""
    raw = record.get("financial_periods")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(p, Mapping) for p in raw):
        raise RefundDataError("invalid_record", "financial_periods must be a list of objects")

    loans = [
        (cid, c)
        for cid, c in related_contracts
        if c.get("kind") == "loan" and c.get("principal") is not None
    ]
    expected_interest = Decimal("0.00")
    loan_ids: list[str] = []
    for cid, loan in loans:
        principal = _amount(loan.get("principal"), f"{cid}.principal")
        rate = Decimal(str(loan.get("annual_interest_rate", "0")))
        expected_interest += (principal * rate).quantize(CENT)
        loan_ids.append(cid)

    periods: list[dict[str, Any]] = []
    for item in sorted(raw, key=lambda p: str(p.get("period"))):
        period = str(item.get("period"))
        cited = review.cite(_ids(item))
        operating = _amount(item.get("operating_result"), f"{period}.operating_result", signed=True)
        rp_interest = _amount(
            item.get("related_party_interest", "0.00"), f"{period}.related_party_interest"
        )
        other = _amount(item.get("other_finance_cost", "0.00"), f"{period}.other_finance_cost")
        net = operating - rp_interest - other
        if net >= 0:
            kind = "profit"
        elif operating - other >= 0:
            kind = "financing_driven"
        elif rp_interest * 2 >= -net:
            kind = "mixed"
        else:
            kind = "operating"
        row: dict[str, Any] = {
            "period": period,
            "classification": kind,
            "operating_result": money(operating, currency),
            "related_party_interest": money(rp_interest, currency),
            "other_finance_cost": money(other, currency),
            "net_result": money(net, currency),
            "evidence_ids": cited,
        }
        debt = item.get("related_party_debt")
        equity = item.get("equity")
        if debt is not None and equity is not None:
            d = _amount(debt, f"{period}.related_party_debt")
            e = _amount(equity, f"{period}.equity", signed=True)
            row["debt_to_equity"] = None if e <= 0 else str((d / e).quantize(Decimal("0.01")))
            row["equity_not_positive"] = e <= 0
        periods.append(row)

    loss_rows = [p for p in periods if p["classification"] != "profit"]
    financing_rows = [p for p in loss_rows if p["classification"] in ("financing_driven", "mixed")]
    period_ids = [e for p in periods for e in p["evidence_ids"]]
    tests: list[dict[str, Any]] = []

    # 1. Interest against the documented loan terms.
    if loans:
        charged = [Decimal(p["related_party_interest"]["amount"]) for p in periods]
        differing = [
            p["period"]
            for p, amount in zip(periods, charged, strict=True)
            if amount and abs(amount - expected_interest) > expected_interest * INTEREST_TOLERANCE
        ]
        tests.append(
            {
                "test": "interest_matches_documented_loan_terms",
                "expected_annual_interest": money(expected_interest, currency),
                "periods_differing": differing,
                "result": "differs" if differing else "consistent",
            }
        )
        if differing:
            review.add(
                review.discrepancies,
                "related_party_interest_differs_from_loan_terms",
                [*loan_ids, *period_ids],
                periods=differing,
                expected_annual_interest=money(expected_interest, currency),
            )
    elif any(Decimal(p["related_party_interest"]["amount"]) > 0 for p in periods):
        tests.append({"test": "interest_matches_documented_loan_terms", "result": "no_loan_terms"})
        review.add(review.gaps, "related_party_loan_terms_not_supplied", [return_id, *period_ids])

    # 2. Third-party finance share.
    rp_total = sum((Decimal(p["related_party_interest"]["amount"]) for p in loss_rows), Decimal(0))
    other_total = sum((Decimal(p["other_finance_cost"]["amount"]) for p in loss_rows), Decimal(0))
    finance_total = rp_total + other_total
    share = None if finance_total == 0 else (other_total / finance_total).quantize(Decimal("0.01"))
    tests.append(
        {
            "test": "third_party_finance_share",
            "third_party_share": None if share is None else str(share),
            "result": "mainly_third_party" if share is not None and share >= Decimal("0.5") else "mainly_related_party",
        }
    )  # fmt: skip

    # 3. Loans documented as funding capital investment.
    capital = []
    for cid, loan in loans:
        if loan.get("purpose") != "capital_investment":
            continue
        evidence = _resolved(review, _ids(loan, "purpose_evidence_ids"))
        if evidence:
            capital.append(
                review.add(
                    review.counter_evidence,
                    "loan_funded_capital_investment",
                    [cid, *evidence],
                    loan=cid,
                )
            )
    tests.append(
        {
            "test": "loan_funded_capital_investment",
            "result": "documented" if capital else "not_documented",
        }
    )
    # 4. Documented loss explanations (counted by the TAX-049 loss review).
    tests.append(
        {
            "test": "documented_loss_explanation",
            "result": "documented" if documented_explanations else "not_documented",
        }
    )

    findings: list[str] = []
    if len(financing_rows) >= MIN_FINANCING_LOSS_PERIODS:
        explained = bool(capital) or documented_explanations > 0
        observation = review.add(
            review.observations,
            "financing_driven_losses",
            [*loan_ids, *[e for p in financing_rows for e in p["evidence_ids"]]],
            periods=[p["period"] for p in financing_rows],
            related_party_interest_in_loss_periods=money(
                sum((Decimal(p["related_party_interest"]["amount"]) for p in financing_rows), Decimal(0)),
                currency,
            ),
            explained=explained,
        )  # fmt: skip
        carry = _carry_forward(review, record, financing_rows, currency)
        if carry:
            observation["carry_forward"] = carry
        if not explained:
            findings.append("financing_driven_losses")
            review.questions.append(
                {
                    "question_id": f"{return_id}:financing_losses",
                    "topic": "financing_losses",
                    "evidence_ids": observation["evidence_ids"],
                    "text": (
                        f"The figures show losses in {len(financing_rows)} periods "
                        f"({financing_rows[0]['period']} to {financing_rows[-1]['period']}) "
                        "where interest paid to a related party is the main cost turning a "
                        "result into a loss. Please explain what the related-party loans "
                        "were used for and provide the loan agreements and board approvals."
                    ),
                    "requested_records": [
                        "loan agreements and amendments",
                        "board or budget papers approving the borrowing",
                        "records of how the loan funds were used",
                    ],
                }
            )
    high = [
        p
        for p in periods
        if p.get("debt_to_equity") and Decimal(p["debt_to_equity"]) > REPORTED_DEBT_EQUITY_RATIO
    ]
    if high:
        review.add(
            review.observations,
            "related_party_debt_above_reported_ratio",
            [*loan_ids, *[e for p in high for e in p["evidence_ids"]]],
            periods=[p["period"] for p in high],
            highest_ratio=max(p["debt_to_equity"] for p in high),
            reported_ratio=f"{REPORTED_DEBT_EQUITY_RATIO}:1",
            scope_note="The ratio's scope (including whether it is limited to mining) is unverified.",
        )  # fmt: skip
        findings.append("related_party_debt_above_reported_ratio")

    return {
        "financing_version": FINANCING_VERSION,
        "periods": periods,
        "loss_periods": {
            "financing_driven": [p["period"] for p in loss_rows if p["classification"] == "financing_driven"],
            "mixed": [p["period"] for p in loss_rows if p["classification"] == "mixed"],
            "operating": [p["period"] for p in loss_rows if p["classification"] == "operating"],
        },
        "alternative_explanations_tested": tests,
        "findings": findings,
        "legal_conclusion": None,
    }  # fmt: skip


def _carry_forward(
    review: Review,
    record: Mapping[str, object],
    financing_rows: list[dict[str, Any]],
    currency: str,
) -> dict[str, Any] | None:
    raw = record.get("loss_carry_forward")
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    rows = sorted(raw, key=lambda r: str(r.get("period")) if isinstance(r, Mapping) else "")
    balances = []
    cited: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise RefundDataError("invalid_record", "loss_carry_forward entries must be objects")
        balances.append(_amount(row.get("closing_balance"), f"{row.get('period')}.closing_balance"))
        cited.extend(_ids(row))
    cited = review.cite(cited)
    growing = (
        all(b2 >= b1 for b1, b2 in zip(balances, balances[1:], strict=False))
        and balances[-1] > balances[0]
    )
    interest = sum(
        (Decimal(p["related_party_interest"]["amount"]) for p in financing_rows), Decimal(0)
    )
    if not growing:
        return None
    return {
        "pattern": "growing_carry_forward",
        "first": {"period": str(rows[0].get("period")), "balance": money(balances[0], currency)},
        "last": {"period": str(rows[-1].get("period")), "balance": money(balances[-1], currency)},
        "related_party_interest_in_financing_loss_periods": money(min(interest, balances[-1]), currency),
        "evidence_ids": cited,
    }  # fmt: skip
