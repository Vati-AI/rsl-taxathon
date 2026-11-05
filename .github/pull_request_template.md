## Task
TAX-XXX — link the task file and issue. Use `Refs #N` until acceptance is verified.

## Change
Describe the user-visible problem and resulting behavior.

## Verification
Commands run, results and relevant screenshots or synthetic evidence. State what was not tested.

## Review
- [ ] Task is IN_REVIEW with this PR URL and evidence
- [ ] Acceptance criteria addressed; blockers and follow-ups recorded
- [ ] No secrets or real taxpayer data
- [ ] Tenant access, provenance and human approvals preserved
- [ ] Relevant tests and `python3 scripts/validate.py` pass
- [ ] Local checks recorded (hosted CI unavailable); owner merges after they pass (ADR-038)
- [ ] UI changes follow docs/DESIGN_STANDARD.md; screenshots/state/accessibility evidence attached (or explain why not applicable)
- [ ] UI changes include screenshots and accessibility evidence (second-person review optional, ADR-038)
- [ ] Shared quality checks pass; material design choices cite official sources/ADR
- [ ] Branch refreshed from main; conflicts resolved and affected checks rerun
- [ ] Prototype scope respected; essential access/evidence/human-review controls preserved
