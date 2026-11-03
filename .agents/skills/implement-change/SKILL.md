---
name: implement-change
description: Implement a claimed RSL Taxathon task within its service authority and acceptance criteria.
---

# Implement Change

Read the task and affected code first. Jube owns operational cases; Frappe owns application/portal/permissions; Vati coordinates APIs; LangGraph owns investigative execution only. Use fixture mode to work without live dependencies, clearly labeled. Consult locked source or deployed API specs rather than inventing endpoints. Verify relevant behavior and tenant/provenance/idempotency boundaries. Record actual evidence with `scripts/task.py evidence`. Keep implementation scoped; new discoveries become follow-up tasks rather than silent scope expansion.

Follow docs/ENGINEERING_STANDARD.md and docs/V1_ACCEPTANCE.md. For APIs use docs/API_STANDARD.md; for AI use docs/AI_STANDARD.md. Record material decisions with official sources in an ADR. Run `python3 scripts/quality.py` after dev-tool installation. GitHub Free is intentional; no paid-plan prerequisite.
