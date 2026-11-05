"""Synthetic SYN-001 and SYN-002 claims for the TAX-087 pipeline demo and bench tests.

Figures come from the TAX-024 records (``data/synthetic/tax024_records.json``); the
Frappe-free test ``test_rules.py`` keeps them equal. That file is not mounted in the
Frappe container, hence this copy. The 2025-Q4 period is sent as its last month,
2025-12, because the portal takes monthly periods. Every value is synthetic.
"""

from __future__ import annotations

import csv
import io
from typing import Any

# (invoice id, contract id, counterparty id, net, VAT, gross, amount paid)
Invoice = tuple[str, str, str, str, str, str, str]

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "case": "SYN-001",
        "taxpayer": "SYN-TAXPAYER-001",
        "period": "2025-12",
        "refund_claim": "15000.00",
        "output_vat": "0.00",
        "input_vat_claimed": "15000.00",
        "contracts": {"CTR-SYN-001-01": "SYN-SUPPLIER-001"},
        "invoices": (
            ("INV-SYN-001-01", "CTR-SYN-001-01", "SYN-SUPPLIER-001",
             "100000.00", "9000.00", "109000.00", "109000.00"),
            ("INV-SYN-001-02", "CTR-SYN-001-01", "SYN-SUPPLIER-001",
             "66666.67", "6000.00", "72666.67", "72666.67"),
        ),
    },
    {
        "case": "SYN-002",
        "taxpayer": "SYN-TAXPAYER-002",
        "period": "2025-12",
        "refund_claim": "25000.00",
        "output_vat": "0.00",
        "input_vat_claimed": "25000.00",
        "contracts": {"CTR-SYN-002-01": "SYN-GROUP-002", "CTR-SYN-002-02": "SYN-SUPPLIER-002"},
        "invoices": (
            ("INV-SYN-002-01", "CTR-SYN-002-01", "SYN-GROUP-002",
             "80000.00", "7200.00", "87200.00", "87200.00"),
            ("INV-SYN-002-02", "CTR-SYN-002-02", "SYN-SUPPLIER-002",
             "31111.11", "2800.00", "33911.11", "33911.11"),
        ),
    },
)  # fmt: skip


def counterparty_name(counterparty_id: str) -> str:
    """Display name of a TAX-024 counterparty (as tax_mapping.bundle_from_tax024)."""
    return f"Synthetic counterparty {counterparty_id}"


def _csv(rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["synthetic", "true"])
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def invoice_document(invoice: Invoice) -> bytes:
    number, contract, supplier, net, vat, gross, _paid = invoice
    return _csv(
        [
            ["invoice_number", number],
            ["contract", contract],
            ["supplier", counterparty_name(supplier)],
            ["net_amount_lsl", net],
            ["vat_amount_lsl", vat],
            ["gross_amount_lsl", gross],
        ]
    )


def contract_document(contract: str, supplier: str) -> bytes:
    return _csv([["contract", contract], ["supplier", counterparty_name(supplier)]])


def schedule_row(
    scenario: dict[str, Any],
    day: int,
    invoice: Invoice,
    invoice_evidence: str,
    contract_evidence: str,
) -> dict[str, Any]:
    """One purchase-invoice schedule row as the portal form sends it (TAX-044)."""
    number, _contract, supplier, net, vat, gross, paid = invoice
    return {
        "invoice_number": number,
        "invoice_date": f"{scenario['period']}-{day:02d}",
        "supplier_name": counterparty_name(supplier),
        "supplier_tin": None,
        "net_amount": net,
        "vat_amount": vat,
        "gross_amount": gross,
        "amount_paid": paid,
        "invoice_evidence_id": invoice_evidence,
        "contract_evidence_id": contract_evidence,
    }
