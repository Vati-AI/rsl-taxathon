"""The contract operations the live /api/v1 service serves (TAX-090, ADR-046).

These are the operations the auditor app calls. ``backend.HANDLERS`` implements
exactly this set (a bench test checks it). Not served, so ``404 not_found``:

- ``createCase``, ``registerDocument``: cases come from sent submissions through
  the TAX-039 bridge and documents through the portal and pipeline (TAX-087);
- ``startInvestigation``: the AI runtime on real cases (TAX-088);
- the five taxpayer-assistance operations: the taxpayer-scoped service (TAX-100).
"""

from __future__ import annotations

SERVED = frozenset(
    {
        "getWorkspaceSession",
        "listCases",
        "getCase",
        "getTaxpayer",
        "listEvidence",
        "listEntities",
        "listFindings",
        "recordFindingFeedback",
        "getCaseReview",
        "proposeDecision",
        "reviewDecision",
        "requestEvidence",
        "getCaseBriefing",
        "getManagementSummary",
        "getIntegrityReview",
    }
)
