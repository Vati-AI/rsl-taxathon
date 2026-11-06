"""The assurance system run for one taxpayer (TAX-118 harness over TAX-111-116).

``assess_taxpayer`` is what the benchmark measures. It receives only what the pilot would
have: RSL source records through the TAX-111 fixture adapter and the taxpayer's own
submission. It never receives the answer key. It runs, in order:

1. TAX-113 recompute of the declared VAT position from the SAF-T-style books;
2. TAX-115 three-way imported-services match (bank file parsed by the TAX-079
   ``ForeignPaymentAdapter``);
3. BP6 related-party triggers and the TAX-051 work-arrangement fact structuring
   (existing tool, unchanged), with facts mapped from agreements and permit days;
4. TAX-116 rule-register routing of every finding code;
5. per refund claim, the TAX-112 IDMS/ASYCUDA line match and the TAX-114 lane.

Outputs are proposals for officers; nothing is decided, paid, denied or assessed.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for extra in ("integration/payment_channels", "integration/followthemoney"):
    if str(ROOT / extra) not in sys.path:
        sys.path.insert(0, str(ROOT / extra))

from channel_adapters import ForeignPaymentAdapter  # noqa: E402

from ai.tools.foreign_payments import three_way_match  # noqa: E402
from ai.tools.idms_match import match_claim_lines, transpositions  # noqa: E402
from ai.tools.refund_triage import load_policy, triage_claim  # noqa: E402
from ai.tools.related_party_signals import related_party_signals  # noqa: E402
from ai.tools.source_recompute import account_category, match_lines_to_books, recompute  # noqa: E402
from ai.tools.work_arrangement import review_work_arrangement  # noqa: E402
from ai.workflows.rule_register import RuleRegister, load_rule_register  # noqa: E402
from integration.rsl_sources.fixture_source import FixtureRslSources  # noqa: E402

PIPELINE_VERSION = "tax118-pipeline-v1"
CONTINUOUS_PRESENCE_DAYS = 183
AUTHENTICITY_QUESTIONS = frozenset({"provide_invoice_absent_from_idms"})

SOURCE_KINDS = (
    ("etax", "vat_return"),
    ("etax", "wht_return"),
    ("etax", "cit_return"),
    ("etax", "paye_roster"),
    ("idms", "invoice"),
    ("idms", "supplier_integration"),
    ("online_refunds", "claim"),
    ("rsl_fraud_model", "score"),
    ("asycuda", "entry"),
    ("registry", "entity"),
    ("central_bank", "cross_border_payment"),
)


def build_sources(world: Mapping[str, Any], unavailable: Iterable[str] = ()) -> FixtureRslSources:
    records = {
        (system, kind): world["records"].get(f"{system}.{kind}", [])
        for system, kind in SOURCE_KINDS
    }
    return FixtureRslSources(
        str(world["tenant_id"]),
        records,
        retrieved_at=str(world["retrieved_at"]),
        unavailable=unavailable,
    )


def _work_record(tin: str, sub: Mapping[str, Any]) -> dict[str, Any] | None:
    agreements = [a for a in sub.get("agreements", []) if a.get("kind") == "consultancy"]
    if not agreements:
        return None
    permits = {p["person_ref"]: p for p in sub.get("permits", [])}
    contracts, invoices, documents, facts = [], [], [], []
    for a in agreements:
        doc_id = str(a["document_id"])
        person = str(a.get("person_ref"))
        contracts.append(
            {
                "id": doc_id,
                "counterparty_id": a.get("counterparty_ref"),
                "classification": "independent_consultancy",
            }
        )
        documents.append({"id": doc_id, "kind": "agreement"})
        for factor in ("duration_and_continuity", "exclusivity"):
            value = a.get("stated_terms", {}).get(factor)
            if value:
                facts.append(
                    {
                        "contract_id": doc_id,
                        "factor": factor,
                        "value": value,
                        "source": "contract",
                        "evidence_ids": [doc_id],
                    }
                )
        permit = permits.get(person)
        if permit is not None:
            permit_id = f"PERMIT-{person}-{permit['year']}"
            documents.append({"id": permit_id, "kind": "permit_record"})
            presence = (
                "continuous_presence_over_183_days"
                if int(permit["days_present"]) >= CONTINUOUS_PRESENCE_DAYS
                else "fixed_term_project"
            )
            facts.append(
                {
                    "contract_id": doc_id,
                    "factor": "duration_and_continuity",
                    "value": presence,
                    "source": "work_evidence",
                    "evidence_ids": [permit_id],
                }
            )
        for row in sub["saft"]["supplier_invoices"]:
            if row.get("person_ref") == person:
                invoices.append(
                    {"id": row["entry_id"], "contract_id": doc_id, "net_amount": row["net"]}
                )
    return {
        "case_id": tin,
        "return": {"id": f"WA-{tin}"},
        "contracts": contracts,
        "invoices": invoices,
        "payments": [],
        "documents": documents,
        "work_facts": facts,
    }


def _bank_payments(csv_text: str | None) -> list[dict[str, Any]]:
    if not csv_text:
        return []
    out = []
    for tx in ForeignPaymentAdapter().parse(csv_text.encode()):
        conversion = tx.details.get("conversion") or {}
        settled = (conversion.get("settled") or {}).get("amount") if conversion else None
        out.append(
            {
                "provider_reference": tx.provider_reference,
                "date": tx.date,
                "direction": tx.direction,
                "settled_amount": settled if settled is not None else str(tx.amount),
                "counterparty_account_ref": tx.counterparty.account_ref,
                "counterparty_country": tx.counterparty.country,
            }
        )
    return out


def assess_taxpayer(
    world: Mapping[str, Any],
    sources: FixtureRslSources,
    tin: str,
    *,
    use_score: bool = True,
    policy: Mapping[str, Any] | None = None,
    register: RuleRegister | None = None,
) -> dict[str, Any]:
    """Run the system for one taxpayer; returns proposals, flags and timings."""
    started = time.perf_counter()
    tenant = str(world["tenant_id"])
    pol = policy or load_policy()
    reg = register or load_rule_register()
    rates = {k: Decimal(v) for k, v in world["rates_assumed"].items()}
    sub = world["submissions"][tin]

    def read(system: str, kind: str) -> dict[str, Any]:
        return sources.read(system, kind, tenant_id=tenant, taxpayer=tin)

    returns = read("etax", "vat_return")["records"]
    claims = read("online_refunds", "claim")["records"]
    idms = read("idms", "invoice")
    scores_env = read("rsl_fraud_model", "score")
    scores = {s["claim_id"]: s for s in scores_env["records"]}
    asycuda = read("asycuda", "entry")["records"]
    wht = [
        dict(line, period=r["period"])
        for r in read("etax", "wht_return")["records"]
        for line in r["lines"]
    ]
    cit = read("etax", "cit_return")["records"]
    cb = read("central_bank", "cross_border_payment")

    # 1. Recompute (TAX-113).
    rec = recompute(sub["saft"], returns, vat_rate=rates["vat"])

    # 2. Three-way imported-services match (TAX-115).
    ledger_rows = [
        dict(row, category=account_category(str(row.get("account", ""))))
        for row in sub["saft"]["supplier_invoices"]
    ]
    three = three_way_match(
        tin=tin,
        suppliers=sub["saft"].get("suppliers", []),
        supplier_invoices=ledger_rows,
        bank_payments=_bank_payments(sub.get("foreign_payments_csv")),
        wht_lines=wht,
        vat_returns=returns,
        residence_certificates=sub.get("residence_certificates", []),
        central_bank_payments=cb["records"] if cb["status"] == "ok" else None,
        rates={"vat": rates["vat"], "wht": rates["wht"], "wht_treaty": rates["wht_treaty"]},
        materiality=Decimal(world["materiality"]["absolute"]),
        group_by="window",
    )

    # 3. Related-party triggers and work arrangements.
    rp = related_party_signals(
        cit_returns=cit,
        agreements=sub.get("agreements", []),
        loss_explanations=sub.get("loss_explanations", []),
    )
    work_record = _work_record(tin, sub)
    work: dict[str, Any] = (
        review_work_arrangement(work_record) if work_record else {"referrals": []}
    )

    # 5. Claims: line match and lane.
    domestic_refs = {
        str(line["supplier_ref"])
        for c in claims
        for line in c["lines"]
        if line["kind"] == "domestic"
    }
    candidates = set(domestic_refs)
    for ref in domestic_refs:
        candidates |= transpositions(ref)
    registry = sources.lookup(
        "registry", "entity", tenant_id=tenant, field="tin", values=candidates
    )
    integration = sources.lookup(
        "idms", "supplier_integration", tenant_id=tenant, field="supplier_tin", values=candidates
    )
    registered = set(registry["matches"])
    integrated = {t for t, r in integration["matches"].items() if r.get("integrated") is True}

    taxpayer_flags = [{"code": f["code"]} for f in three["flags"]] + [
        {"code": f["code"]}
        for f in rp["findings"]
        if f["code"] == "related_party_fees_with_persistent_losses"
    ]
    claim_results = []
    authenticity = False
    for claim in claims:
        match = match_claim_lines(
            claim,
            idms_invoices=idms["records"],
            idms_available=idms["status"] == "ok" and integration["status"] == "ok",
            integrated_tins=integrated,
            registered_tins=registered,
            asycuda_entries=asycuda,
            documents=sub.get("documents", {}),
        )
        # Import lines are evidenced by ASYCUDA (TAX-112); the books check covers domestic lines.
        books = match_lines_to_books(
            [line for line in claim["lines"] if line["kind"] == "domestic"], sub["saft"]
        )
        score = scores.get(claim["claim_id"]) if use_score else None
        lane = triage_claim(
            claim,
            line_match=match,
            recompute=rec,
            rsl_score=score if score is not None else {"status": "unavailable"},
            books_match=books,
            taxpayer_flags=taxpayer_flags,
            group_rating=None,
            policy=pol,
        )
        claim_auth = any(
            "supplier_not_registered" in line["notes"]
            or AUTHENTICITY_QUESTIONS & set(line["questions"])
            for line in match["lines"]
        ) or any(m["in_books"] is False for m in books.values())
        # The taxpayer-level flag needs hard evidence; an RSL score can change the lane only.
        authenticity = authenticity or claim_auth
        claim_results.append(
            {
                "claim_id": claim["claim_id"],
                "period": claim["period"],
                "lane": lane,
                "match": match,
                "authenticity_indicator": claim_auth,
            }
        )
    recompute_excess = any(
        f["code"] == "declared_input_vat_exceeds_recomputed" for f in rec["findings"]
    )

    # 4. Findings and routes.
    findings: list[dict[str, Any]] = []
    if authenticity or recompute_excess:
        findings.append({"code": "input_vat_unsupported", "issue": "fake_invoices"})
    for f in three["flags"]:
        findings.append(
            {"code": f["code"], "issue": "untaxed_imported_services", "reason": f["reason"]}
        )
    for f in rp["findings"]:
        issue = (
            "policy_gap"
            if f["code"] == "related_party_interest_free_loan"
            else "related_party_loss"
        )
        findings.append({"code": f["code"], "issue": issue, "reason": f["reason"]})
    if work["referrals"]:
        findings.append(
            {
                "code": "work_arrangement_referred_to_specialist",
                "issue": "disguised_employment",
                "contracts": [r["contract_id"] for r in work["referrals"]],
            }
        )
    for f in findings:
        f["route"] = reg.route_for(f["code"])
    flags = sorted({f["issue"] for f in findings})
    flagged_amounts = {
        "fake_invoices": str(
            sum(
                (
                    Decimal(c["lane"]["disputed_amount"]["amount"])
                    for c in claim_results
                    if c["authenticity_indicator"]
                ),
                Decimal("0.00"),
            )
        ),
        "untaxed_imported_services": three["total_gap"]["amount"],
    }
    elapsed = time.perf_counter() - started
    return {
        "tin": tin,
        "pipeline_version": PIPELINE_VERSION,
        "flags": flags,
        "findings": findings,
        "claims": claim_results,
        "recompute": rec,
        "three_way": three,
        "related_party": rp,
        "work_arrangement": work,
        "flagged_amounts": flagged_amounts,
        "inputs_status": {
            "idms": idms["status"],
            "rsl_fraud_model": scores_env["status"],
            "use_score": use_score,
        },
        "seconds": round(elapsed, 4),
        "decision": None,
    }
