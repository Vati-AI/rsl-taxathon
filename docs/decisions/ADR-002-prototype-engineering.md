# ADR-002: Consistent, affordable prototype v1

Status: accepted for setup; implementation follows assigned task acceptance.
Owner: Keletso; TAX-083. Date: 2026-11-03.

Decision: GitHub Free, one monorepo, human PR review, current service authorities and a small shared toolchain. No paid-plan prerequisite. Keep essential authentication, isolation, provenance and review controls; defer enterprise hardening.

Custom frontend default: Vue 3 + TypeScript + Vite + Frappe UI, npm lockfile, shared tokens/components. Native Frappe pages can use its framework assets. This aligns the custom screens with the user's Frappe preference and avoids separate UI stacks. TAX-020/021 pin compatible frontend package versions and prove a build; the existing fixtures do not yet implement this stack. If pinned Frappe components conflict with the selected versions, record the tested adjustment rather than improvising another framework.

Tooling: Python 3.13 for host dev/CI (fixture tooling retains 3.11 compatibility); Node 22 for frontend tooling; Frappe runs its separately pinned framework/container runtime. Ruff for Python formatting/lint, Mypy for gradual boundary checks; ESLint/Prettier for current web assets. Full strict typing can grow with feature code, not a wholesale vendor-code rewrite. Current framework and AI locks remain authoritative.

Research basis:
- https://ui.frappe.io/ — official Vue component examples support the frontend choice; adoption still needs task-level compatibility testing.
- https://docs.astral.sh/ruff/configuration/ — shared repository formatting/lint configuration.
- https://docs.frappe.io/framework/user/en/api/database — permission-aware versus bypassing reads and transaction behaviour.
- https://docs.langchain.com/oss/python/langgraph/interrupts — resumable human checkpoints and replay-aware side effects.
- https://spec.openapis.org/oas/ — executable API descriptions, implemented by TAX-018.
- https://asvs.owasp.org/ — select relevant auth/access/input controls; no claim of ASVS certification.
- https://csrc.nist.gov/projects/ssdf — integrate review/checks into development proportionately.
- https://opentelemetry.io/docs/concepts/signals/ — common logs/correlation concepts; no hosted telemetry purchase required.

Sources checked 2026-11-03. These are design inputs, not proof of product conformance. Verification: see TAX-083 evidence and CI; feature-level tests remain in their tasks. Revisit before real data, external deployment or material scale increases.
