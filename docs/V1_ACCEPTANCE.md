# V1 readiness and proportional safeguards

## Required for this prototype

Synthetic data; no committed secrets; server-side authentication and role/taxpayer isolation on product routes; controlled document types/sizes; safe parsing and escaped UI output; human approval for consequential actions; evidence provenance; bounded retries and no duplicate side effects. Local dev ports stay loopback. A shared demo needs HTTPS, restricted access and separate demo credentials. Fixture servers are never publicly deployed as authenticated product APIs.

Test login/logout, taxpayer A versus B access, officer/supervisor permissions, stale approval rejection, duplicate submissions, upload rejection, missing evidence and unavailable upstreams. Keep the role/action matrix alongside TAX-029's executable permission tests. Retain synthetic evidence for the demo and document how to reset it without deleting other projects. Real taxpayer data requires a separate reviewed data-handling decision.

## Five-scenario integrated demo

TAX-058 owns one repeatable walkthrough of submission -> extraction/risk -> case -> investigation -> human review -> recorded outcome. Include all five scenarios, explicit live/fixture disclosure, verified evidence links and reproducible startup instructions. Run the legitimate-refund slice early before expanding other routes. Each service adapter needs its own authenticated smoke test; do not infer connectivity from Docker health alone.

TAX-055 covers browser flows; TAX-056 AI quality; TAX-057 tenant/replay/failure tests; TAX-063 backup/restore of disposable synthetic data. Measure actual response time and model cost; agree a demo budget based on measured hardware. The current tooling tests are not substitutes for these product tests.

## Deliberately deferred

Paid branch protection, enterprise SSO, external penetration-test certification, full compliance programme, high availability/multi-region failover, advanced SOC/SIEM operations and national-scale capacity testing. These are not prerequisites for developers to code or for a local synthetic prototype. Preserve simple interfaces so later hardening does not require rewriting domain rules.

## Review on GitHub Free

The human reviewer checks CI, diff, task evidence, relevant visual review and known limitations before squash merging. Agents fetch/merge main into their own branch and rerun checks before final handover; no force-push is required. Resolve conflicts semantically, never accept one side blindly. CI failures and task-sync failures remain visible. Branch protection is intentionally not purchased; policies are conventions, not a technical access barrier.

After merge an agent verifies the PR and records DONE in a small follow-up PR, then syncs the board. This is agent-maintained, not an unattended hosted worker. Review completion-only PRs promptly to unblock dependencies. Do not claim that GitHub issue closure alone updates canonical task files.
