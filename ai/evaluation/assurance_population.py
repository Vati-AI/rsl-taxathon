"""Synthetic Lesotho FMCG taxpayer population with a hidden answer key (TAX-118).

``generate(seed, ...)`` returns two separate objects:

- ``world``: what the system may see. Mock RSL source records (e-Tax, Lekuka/IDMS, the
  Online Refunds Application, RSL fraud-model scores, ASYCUDA World, the registry, Central
  Bank cross-border data) and each taxpayer's own submissions (SAF-T-style books, foreign
  payment bank file, agreements, extracted invoice copies, loss explanations).
- ``answer_key``: what was planted. Issues per taxpayer, amounts at risk, the
  policy-correct lane of each refund claim and the expected legal route. The system
  pipeline never receives it.

Everything is invented. TINs are random 9-digit strings; names are generated; rates are
**synthetic assumptions** (VAT 15%, WHT 10%, treaty WHT 5%), not statements of Lesotho
law. The same seed and parameters always produce the same population: every random
choice comes from a named stream seeded by ``(seed, stream)``, and Lekuka integration uses
its own stream so changing coverage changes only which suppliers are integrated.

Planted issue types (taxpayer level): ``fake_invoices``, ``untaxed_imported_services``,
``disguised_employment``, ``related_party_loss``, ``policy_gap``; ``mixed`` taxpayers carry
two of them. Clean taxpayers include legitimate look-alikes (structural refunds, declared
foreign services, treaty relief, genuine consultants, explained start-up losses,
market-rate loans). Noise: TIN typing slips, late-claimed invoices, DD/MM/YYYY dates,
exchange-rate rounding, missing invoice copies, duplicate schedule lines, supplier
non-declaration, name variations and CMA payments absent from Central Bank data.

TAX-141 adds an opt-in ``central_bank_purpose`` parameter: Central Bank cross-border
reports then carry their balance-of-payments purpose (``services`` or ``income``). Without
it the reports are unchanged.

TAX-139 adds an optional sixth issue, ``persistent_credit``, enabled only when the
``persistent_credit`` parameter is passed (``{"planted": n, "capex_lookalikes": m}``):
planted taxpayers are non-exporters that under-declare standard-rated sales in their
returns and books from some month on, so input VAT exceeds output VAT period after period
with no structural reason; capital-spending look-alikes are clean non-exporters with a
long equipment programme booked to asset accounts. Both draw from their own streams, so
without the parameter every population, including the sealed TAX-118 seed, is unchanged.
"""

from __future__ import annotations

import csv
import io
import random
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

GENERATOR_VERSION = "tax118-population-v1"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
TENANT = "SYN-TENANT-RSL"

DEFAULT_PARAMS: dict[str, Any] = {
    "n_taxpayers": 300,
    "n_dev": 100,
    "first_period": "2024-11",
    "months": 24,
    "clean_share": "0.82",
    "issue_counts": {
        "fake_invoices": 10,
        "untaxed_imported_services": 10,
        "disguised_employment": 9,
        "related_party_loss": 9,
        "policy_gap": 8,
        "mixed": 8,
    },
    "vat_rate": "0.15",
    "wht_rate": "0.10",
    "wht_treaty_rate": "0.05",
    "lekuka_coverage": "0.15",
    "noise": {
        "tin_typo_per_line": "0.03",
        "late_claim_per_invoice": "0.06",
        "missing_copy_per_unintegrated_line": "0.02",
        "duplicate_line_per_claim": "0.02",
        "supplier_gap_per_idms_invoice": "0.01",
        "dmy_dates_per_taxpayer": "0.20",
        "treaty_certificate_missing": "0.15",
    },
    "materiality_absolute": "50000.00",
    "materiality_share": "0.25",
    "structural_min_zero_share": "0.30",
}

ISSUES = (
    "fake_invoices",
    "untaxed_imported_services",
    "disguised_employment",
    "related_party_loss",
    "policy_gap",
)
# TAX-139: planted only when the ``persistent_credit`` parameter is passed; never "mixed".
PERSISTENT_CREDIT = "persistent_credit"
CAPITAL_ACCOUNT = "1600"
SYLLABLES = (
    "Lesa",
    "Maluti",
    "Thaba",
    "Khotso",
    "Pula",
    "Mohale",
    "Senqu",
    "Qacha",
    "Leribe",
    "Mafeteng",
    "Berea",
    "Tsoelike",
    "Moshoe",
    "Katse",
    "Butha",
    "Sefika",
    "Letseng",
)
SUFFIXES = (
    ("Holdings", "Hldgs"),
    ("Distributors", "Distr."),
    ("Trading", "Trdg"),
    ("Wholesale", "W/sale"),
    ("Foods", "Foods"),
    ("Supplies", "Suppl."),
)
FOREIGN = (
    ("za", "ZAR", Decimal("1.000000")),
    ("mu", "USD", Decimal("18.20")),
    ("gb", "GBP", Decimal("23.10")),
)


def _d(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def _s(x: Decimal) -> str:
    return str(_d(x))


class _Streams:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._cache: dict[str, random.Random] = {}

    def __call__(self, name: str) -> random.Random:
        if name not in self._cache:
            self._cache[name] = random.Random(f"{self.seed}:{name}")
        return self._cache[name]


def _periods(first: str, months: int) -> list[str]:
    y, m = int(first[:4]), int(first[5:])
    out = []
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _next(period: str) -> str:
    return _periods(period, 2)[1]


def _tin(rng: random.Random, used: set[str]) -> str:
    while True:
        tin = str(rng.randint(100000000, 999999999))
        if tin not in used and not any(t in used for t in _swaps(tin)):
            used.add(tin)
            return tin


def _swaps(tin: str) -> set[str]:
    return {
        tin[:i] + tin[i + 1] + tin[i] + tin[i + 2 :]
        for i in range(len(tin) - 1)
        if tin[i] != tin[i + 1]
    }


def _name(rng: random.Random) -> tuple[str, str]:
    base = rng.choice(SYLLABLES) + " " + rng.choice(SYLLABLES)
    full, short = rng.choice(SUFFIXES)
    return f"{base} {full}", f"{base.upper()} {short}"


def _date(period: str, day: int) -> str:
    return f"{period}-{min(max(day, 1), 28):02d}"


def generate(seed: int, params: dict[str, Any] | None = None) -> tuple[dict, dict]:
    """Build the population. Returns ``(world, answer_key)``."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    p["noise"] = {**DEFAULT_PARAMS["noise"], **(params or {}).get("noise", {})}
    rs = _Streams(seed)
    vat = Decimal(p["vat_rate"])
    wht_rate = Decimal(p["wht_rate"])
    treaty_rate = Decimal(p["wht_treaty_rate"])
    coverage = Decimal(p["lekuka_coverage"])
    noise = {k: Decimal(v) for k, v in p["noise"].items()}
    periods = _periods(p["first_period"], int(p["months"]))
    window = set(periods)
    used: set[str] = set()

    records: dict[str, list[dict[str, Any]]] = {
        k: []
        for k in (
            "etax.vat_return",
            "etax.wht_return",
            "etax.cit_return",
            "etax.paye_roster",
            "idms.invoice",
            "online_refunds.claim",
            "rsl_fraud_model.score",
            "asycuda.entry",
            "registry.entity",
            "central_bank.cross_border_payment",
            "idms.supplier_integration",
        )
    }
    submissions: dict[str, dict[str, Any]] = {}
    key: dict[str, Any] = {}

    # Assign issues.
    n = int(p["n_taxpayers"])
    alloc = rs("alloc")
    slots: list[list[str]] = []
    for issue, count in p["issue_counts"].items():
        for _ in range(int(count)):
            if issue == "mixed":
                slots.append(sorted(alloc.sample(ISSUES, 2)))
            else:
                slots.append([issue])
    slots += [[] for _ in range(n - len(slots))]
    alloc.shuffle(slots)
    # TAX-139 (optional): pick planted persistent-credit taxpayers and capital-spending
    # look-alikes among the clean slots, on a stream of their own.
    pc_params = p.get("persistent_credit")
    pc_planted: set[int] = set()
    pc_capex: set[int] = set()
    if pc_params:
        clean_slots = [i for i, s in enumerate(slots) if not s]
        n_planted = int(pc_params.get("planted", 0))
        n_capex = int(pc_params.get("capex_lookalikes", 0))
        chosen = rs("pc:alloc").sample(clean_slots, n_planted + n_capex)
        pc_planted, pc_capex = set(chosen[:n_planted]), set(chosen[n_planted:])
        for i in pc_planted:
            slots[i] = [PERSISTENT_CREDIT]

    # Shared supplier pool.
    pool_rng = rs("suppliers")
    suppliers: list[dict[str, Any]] = []
    for i in range(400):
        tin = _tin(pool_rng, used)
        full, short = _name(pool_rng)
        suppliers.append({"tin": tin, "name": full, "short": short})
        records["registry.entity"].append(
            {
                "entity_id": f"ENT-{tin}",
                "tin": tin,
                "country": "ls",
                "kind": "company",
                "vat_registered": True,
            }
        )
    lek = rs("lekuka")
    integrated = {s["tin"] for s in suppliers if Decimal(str(lek.random())) < coverage}
    for s in suppliers:
        records["idms.supplier_integration"].append(
            {"supplier_tin": s["tin"], "integrated": s["tin"] in integrated}
        )

    split_rng = rs("split")
    order = list(range(n))
    split_rng.shuffle(order)
    dev_index = set(order[: int(p["n_dev"])])

    for t in range(n):
        r = rs(f"tp:{t}")
        tin = _tin(r, used)
        full, short = _name(r)
        issues = slots[t]
        group_id = f"GRP-{t:03d}"
        records["registry.entity"].append(
            {
                "entity_id": f"ENT-{tin}",
                "tin": tin,
                "country": "ls",
                "kind": "company",
                "vat_registered": True,
                "group_id": group_id,
            }
        )
        refund_prone = r.random() < 0.70 or "fake_invoices" in issues
        zero_share = Decimal(
            str(round(r.uniform(0.35, 0.85) if refund_prone else r.uniform(0.0, 0.15), 4))
        )
        base_sales = Decimal(str(round(r.lognormvariate(14.6, 0.6), 2)))  # ~M2.2m median
        my_suppliers = r.sample(suppliers, r.randint(8, 18))
        imports = r.random() < 0.30
        dmy = Decimal(str(r.random())) < noise["dmy_dates_per_taxpayer"]
        counterparts: list[str] = []
        pc_r = rs(f"pc:{t}") if t in pc_planted | pc_capex else None
        if pc_r is not None:
            # Non-exporters: whatever credit they show has no zero-rated explanation.
            refund_prone = False
            zero_share = Decimal(str(round(pc_r.uniform(0.0, 0.12), 4)))
            if t in pc_capex:
                counterparts.append("capex_lookalike")
        if refund_prone and not issues:
            counterparts.append("structural_refund")

        parent_country, parent_ccy, parent_rate = FOREIGN[r.choice((0, 0, 0, 1, 2))]
        parent_ref = f"F-{parent_country.upper()}-PARENT-{t:03d}"
        parent_account = f"SYN-{parent_country.upper()}-ACC-{t:03d}"
        parent_name = _name(r)
        records["registry.entity"].append(
            {
                "entity_id": parent_ref,
                "country": parent_country,
                "kind": "company",
                "vat_registered": False,
                "group_id": group_id,
            }
        )
        sub: dict[str, Any] = {
            "tin": tin,
            "saft": {
                "header": {
                    "tin": tin,
                    "company_name": full,
                    "currency": "LSL",
                    "period_start": periods[0],
                    "period_end": periods[-1],
                },
                "suppliers": [],
                "sales_summary": [],
                "supplier_invoices": [],
                "journals": [],
            },
            "foreign_payments_csv": None,
            "agreements": [],
            "documents": {},
            "residence_certificates": [],
            "loss_explanations": [],
        }
        saft = sub["saft"]
        tk: dict[str, Any] = {
            "issues": issues,
            "counterparts": counterparts,
            "split": "dev" if t in dev_index else "test",
            "amount_at_risk": {},
            "claims": {},
            "expected_routes": {},
        }
        for s in my_suppliers:
            saft["suppliers"].append(
                {"supplier_ref": s["tin"], "name": s["short"], "country": "ls", "resident": True}
            )

        entry_seq = [0]

        def entry(prefix: str) -> str:
            entry_seq[0] += 1
            return f"{prefix}-{tin}-{entry_seq[0]:05d}"

        # ---- Foreign services, loans, consultants (books + bank + declarations) ----
        foreign_rows: list[dict[str, Any]] = []  # ledger rows
        bank_rows: list[list[str]] = []
        wht_lines: dict[str, list[dict[str, Any]]] = {pp: [] for pp in periods}
        imp_vat_declared: dict[str, Decimal] = {pp: ZERO for pp in periods}
        expected_imp_vat: dict[str, Decimal] = {pp: ZERO for pp in periods}
        foreign_parties: dict[str, dict[str, Any]] = {}

        def add_party(ref: str, name: str, country: str, account: str) -> None:
            if ref not in foreign_parties:
                foreign_parties[ref] = {
                    "supplier_ref": ref,
                    "name": name,
                    "country": country,
                    "resident": False,
                    "bank_account_ref": account,
                }

        def pay_foreign(
            ref: str,
            account: str,
            country: str,
            ccy: str,
            rate: Decimal,
            name_variant: str,
            period: str,
            lsl: Decimal,
            narration: str,
        ) -> None:
            day = r.randint(3, 25)
            pay_period = _next(period) if r.random() < 0.25 else period
            instructed = _d(lsl / rate) if ccy != "LSL" else lsl
            settled = _d(instructed * rate)
            settle_rate = rate
            bank_rows.append(
                [
                    f"SYN-LS-ACC-{t:03d}",
                    f"FX-{tin}-{len(bank_rows) + 1:05d}",
                    _date(pay_period, day),
                    _date(pay_period, day + 2),
                    "outgoing",
                    _s(instructed),
                    ccy,
                    _s(settled),
                    "LSL",
                    f"{settle_rate:.6f}",
                    "Synthetic bank advice",
                    name_variant,
                    account,
                    country,
                    "",
                    narration,
                ]
            )
            if country not in {"za", "na", "sz"}:
                report = {
                    "report_id": f"CB-{tin}-{len(bank_rows):05d}",
                    "resident_tin": tin,
                    "value_date": _date(pay_period, day),
                    "direction": "outgoing",
                    "amount": _s(settled),
                    "currency": "LSL",
                    "counterparty_country": country,
                }
                if p.get("central_bank_purpose"):
                    # TAX-141 (opt-in): the report's balance-of-payments purpose.
                    report["purpose"] = "income" if narration.startswith("Loan") else "services"
                records["central_bank.cross_border_payment"].append(report)
            return None

        payee_variant = not issues and r.random() < 0.05
        if payee_variant:
            tk["noise_taxpayer"] = ["wht_payee_reference_variant"]

        def declare(
            ref: str, period: str, gross: Decimal, kind: str, rate_used: Decimal, relief: bool
        ) -> None:
            wht_lines[period].append(
                {
                    "line_id": entry("WHT"),
                    "payee_ref": ("PAYEE-" + ref[2:]) if payee_variant else ref,
                    "payment_type": kind,
                    "gross_amount": _s(gross),
                    "wht_amount": _s(gross * rate_used),
                    "treaty_relief_claimed": relief,
                }
            )

        # Management fees to the parent.
        has_fees = (
            "untaxed_imported_services" in issues
            or "related_party_loss" in issues
            or (not issues and r.random() < 0.45)
        )
        if has_fees:
            add_party(parent_ref, parent_name[1], parent_country, parent_account)
            monthly = _d(Decimal(str(r.uniform(60000, 450000))))
            treaty = not issues and r.random() < 0.25
            cert_missing = treaty and Decimal(str(r.random())) < noise["treaty_certificate_missing"]
            if treaty and not cert_missing:
                sub["residence_certificates"].append(parent_ref)
            if treaty:
                counterparts.append(
                    "treaty_relief" + ("_certificate_missing" if cert_missing else "")
                )
            elif not issues:
                counterparts.append("foreign_services_declared")
            under_declare = "untaxed_imported_services" in issues
            misbooked = under_declare and r.random() < 0.3
            if misbooked:
                tk["imported_services_disguise"] = "fees_booked_as_operating_expenses"
            partial = under_declare and r.random() < 0.4
            risk = ZERO
            for i_p, period in enumerate(periods):
                amt = monthly
                foreign_rows.append(
                    {
                        "entry_id": entry("SI"),
                        "period": period,
                        "supplier_ref": parent_ref,
                        "invoice_number": f"MF-{period}",
                        "invoice_date": _date(period, 28),
                        "net": _s(amt),
                        "vat": "0.00",
                        "tax_code": "NONE",
                        "account": "6900" if misbooked else "6100",
                    }
                )
                pay_foreign(
                    parent_ref,
                    parent_account,
                    parent_country,
                    parent_ccy,
                    parent_rate,
                    r.choice((parent_name[0], parent_name[1], parent_name[1].title())),
                    period,
                    amt,
                    f"Management services {period}",
                )
                rate_used = treaty_rate if treaty else wht_rate
                expected_wht = _d(amt * rate_used)
                exp_vat = _d(amt * vat)
                expected_imp_vat[period] += exp_vat
                if under_declare:
                    if partial and i_p % 2 == 0:
                        declare(parent_ref, period, amt, "management_fee", wht_rate, False)
                        imp_vat_declared[period] += exp_vat
                    else:
                        risk += expected_wht + exp_vat
                else:
                    declare(parent_ref, period, amt, "management_fee", rate_used, treaty)
                    imp_vat_declared[period] += exp_vat
            if under_declare:
                tk["amount_at_risk"]["untaxed_imported_services"] = _s(risk)
                tk["expected_routes"]["untaxed_imported_services"] = [
                    "CAND-WHT-FOREIGN",
                    "CAND-IMPORTED-SERVICES-VAT",
                ]

        # Loans from the parent (policy gap or market rate).
        if "policy_gap" in issues or (not issues and r.random() < 0.12):
            add_party(parent_ref, parent_name[1], parent_country, parent_account)
            principal = _d(Decimal(str(r.uniform(5e6, 4e7))))
            interest_free = "policy_gap" in issues
            rate_pa = ZERO if interest_free else Decimal(str(round(r.uniform(0.07, 0.10), 4)))
            doc_id = entry("AGR")
            sub["agreements"].append(
                {
                    "document_id": doc_id,
                    "kind": "loan",
                    "counterparty_ref": parent_ref,
                    "related_party": True,
                    "principal": _s(principal),
                    "annual_interest_rate": str(rate_pa),
                }
            )
            if interest_free:
                tk["amount_at_risk"]["policy_gap"] = "not_quantified_no_legal_route"
                tk["expected_routes"]["policy_gap"] = ["GAP-ARMS-LENGTH"]
            else:
                counterparts.append("market_rate_loan")
                monthly_interest = _d(principal * rate_pa / 12)
                for period in periods:
                    foreign_rows.append(
                        {
                            "entry_id": entry("SI"),
                            "period": period,
                            "supplier_ref": parent_ref,
                            "invoice_number": f"INT-{period}",
                            "invoice_date": _date(period, 28),
                            "net": _s(monthly_interest),
                            "vat": "0.00",
                            "tax_code": "NONE",
                            "account": "6300",
                        }
                    )
                    pay_foreign(
                        parent_ref,
                        parent_account,
                        parent_country,
                        parent_ccy,
                        parent_rate,
                        parent_name[1],
                        period,
                        monthly_interest,
                        f"Loan interest {period}",
                    )
                    declare(parent_ref, period, monthly_interest, "interest", wht_rate, False)

        # Foreign individuals.
        roster: list[str] = []
        persons = []
        if "disguised_employment" in issues:
            for k in range(r.randint(1, 2)):
                persons.append(("disguised", f"PER-{tin}-{k}", r.randint(200, 320)))
        elif not issues and r.random() < 0.15:
            counterparts.append("genuine_consultant")
            for k in range(r.randint(1, 2)):
                days = r.randint(150, 200) if r.random() < 0.08 else r.randint(10, 60)
                persons.append(("genuine", f"PER-{tin}-{k}", days))
        for kind, person, days in persons:
            firm_ref = parent_ref if kind == "disguised" else f"F-ZA-FIRM-{tin}-{person[-1]}"
            firm_account = parent_account if kind == "disguised" else f"SYN-ZA-F-{tin}{person}"
            add_party(
                firm_ref, _name(r)[1], "za" if kind == "genuine" else parent_country, firm_account
            )
            doc_id = entry("AGR")
            sub["agreements"].append(
                {
                    "document_id": doc_id,
                    "kind": "consultancy",
                    "counterparty_ref": firm_ref,
                    "person_ref": person,
                    "related_party": kind == "disguised",
                    "stated_terms": {
                        "duration_and_continuity": "fixed_term_project",
                        "payment_basis": "per_deliverable",
                        "exclusivity": "non_exclusive",
                    },
                }
            )
            records_permit = sub.setdefault("permits", [])
            permit_recorded = r.random() >= (0.30 if kind == "disguised" else 0.10)
            if permit_recorded:
                records_permit.append(
                    {"person_ref": person, "sponsor_tin": tin, "year": 2025, "days_present": days}
                )
            elif kind == "disguised":
                tk.setdefault("notes", []).append("permit_record_missing")
            months_billed = periods if kind == "disguised" else r.sample(periods, r.randint(2, 5))
            fee = _d(Decimal(str(r.uniform(40000, 120000))))
            for period in sorted(months_billed):
                amt = fee if kind == "disguised" else _d(fee * Decimal(str(r.uniform(0.5, 1.8))))
                foreign_rows.append(
                    {
                        "entry_id": entry("SI"),
                        "period": period,
                        "supplier_ref": firm_ref,
                        "invoice_number": f"CF-{person}-{period}",
                        "invoice_date": _date(period, 27),
                        "net": _s(amt),
                        "vat": "0.00",
                        "tax_code": "NONE",
                        "account": "6200",
                        "person_ref": person,
                    }
                )
                pay_foreign(
                    firm_ref,
                    firm_account,
                    "za" if kind == "genuine" else parent_country,
                    "ZAR" if kind == "genuine" else parent_ccy,
                    Decimal("1.000000") if kind == "genuine" else parent_rate,
                    "Consulting",
                    period,
                    amt,
                    f"Consulting {period}",
                )
                declare(firm_ref, period, amt, "service_fee", wht_rate, False)
                exp_vat = _d(amt * vat)
                expected_imp_vat[period] += exp_vat
                imp_vat_declared[period] += exp_vat
        if persons and "disguised_employment" in issues:
            tk["expected_routes"]["disguised_employment"] = ["CAND-EMPLOYMENT"]
            tk["amount_at_risk"]["disguised_employment"] = "not_quantified_specialist_review"
        for k in range(r.randint(5, 40)):
            roster.append(f"EMP-{tin}-{k:03d}")
        records["etax.paye_roster"].append(
            {
                "roster_id": f"PAYE-{tin}-2025",
                "tin": tin,
                "year": 2025,
                "employees": [{"person_ref": x} for x in roster],
            }
        )

        # ---- CIT history and related-party loss ----
        revenue0 = base_sales * 12
        fees0 = _d(revenue0 * Decimal("0.03"))
        startup = not issues and r.random() < 0.08
        genuine_losses = not issues and not startup and r.random() < 0.03
        if startup:
            counterparts.append("startup_losses_explained")
        if genuine_losses:
            counterparts.append("genuine_losses_undocumented")
        for y_i, year in enumerate(range(2021, 2026)):
            revenue = _d(revenue0 * Decimal(str(1 + 0.03 * y_i + r.uniform(-0.02, 0.02))))
            if "related_party_loss" in issues:
                fees = _d(fees0 * Decimal("1.35") ** y_i)
                result = "loss" if y_i >= 1 else "profit"
            elif genuine_losses:
                fees = _d(fees0 * Decimal("1.15") ** y_i)
                result = "loss" if y_i >= 2 else "profit"
            else:
                fees = _d(fees0 * Decimal(str(1 + r.uniform(-0.03, 0.05))))
                result = "loss" if startup and y_i <= 2 else "profit"
            other = _d(revenue * Decimal("0.9"))
            records["etax.cit_return"].append(
                {
                    "cit_return_id": f"CIT-{tin}-{year}",
                    "tin": tin,
                    "year": year,
                    "revenue": _s(revenue),
                    "related_party_fees": _s(fees),
                    "other_expenses": _s(other),
                    "interest_expense": "0.00",
                    "taxable_income_sign": result,
                    "taxable_income_abs": _s(_d(revenue * Decimal("0.02"))),
                }
            )
            if startup and result == "loss":
                sub["loss_explanations"].append(
                    {"document_id": entry("DOC"), "year": year, "code": "market_entry"}
                )
        if "related_party_loss" in issues:
            tk["expected_routes"]["related_party_loss"] = [
                "CAND-RP-ALLOCATION",
                "CAND-DEDUCTION-TEST",
            ]
            tk["amount_at_risk"]["related_party_loss"] = "not_quantified_allocation_review"

        # ---- Monthly VAT: sales, purchases, imports, fakes ----
        fake_months: set[str] = set()
        strategy = r.choice(("phantom", "registered_unbooked", "registered_booked"))
        if "fake_invoices" in issues:
            tk["fake_strategy"] = strategy
        late: dict[str, list[dict[str, Any]]] = {pp: [] for pp in periods}
        ledger_in: dict[str, Decimal] = {}
        sales_vat: dict[str, Decimal] = {}
        claim_seq = 0
        fake_total = ZERO
        candidate_fake = [pp for pp in periods[2:]]
        if "fake_invoices" in issues:
            fake_months = set(r.sample(candidate_fake, r.randint(4, 8)))
        # TAX-139: suppression from a start month (planted) or a capital programme
        # (look-alike), drawn from the taxpayer's own pc stream.
        suppress_from: str | None = None
        suppress_factor = Decimal("1")
        capex_months: set[str] = set()
        suppressed_total = ZERO
        if pc_r is not None and t in pc_planted:
            suppress_from = periods[pc_r.randint(0, 8)]
            suppress_factor = Decimal(str(round(pc_r.uniform(0.45, 0.70), 4)))
        elif pc_r is not None:
            start = pc_r.randint(0, 8)
            capex_months = set(periods[start : start + pc_r.randint(12, 16)])
        for period in periods:
            sales = _d(base_sales * Decimal(str(r.uniform(0.85, 1.15))))
            z = _d(sales * zero_share)
            ex = _d(sales * Decimal("0.03"))
            std = sales - z - ex
            suppressed_vat = ZERO
            if suppress_from is not None and period >= suppress_from:
                # Declared (and booked) standard-rated sales fall short of the real ones.
                true_out = _d(std * vat)
                std = _d(std * suppress_factor)
                suppressed_vat = true_out - _d(std * vat)
                suppressed_total += suppressed_vat
            out_vat = _d(std * vat)
            sales_vat[period] = out_vat
            saft["sales_summary"] += [
                {"period": period, "tax_code": "STD", "net": _s(std), "vat": _s(out_vat)},
                {"period": period, "tax_code": "ZERO", "net": _s(z), "vat": "0.00"},
                {"period": period, "tax_code": "EXEMPT", "net": _s(ex), "vat": "0.00"},
            ]
            purchases = _d(sales * Decimal(str(r.uniform(0.78, 0.92))))
            k = r.randint(6, 11)
            weights = [r.random() + 0.2 for _ in range(k)]
            wsum = sum(weights)
            this_lines: list[dict[str, Any]] = []
            for j in range(k):
                s = r.choice(my_suppliers)
                net = _d(purchases * Decimal(str(weights[j] / wsum)))
                iv = _d(net * vat)
                inv_no = f"INV-{s['tin'][-4:]}-{period.replace('-', '')}-{tin[-4:]}{j:02d}"
                inv_date = _date(period, r.randint(1, 28))
                row = {
                    "entry_id": entry("SI"),
                    "period": period,
                    "supplier_ref": s["tin"],
                    "invoice_number": inv_no,
                    "invoice_date": inv_date,
                    "net": _s(net),
                    "vat": _s(iv),
                    "tax_code": "STD",
                    "account": "5000",
                }
                if dmy:
                    row["invoice_date"] = f"{inv_date[8:10]}/{inv_date[5:7]}/{inv_date[:4]}"
                saft["supplier_invoices"].append(row)
                ledger_in[period] = ledger_in.get(period, ZERO) + iv
                line = {
                    "kind": "domestic",
                    "supplier": s,
                    "invoice_number": inv_no,
                    "invoice_date": inv_date,
                    "net": net,
                    "vat": iv,
                    "fake": None,
                }
                declared = Decimal(str(r.random())) >= noise["supplier_gap_per_idms_invoice"]
                if s["tin"] in integrated:
                    records["idms.invoice"].append(
                        {
                            "idms_id": f"IDMS-{inv_no}-{tin}",
                            "invoice_number": inv_no,
                            "supplier_tin": s["tin"],
                            "buyer_tin": tin,
                            "issue_date": inv_date,
                            "net_amount": _s(net),
                            "vat_amount": _s(iv),
                            "supplier_output_declared": declared,
                        }
                    )
                    if not declared:
                        line["noise"] = "supplier_gap"
                claim_target = period
                if (
                    period != periods[-1]
                    and Decimal(str(r.random())) < noise["late_claim_per_invoice"]
                ):
                    claim_target = _next(period)
                late[claim_target].append(line) if claim_target != period else this_lines.append(
                    line
                )
            if imports:
                for _ in range(r.randint(1, 2)):
                    cv = _d(Decimal(str(r.uniform(80000, 900000))))
                    ivat = _d(cv * vat)
                    entry_id = entry("ASY")
                    records["asycuda.entry"].append(
                        {
                            "entry_id": entry_id,
                            "importer_tin": tin,
                            "registered_on": _date(period, r.randint(1, 28)),
                            "origin_country": "za",
                            "customs_value": _s(cv),
                            "import_vat": _s(ivat),
                            "currency": "LSL",
                        }
                    )
                    saft["supplier_invoices"].append(
                        {
                            "entry_id": entry("SI"),
                            "period": period,
                            "supplier_ref": "F-ZA-GOODS",
                            "invoice_number": entry_id,
                            "invoice_date": _date(period, 10),
                            "net": _s(cv),
                            "vat": _s(ivat),
                            "tax_code": "IMPORT",
                            "account": "5000",
                        }
                    )
                    ledger_in[period] = ledger_in.get(period, ZERO) + ivat
                    cited = entry_id
                    if r.random() < 0.02:
                        cited = entry_id[:-1] + str((int(entry_id[-1]) + 1) % 10)
                    this_lines.append(
                        {
                            "kind": "import",
                            "supplier": None,
                            "invoice_number": cited,
                            "noise": "customs_ref_typo" if cited != entry_id else None,
                            "invoice_date": _date(period, 10),
                            "net": cv,
                            "vat": ivat,
                            "fake": None,
                        }
                    )
            if pc_r is not None and period in capex_months:
                for _ in range(pc_r.randint(1, 3)):
                    s = pc_r.choice(suppliers)
                    net = _d(Decimal(str(pc_r.uniform(250000, 1100000))))
                    iv = _d(net * vat)
                    inv_no = f"CAP-{s['tin'][-4:]}-{period.replace('-', '')}-{tin[-4:]}"
                    inv_no += f"{len(this_lines):02d}"
                    inv_date = _date(period, pc_r.randint(1, 28))
                    saft["supplier_invoices"].append(
                        {
                            "entry_id": entry("SI"),
                            "period": period,
                            "supplier_ref": s["tin"],
                            "invoice_number": inv_no,
                            "invoice_date": inv_date,
                            "net": _s(net),
                            "vat": _s(iv),
                            "tax_code": "STD",
                            "account": CAPITAL_ACCOUNT,
                        }
                    )
                    if not any(x["supplier_ref"] == s["tin"] for x in saft["suppliers"]):
                        saft["suppliers"].append(
                            {
                                "supplier_ref": s["tin"],
                                "name": s["short"],
                                "country": "ls",
                                "resident": True,
                            }
                        )
                    ledger_in[period] = ledger_in.get(period, ZERO) + iv
                    if s["tin"] in integrated:
                        records["idms.invoice"].append(
                            {
                                "idms_id": f"IDMS-{inv_no}-{tin}",
                                "invoice_number": inv_no,
                                "supplier_tin": s["tin"],
                                "buyer_tin": tin,
                                "issue_date": inv_date,
                                "net_amount": _s(net),
                                "vat_amount": _s(iv),
                                "supplier_output_declared": True,
                            }
                        )
                    this_lines.append(
                        {
                            "kind": "domestic",
                            "supplier": s,
                            "invoice_number": inv_no,
                            "invoice_date": inv_date,
                            "net": net,
                            "vat": iv,
                            "fake": None,
                        }
                    )
            claim_lines = this_lines + late[period]
            fake_vat_period = ZERO
            if period in fake_months:
                for _ in range(r.randint(1, 3)):
                    fv = _d(Decimal(str(r.uniform(20000, 150000))))
                    fnet = _d(fv / vat)
                    ftype = "phantom" if strategy == "phantom" else "registered"
                    if ftype == "phantom":
                        fsup: dict[str, Any] = {
                            "tin": _tin(r, used),
                            "name": _name(r)[0],
                            "short": "",
                            "phantom": True,
                        }
                    else:
                        fsup = r.choice(suppliers)
                    inv_no = (
                        f"INV-{fsup['tin'][-4:]}-{period.replace('-', '')}-{tin[-4:]}"
                        f"9{len(claim_lines):02d}"
                    )
                    inv_date = _date(period, r.randint(1, 28))
                    booked = strategy == "registered_booked"
                    if booked:
                        saft["supplier_invoices"].append(
                            {
                                "entry_id": entry("SI"),
                                "period": period,
                                "supplier_ref": fsup["tin"],
                                "invoice_number": inv_no,
                                "invoice_date": inv_date,
                                "net": _s(fnet),
                                "vat": _s(fv),
                                "tax_code": "STD",
                                "account": "5000",
                            }
                        )
                        ledger_in[period] = ledger_in.get(period, ZERO) + fv
                    colludes = r.random() < 0.4
                    if ftype == "registered" and fsup["tin"] in integrated and colludes:
                        records["idms.invoice"].append(
                            {
                                "idms_id": f"IDMS-{inv_no}-{tin}",
                                "invoice_number": inv_no,
                                "supplier_tin": fsup["tin"],
                                "buyer_tin": tin,
                                "issue_date": inv_date,
                                "net_amount": _s(fnet),
                                "vat_amount": _s(fv),
                                "supplier_output_declared": False,
                            }
                        )
                    claim_lines.append(
                        {
                            "kind": "domestic",
                            "supplier": fsup,
                            "invoice_number": inv_no,
                            "invoice_date": inv_date,
                            "net": fnet,
                            "vat": fv,
                            "fake": {"type": ftype, "booked": booked},
                        }
                    )
                    fake_vat_period += fv
            input_claimed = sum((ln["vat"] for ln in claim_lines), ZERO)
            refund = max(ZERO, input_claimed - out_vat)
            return_id = f"VAT-{tin}-{period}"
            records["etax.vat_return"].append(
                {
                    "return_id": return_id,
                    "tin": tin,
                    "period": period,
                    "standard_rated_sales": _s(std),
                    "zero_rated_sales": _s(z),
                    "exempt_sales": _s(ex),
                    "output_vat": _s(out_vat),
                    "input_vat_claimed": _s(input_claimed),
                    "imported_services_vat_declared": _s(imp_vat_declared[period]),
                    "refund_claim": _s(refund),
                    "filed_on": _date(_next(period), 19),
                }
            )
            if wht_lines[period]:
                records["etax.wht_return"].append(
                    {
                        "wht_return_id": f"WHT-{tin}-{period}",
                        "tin": tin,
                        "period": period,
                        "filed_on": _date(_next(period), 15),
                        "lines": wht_lines[period],
                    }
                )
            # Journals (VAT control accounts tie to the ledger as booked).
            booked_in = ledger_in.get(period, ZERO)
            saft["journals"].append(
                {
                    "journal_id": f"JNL-{tin}-{period}",
                    "period": period,
                    "lines": [
                        {"account": "1400", "debit": _s(booked_in), "credit": "0.00"},
                        {"account": "2400", "debit": "0.00", "credit": _s(out_vat)},
                        {"account": "2100", "debit": _s(out_vat), "credit": _s(booked_in)},
                    ],
                }
            )
            if refund <= 0:
                continue
            claim_seq += 1
            claim_id = f"ORA-{tin}-{claim_seq:03d}"
            ora_lines = []
            uploads = []
            noise_found: list[str] = []
            for idx, ln in enumerate(claim_lines):
                line_id = f"{claim_id}-L{idx + 1:02d}"
                supplier_ref = (
                    ln["invoice_number"] if ln["kind"] == "import" else ln["supplier"]["tin"]
                )
                if ln["kind"] == "import":
                    supplier_ref = "F-ZA-GOODS"
                typo = False
                if (
                    ln["kind"] == "domestic"
                    and ln["fake"] is None
                    and Decimal(str(r.random())) < noise["tin_typo_per_line"]
                ):
                    swapped = sorted(_swaps(supplier_ref))
                    if swapped:
                        supplier_ref = r.choice(swapped)
                        typo = True
                ora_lines.append(
                    {
                        "line_id": line_id,
                        "kind": ln["kind"],
                        "supplier_ref": supplier_ref,
                        "invoice_number": ln["invoice_number"],
                        "invoice_date": ln["invoice_date"],
                        "net_amount": _s(ln["net"]),
                        "vat_amount": _s(ln["vat"]),
                    }
                )
                if ln.get("noise") in {"supplier_gap", "customs_ref_typo"}:
                    noise_found.append(str(ln["noise"]))
                if ln["kind"] == "domestic":
                    real_tin = ln["supplier"]["tin"]
                    needs_copy = real_tin not in integrated or ln["fake"] is not None
                    u_missing = Decimal(str(r.random()))
                    if needs_copy:
                        missing = (
                            ln["fake"] is None
                            and u_missing < noise["missing_copy_per_unintegrated_line"]
                        )
                        if missing:
                            noise_found.append("missing_copy")
                        else:
                            doc_id = f"DOC-{line_id}"
                            uploads.append(
                                {"document_id": doc_id, "line_id": line_id, "kind": "invoice_copy"}
                            )
                            sub["documents"][doc_id] = {
                                "supplier_tin": real_tin,
                                "invoice_number": ln["invoice_number"],
                                "net_amount": _s(ln["net"]),
                                "vat_amount": _s(ln["vat"]),
                            }
                if typo:
                    noise_found.append("tin_typo")
            if Decimal(str(r.random())) < noise["duplicate_line_per_claim"] and ora_lines:
                dup = dict(r.choice([x for x in ora_lines]))
                dup["line_id"] = f"{claim_id}-L{len(ora_lines) + 1:02d}"
                ora_lines.append(dup)
                noise_found.append("duplicate_line")
            submitted = _date(_next(period), 22)
            records["online_refunds.claim"].append(
                {
                    "claim_id": claim_id,
                    "tin": tin,
                    "period": period,
                    "return_id": return_id,
                    "claimed_amount": _s(refund),
                    "submitted_on": submitted,
                    "lines": ora_lines,
                    "uploaded_documents": uploads,
                }
            )
            fraud = rs(f"score:{t}")
            if fake_vat_period > 0:
                score = fraud.betavariate(5, 2)
            else:
                score = fraud.betavariate(2, 6)
            records["rsl_fraud_model.score"].append(
                {
                    "claim_id": claim_id,
                    "tin": tin,
                    "score": f"{score:.4f}",
                    "model_id": "RSL-REFUND-FRAUD-STANDIN",
                    "model_version": "fixture-1",
                    "owner": "RSL",
                }
            )
            fake_total += fake_vat_period
            mat_abs = Decimal(p["materiality_absolute"])
            mat_share = Decimal(p["materiality_share"])
            material = fake_vat_period >= mat_abs or (
                refund > 0 and fake_vat_period / refund >= mat_share
            )
            open_items = bool(set(issues) & {"untaxed_imported_services", "related_party_loss"})
            structural = zero_share >= Decimal(p["structural_min_zero_share"])
            questions = bool(
                set(noise_found)
                & {"supplier_gap", "missing_copy", "duplicate_line", "customs_ref_typo"}
            )
            if fake_vat_period > 0 and material:
                lane = "investigation"
            elif fake_vat_period > 0 or questions or open_items or not structural:
                lane = "standard"
            else:
                lane = "fast"
            tk["claims"][claim_id] = {
                "policy_lane": lane,
                "fake_vat": _s(fake_vat_period),
                "fake_types": [ln["fake"] for ln in claim_lines if ln["fake"]],
                "noise": sorted(set(noise_found)),
                "valid_refund": _s(refund - min(refund, fake_vat_period)),
                "claimed": _s(refund),
            }
            if suppress_from is not None:
                # The refund is overstated by the output VAT left off the return.
                tk["claims"][claim_id]["suppressed_output_vat"] = _s(suppressed_vat)
                tk["claims"][claim_id]["valid_refund"] = _s(max(ZERO, refund - suppressed_vat))
        if "fake_invoices" in issues:
            tk["amount_at_risk"]["fake_invoices"] = _s(fake_total)
            tk["expected_routes"]["fake_invoices"] = ["CAND-INPUT-VAT-EVIDENCE"]
        if suppress_from is not None:
            tk["amount_at_risk"][PERSISTENT_CREDIT] = _s(suppressed_total)
            tk["expected_routes"][PERSISTENT_CREDIT] = ["CAND-REFUNDS", "CAND-RECORDS"]
            tk["suppression"] = {"from": suppress_from, "factor": str(suppress_factor)}
        if capex_months:
            tk["capex_months"] = sorted(capex_months)

        # Bank file and supplier master.
        saft["suppliers"] += list(foreign_parties.values())
        saft["suppliers"].append(
            {
                "supplier_ref": "F-ZA-GOODS",
                "name": "Goods imports",
                "country": "za",
                "resident": False,
            }
        )
        saft["supplier_invoices"] += foreign_rows
        if bank_rows:
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="\n")
            writer.writerow(
                [
                    "account_ref",
                    "transfer_ref",
                    "instruction_date",
                    "settlement_date",
                    "direction",
                    "instructed_amount",
                    "instructed_currency",
                    "settled_amount",
                    "settled_currency",
                    "exchange_rate",
                    "rate_source",
                    "counterparty_name",
                    "counterparty_account_ref",
                    "counterparty_country",
                    "intermediary_bank_ref",
                    "narration",
                ]
            )
            for bank_row in bank_rows:
                if bank_row[6] == "LSL":
                    bank_row[7] = bank_row[8] = bank_row[9] = bank_row[10] = ""
                writer.writerow(bank_row)
            sub["foreign_payments_csv"] = buf.getvalue()
        submissions[tin] = sub
        key[tin] = tk

    world = {
        "synthetic": True,
        "generator_version": GENERATOR_VERSION,
        "tenant_id": TENANT,
        "retrieved_at": "2026-11-06T08:00:00Z",
        "periods": periods,
        "rates_assumed": {
            "vat": p["vat_rate"],
            "wht": p["wht_rate"],
            "wht_treaty": p["wht_treaty_rate"],
        },
        "materiality": {"absolute": p["materiality_absolute"]},
        "records": records,
        "submissions": submissions,
        "taxpayers": sorted(submissions),
        "lekuka_coverage": p["lekuka_coverage"],
    }
    answer_key = {
        "synthetic": True,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "params": p,
        "taxpayers": key,
        "window": sorted(window)[0] + ".." + sorted(window)[-1],
    }
    return world, answer_key
