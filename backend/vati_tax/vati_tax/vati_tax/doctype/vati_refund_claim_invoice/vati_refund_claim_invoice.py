"""One row of a VAT refund claim's purchase-invoice schedule (TAX-044).

A child table of ``Vati Refund Claim``; the parent validates every row with
``vati_tax.submissions.invoices`` and recomputes the schedule totals.
"""

from frappe.model.document import Document


class VatiRefundClaimInvoice(Document):
    pass
