# Frappe custom app

This is an installable package skeleton, not a configured site or complete DocType model. TAX-019 installs it in an isolated bench; TAX-029 adds roles and tenant permissions. Do not load it into existing HR/helpdesk/production sites.

Install and verify it with the repository tooling, never by hand on a shared site:

```sh
python3 scripts/frappe_dev.py init     # pinned Frappe v16.54.0 bench, taxathon.localhost site, editable vati_tax
python3 scripts/frappe_dev.py verify   # new disposable site: install, migrate twice, import, app tests, drop
```

See [docs/FRAPPE_DEV.md](../../docs/FRAPPE_DEV.md) for running a second checkout with its own project/ports.

Package layout:

- `vati_tax/hooks.py` declares the app; `modules.txt` declares the `Vati Tax` module (package `vati_tax/vati_tax`), where DocTypes go.
- `vati_tax/patches.txt` uses Frappe's `[pre_model_sync]` / `[post_model_sync]` sections. Add a dotted path plus a test for every data migration; never edit site databases manually.
- `pyproject.toml` ships `*.txt`, JSON, HTML and `public/` assets with the package.
- `vati_tax/api.py` exposes only an authenticated scaffold health method. `vati_tax/portal` holds the TAX-021 taxpayer portal boundary and its Frappe-free unit tests. TAX-043 adds `portal/claims.py` (pure VAT refund claim form rules) and `portal.api.submit_refund_claim`, which stores a refund claim in the TAX-031 DocTypes in one transaction with idempotent replay; site-backed tests are in `portal/test_refund_claim_bench.py`.
- `vati_tax/responses` (TAX-045) holds taxpayer responses to evidence requests: `rules.py` (pure taxpayer view allowlist, status mapping and answer rules), `store.py` (scoped reads, the immutable `Vati Evidence Response` and the `responded` transition with a ledger entry). The portal methods are in `portal/api.py` ([ADR-019](../../docs/decisions/ADR-019-evidence-request-responses.md)).
- `vati_tax/identity` (TAX-029) holds roles, tenant memberships, service identities and permissions: `policy.py` (pure rules and the role/action matrix), `permissions.py` (Frappe hooks, `require_action`), `provision.py` (operator helpers) and the `Vati Tenant` / `Vati Tenant Membership` DocTypes. Any DocType in this app with a `tenant_id` field (plus `taxpayer_id` for taxpayer-owned records) is tenant-isolated automatically; see [docs/PERMISSIONS.md](../../docs/PERMISSIONS.md).
- `vati_tax/ids` (TAX-030) holds canonical case IDs and service links. `canonical.py` has the pure rules, `mapping.py` the internal persistence API (`register_case`, `record_link`, `apply_link_event`, `resolve_case`, `case_service_links`), and the `Vati Case Identity` / `Vati Service Link` DocTypes store them ([ADR-006](../../docs/decisions/ADR-006-canonical-ids-service-links.md)).

- `vati_tax/outbox` (TAX-033) holds the durable outbox ([ADR-008](../../docs/decisions/ADR-008-durable-outbox.md)). `policy.py` has the pure rules, `store.enqueue` writes an event in the caller's transaction, `worker.dispatch` delivers committed events at least once (with leases, bounded backoff, dead letters and per-case order), and `api.py` provides operator retry and effect reconciliation. Register handlers with `vati_outbox_handlers = {"<event type>": "dotted.path"}` in hooks. Route outward calls through `Delivery.effect` so that replays never duplicate them.
- `vati_tax/evidence` (TAX-032) holds evidence storage, extraction revisions and provenance. `rules.py` has the pure upload/extraction rules, `store.py` the internal API (`register_upload`, `register_file`, `record_extraction`, `get_evidence`, `evidence_history`, `list_extractions`, `case_evidence`, `verify_integrity`) and `api.py` the whitelisted `upload_evidence`/`get_evidence` methods. The `Vati Evidence`, `Vati Evidence Extraction` and `Vati Extraction Provenance` DocTypes store them ([ADR-011](../../docs/decisions/ADR-011-evidence-storage-provenance.md)).

- `vati_tax/decisions` (TAX-046) holds officer decisions, approvals, evidence requests and their audit events. `rules.py` has the pure input rules and outcome allowlist, `engine.py` the Frappe-free workflow (propose, approve/reject/withdraw, one Jube operation per approved decision, retry, evidence requests), `store.py` the Frappe repository and `api.py` the whitelisted methods. The `Vati Decision`, `Vati Evidence Request` and `Vati Audit Event` DocTypes store them ([ADR-013](../../docs/decisions/ADR-013-decisions-approvals-evidence-requests.md)). A site names its Jube adapter factory in `site_config.json` as `vati_case_lifecycle`.
- `vati_tax/audit` (TAX-059) seals every `Vati Audit Event` into a per-tenant hash chain and records reads, overrides and refused attempts. `ledger.py` has the Frappe-free chain, verifier, review-only signals and summary, `service.py` the policy-checked operations, `store.py` the Frappe binding and `api.py` the supervisor-only whitelisted methods ([ADR-015](../../docs/decisions/ADR-015-audit-ledger-separation-of-duties.md)).
- `vati_tax/exports` (TAX-062) handles scoped evidence export requests, a different supervisor's authorisation, the frozen hash/provenance manifest and retention metadata. `rules.py` has the pure request, manifest and retention rules, `service.py` the Frappe-free workflow (every step and refusal goes to the audit ledger), `store.py` the Frappe binding and `api.py` the whitelisted methods. The `Vati Evidence Export` DocType stores the requests. Retention periods come from `vati_retention_days` in `site_config.json` and are unverified placeholders; nothing is deleted ([ADR-016](../../docs/decisions/ADR-016-evidence-export-retention.md)).
- `vati_tax/challenges` (TAX-069) links an approved decision to an objection, an appeal and their reviewed outcomes. `rules.py` has the pure rules, outcome list and decision digest, `service.py` the Frappe-free workflow (lodge, review by an uninvolved supervisor, history), `store.py` the Frappe binding and `api.py` the whitelisted methods. The `Vati Decision Challenge` DocType stores them. Decisions and evidence are never rewritten, and an overturned outcome raises a review-only `decision_overturned` signal ([ADR-024](../../docs/decisions/ADR-024-objections-appeals-final-outcomes.md)).

Tests named `test_*.py` must pass under plain `unittest` without a site. Site-backed tests skip themselves there and run on the disposable `verify` site via `bench run-tests --app vati_tax`.

- `vati_tax/submissions` (TAX-031) holds the taxpayer and submission rules: `domain.py` (pure money, period, status and field-write rules), `guard.py`/`declaration.py` (controller adapters) and the `Vati Taxpayer`, `Vati Submission`, `Vati VAT Return`, `Vati Refund Claim` and `Vati Imported Service` DocTypes ([ADR-010](../../docs/decisions/ADR-010-submission-doctypes.md)).

TAX-032 and TAX-033 add evidence and the outbox; give their DocTypes `tenant_id`/`taxpayer_id` fields and role permissions, and add negative tests.
