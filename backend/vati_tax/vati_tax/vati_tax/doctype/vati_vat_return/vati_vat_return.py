"""Declared VAT return figures for a ``vat_return`` submission (TAX-031).

One per submission. ``net_vat`` is output VAT minus input VAT on the taxpayer's
own figures (negative is a refund position); it is arithmetic, not an assessment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vati_tax.submissions import domain, guard
from vati_tax.submissions.declaration import DeclarationDocument

AMOUNTS = ("taxable_supplies", "output_vat", "taxable_purchases", "input_vat")


class VatiVATReturn(DeclarationDocument):
    ID_FIELD = "vat_return_id"
    PARENT_TYPES = frozenset({"vat_return"})
    DECLARED = frozenset(AMOUNTS)
    SERVER = DeclarationDocument.SERVER | {"net_vat"}

    if TYPE_CHECKING:
        vat_return_id: str
        submission_id: str
        tenant_id: str
        taxpayer_id: str
        period: str
        currency: str
        taxable_supplies: Any
        output_vat: Any
        taxable_purchases: Any
        input_vat: Any
        net_vat: Any

    def validate_figures(self, parent: Any) -> None:
        for field in AMOUNTS:
            guard.money(self, field)
        self.net_vat = domain.net_vat(
            domain.parse_money(self.output_vat, "output_vat"),
            domain.parse_money(self.input_vat, "input_vat"),
        )
