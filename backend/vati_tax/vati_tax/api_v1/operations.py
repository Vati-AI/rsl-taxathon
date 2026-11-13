"""The contract operations the live /api/v1 service serves (TAX-090, ADR-046).

These are the operations the auditor app calls. ``backend.HANDLERS`` implements
exactly this set (a bench test checks it). Not served, so ``404 not_found``:

- ``createCase``, ``registerDocument``: cases come from sent submissions through
  the TAX-039 bridge and documents through the portal and pipeline (TAX-087);
- ``startInvestigation``: the AI runtime on real cases (TAX-088).

The five taxpayer-assistance operations are served for taxpayers only by
``vati_tax.assistance`` (TAX-100).
"""

from __future__ import annotations

SERVED = frozenset(
    {
        "getWorkspaceSession",
        "listCases",
        "getCase",
        "getCaseReferences",  # TAX-133
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
        # TAX-100: taxpayer context only (ADR-031).
        "explainValidationResults",
        "reviewSubmissionDocuments",
        "draftEvidenceResponse",
        "getEvidenceResponseDraft",
        "confirmEvidenceResponseDraft",
    }
)
