"""TAX-073 customs multi-value reconciliation as a case-scoped TAX-022 tool.

Model-free and LangGraph-free: the tool looks up the active case's synthetic
customs/invoice/accounting/payment records by ``(tenant_id, case_id)`` and
returns the deterministic advisory reconciliation in a tool envelope. It never
records a decision; graphs that use it must hand results to an officer.
"""

from collections.abc import Callable, Mapping
from decimal import Decimal

from ai.tools.contracts import (
    ToolInvocationContext,
    ToolResultEnvelope,
    tool_failure,
    tool_success,
)
from ai.tools.customs_reconciliation import (
    DEFAULT_TOLERANCE,
    CustomsDataError,
    reconcile_customs_values,
)
from ai.tools.refund_reconciliation import DEFAULT_CURRENCY

CUSTOMS_TOOL_NAME = "customs_value_reconciliation_tool"

# Keyed by (tenant_id, case_id) so colliding case references across tenants
# can never resolve another tenant's records.
CustomsRecordSource = Callable[[str, str], Mapping[str, object] | None]
CustomsTool = Callable[[ToolInvocationContext], ToolResultEnvelope]


def customs_records_source(
    records: Mapping[tuple[str, str], Mapping[str, object]],
) -> CustomsRecordSource:
    """Wrap an in-memory synthetic record map keyed by (tenant_id, case_id)."""

    def lookup(tenant_id: str, case_id: str) -> Mapping[str, object] | None:
        return records.get((tenant_id, case_id))

    return lookup


def make_customs_reconciliation_tool(
    source: CustomsRecordSource,
    *,
    reporting_currency: str = DEFAULT_CURRENCY,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> CustomsTool:
    """Build a case-scoped tool reconciling the active case's import values."""

    def customs_value_reconciliation_tool(context: ToolInvocationContext) -> ToolResultEnvelope:
        scope = {
            "tool_name": CUSTOMS_TOOL_NAME,
            "tenant_id": context["tenant_id"],
            "case_id": context["case_id"],
            "run_id": context["run_id"],
            "correlation_id": context["correlation_id"],
        }
        record = source(context["tenant_id"], context["case_id"])
        if record is None:
            return tool_failure(
                **scope,
                code="customs_record_not_found",
                message="No customs value records are available for this case.",
                retryable=False,
            )
        if record.get("case_id") not in (None, context["case_id"]):
            return tool_failure(
                **scope,
                code="tool_scope_mismatch",
                message="Customs records did not match the active investigation.",
                retryable=False,
            )
        try:
            data = reconcile_customs_values(
                record, reporting_currency=reporting_currency, tolerance=tolerance
            )
        except CustomsDataError as error:
            # Stable code only; record content stays out of the envelope.
            return tool_failure(
                **scope,
                code=error.code,
                message="Customs records could not be reconciled.",
                retryable=False,
            )
        return tool_success(**scope, data=data)

    customs_value_reconciliation_tool.__name__ = CUSTOMS_TOOL_NAME
    return customs_value_reconciliation_tool
