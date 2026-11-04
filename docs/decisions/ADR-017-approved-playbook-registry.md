# ADR-017: Approved tool and playbook registry pinned to source digests

Status: proposed (TAX-061, pending review)
Owner and task: Gupta (owner), TAX-061; implemented by claude-gupta-20261104-tax061. Review: Leseli (AI evaluation), supervisors for activation policy.
Date: 2026-11-04

Context and smallest prototype need: TAX-061 requires that tools and playbooks are versioned with approval status, allowed operations and provenance requirements, that unapproved versions are rejected, and that a known approved version can be replayed. docs/ARCHITECTURE.md and docs/AI_STANDARD.md require supervisor review, holdout evaluation and rollback before activating a new playbook. They also forbid automatic learning or promotion. TAX-046 makes `playbook.approve` a supervisor-only, human-only action. TAX-048, 049, 050, 051 and 052 now provide deterministic tools and playbooks, but nothing yet ties a run to a reviewed version.

Decision:

- `ai/workflows/playbook_registry.synthetic.json` holds the versioned entries (`kind`, `name`, `version`, `status` of approved, proposed or retired), proposer and approver, `allowed_operations` from a fixed allowlist, `provenance` requirements from a fixed set, tool references (`name@version`), and `content_digest`. The digest is a SHA-256 over the named files under `ai/tools` and `ai/workflows`. Line endings are normalised so Windows CRLF and Linux LF checkouts agree.
- The loader refuses the following:
  - a document not marked synthetic;
  - an approver that does not match `SYN-SUPERVISOR-NN`;
  - an approver who is also the proposer;
  - an approver on a non-approved entry;
  - unknown operations or provenance values;
  - `apply_approved_action` without `human_checkpoint`;
  - source files outside `ai/`;
  - unknown tool references;
  - more than one approved version of the same name;
  - a playbook name with no builder in the code.
- `require_approved` and the runner reject a version that is unregistered, proposed or retired, whose current source no longer matches its approved digest, or whose tools are not all approved. Each rejection has a stable code.
- Registry data cannot load code. Builders come from the fixed `PLAYBOOK_BUILDERS` table in the code, and the JSON only selects among them.
- `run_playbook` stamps each finding with provenance: name, version, content digest, approver and registry version. It returns a run record with input and output digests. `replay_run` reruns the recorded approved version on the same input. It reports `replay_matched` or `replay_diverged`, and rejects changed inputs or digests.
- Consequence for contributors: editing a registered source file makes its version unapproved until the registry is updated in the same reviewed PR. `python -m ai.workflows.playbook_registry` prints the current digests. CI fails on a mismatch, which turns code review into the approval gate for this prototype.

Alternatives and tradeoff:

- Version strings only, with no digest: simpler, but an edited tool would keep running under an approved label.
- Store the registry in Frappe or a database: closer to the eventual supervisor workflow, but approvals there would not bind to the reviewed source. It also needs live services in every AI test. The JSON registry plus PR review fits the GitHub Free prototype. A later task can source approvals from TAX-046 `playbook.approve` records and keep the same checks.
- Dynamic import paths in the registry: flexible, but registry data would then be able to execute arbitrary code, so this was rejected.
- Digest a Git tree or commit: this changes with unrelated files and isn't available in an installed package. File digests are narrower and portable.

Official sources and version/date checked (2026-11-04): repository sources only. docs/ARCHITECTURE.md (playbook activation), docs/AI_STANDARD.md (no automatic promotion, record workflow version), ADR-007 and `vati_tax.identity.policy` (`playbook.approve` is supervisor-only and human-only), and ADR-014 (`action_digest` canonical encoding). No new dependency.

Verification performed: `tests/ai/test_playbook_registry.py` (11 tests, 27 subtests) covers:

- the shipped registry: every approved digest matches current source, and approvers differ from proposers;
- ten load-time policy violations;
- rejection of unregistered, proposed and retired versions;
- an edited source file rejecting its tool and playbook while unrelated playbooks stay approved;
- a playbook with an unapproved tool being rejected;
- a tampered digest being rejected;
- CRLF and LF producing the same digest;
- an approved run carrying its provenance stamp;
- replays of refund_lanes, imported_services and related_party matching;
- a changed input or digest on replay being rejected, and changed case evidence being reported as `replay_diverged`;
- the checkpointed work_arrangement playbook running through the registry.

Consequences and revisit trigger: approvals are synthetic placeholders and are not RSL activations. Holdout evaluation (TAX-056) and rollback tooling are still required before real activation. Revisit when TAX-046 `playbook.approve` records should become the source of approvals, when TAX-059 needs registry changes in the audit ledger, or when TAX-064/065 propose learned playbook versions. Those versions must enter as `proposed` and can never self-approve.
