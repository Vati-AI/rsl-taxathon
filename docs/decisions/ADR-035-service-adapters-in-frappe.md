# ADR-035: Service adapters packaged and configured inside the Frappe bench

Status: proposed (code-owner review requested on the TAX-085 PR)
Owner and task: Keletso, TAX-085
Date: 2026-11-05

Context and smallest prototype need:
- The Frappe app calls the integration adapters through site-config factories: `vati_case_lifecycle` (decisions TAX-046, bridge TAX-039) and `vati_risk_evaluator` (bridge). The TAX-087 handlers will also need Docling, OpenAleph and yente.
- Before this ADR no factory existed and the adapters were not importable in the bench, so every officer decision that needs Jube failed with `jube_not_configured`.
- The adapters (`integration/jube`, `docling`, `openaleph`, `followthemoney`, `yente`) use only the standard library. They import each other by flat module name (`from case_lifecycle import ...`, `import tax_mapping`), as the repository's tests and scripts do.
- Inside the Frappe container the developer's services are not on `127.0.0.1`.

Decision:

**Packaging (the ADR-023 mechanism)**
- `docker-compose.frappe.yml` mounts `./integration` read-only at `/workspace/vati_shared/integration`, next to `ai/`.
- `frappe_dev.py init` writes the existing `vati_shared.pth` with `/workspace/vati_shared` plus the five adapter directories. Nothing is installed.
- `verify` imports every adapter module under the bench interpreter (Python 3.14.7) and fails if one is missing or loaded from elsewhere, or if `langgraph` is installed.
- The adapters keep their own names (`case_lifecycle`, `risk_invoke`, `tax_risk`, `extraction_adapter`, `entity_ingest`, `tax_mapping`, `scheme_graph`, `candidate_review`), so bench code and repository tests load the same modules.

**Factories (`vati_tax.integrations`)**
- `config.py` (Frappe-free, unit-tested) builds the real adapters from one `vati_services` object.
- `factories.py` binds it to `frappe.conf`:
  - `case_lifecycle`: `JubeCaseAdapter` with `MappingLinkStore(ids.mapping.record_link, case_service_links)`
  - `risk_evaluator`: `DeclaredRiskEvaluator`
  - `docling_extraction`: the caller passes the job store and sink
  - `openaleph_ingest`: bound to the configured collection
  - `yente_matcher`: bound to the configured dataset
- A missing or invalid section, or adapter code missing from the path, raises `ServiceNotConfigured`: `upstream_unavailable`, 503, reason `<service>_not_configured` or `adapter_not_installed`. Its messages never include configuration values. There is no fixture fallback.
- Unreachable OpenAleph and yente hosts become those modules' own retryable `upstream_unavailable` errors, not raw `URLError`s.

**Configuration**
- `frappe_dev.py init` reads the ignored local files: `.local/jube.env`, `.local/jube-case-workflow.json`, `.local/openaleph.env`, `.local/yente.env` and the root `.env`.
- It passes the result into the container as environment variables (never argv) and merges it into `taxathon.localhost`'s `site_config.json` with mode 600.
- It owns `vati_services`, `vati_case_lifecycle` and `vati_risk_evaluator`, and rewrites them on every run, so re-running is idempotent and a service removed locally disappears from the site.
- Only service names are printed.
- The risk model GUID is recorded once in `.local/jube.env` by `scripts/jube_rules.py record-model`. `init` tries this automatically while Jube is running.
- `verify` runs the app's tests without service config (tests inject fakes). `verify --live-services` then gives the disposable site the same config and runs `vati_tax.integrations.live_check`.

**Container host**
- Loopback service URLs are rewritten to `host.docker.internal`, configurable with `--service-host` / `VATI_SERVICE_HOST`.
- Docker Desktop provides that name. The compose file maps it to `host-gateway` for Linux Docker Engine.
- The five adapters' "plain HTTP only on loopback" rules now also accept exactly `host.docker.internal`, which is the developer's own machine as seen from a container. Lookalike hosts are still refused (tested).

Alternatives and tradeoff:
- **Installing the adapters into the bench (`pip install -e`)**: there is no package metadata, and it breaks the flat sibling imports. Rejected for the same reasons as in ADR-023.
- **Importing them as the `integration.jube...` namespace package**: this breaks `risk_invoke` and `scheme_graph`'s flat imports and would load second copies of the modules next to the tests' copies.
- **Joining the Frappe container to each vendor stack's Docker network**: that couples compose projects and fails when a stack is not running.
- **Secrets as container environment**: they would be visible in `docker inspect` and need a container recreate on every key change. Site config keeps them in the per-site file Frappe already protects.
- **Global `common_site_config.json`**: this would leak live config into every `verify-*` site's tests (some assert `jube_not_configured`) and enable the five-minute Jube poller on them.

Official sources and version/date checked:
- Python 3.14 `site` module (`.pth` lines are directories appended to `sys.path`).
- Docker Desktop networking (`host.docker.internal`); Docker Compose `extra_hosts` with `host-gateway`.
- Frappe 16.54.0 `frappe.conf` per-site config and `bench execute`, in the pinned bench, 2026-11-05.

Verification performed:
- `tests/integration/test_service_factories.py` (14 tests, no network) covers:
  - each factory builds the real adapter
  - a decision proposed and approved through the factory-built Jube adapter against the Jube fake
  - not-configured reasons, with no secret in any error
  - unreachable services become retryable `upstream_unavailable`
  - the host rule refuses lookalike hosts
- `tests/tooling/test_frappe_dev.py` (10 new tests) covers config parsing from ignored files, host rewrite and validation, that nothing secret is printed, the idempotent site-config writer (mode 600, stale keys removed), the mount and the stdlib-only path file.
- On the shared isolated stack (`rsl-taxathon-frappe-tax019`) with the real local Jube, `init` (run twice; `site_config.json` digest unchanged) was followed by `verify --live-services`:
  - 395 Frappe-free tests and 167 + 353 site tests passed; all adapters imported under Python 3.14.7, and `langgraph` was absent.
  - On the clean site, the live check raised the Jube case, an auditor's `continue_investigation` read the status from Jube, and the supervisor's approval synced: Jube read back `INVESTIGATING`.
  - An unreachable Jube was refused as `upstream_unavailable/jube_unavailable` and a missing config as `upstream_unavailable/jube_not_configured`, recording no decisions.

Consequences and revisit trigger:
- Every developer re-runs `frappe_dev.py init` once (with their `--project`/ports) to get the mount, path file and site config.
- Docling, OpenAleph and yente factories are proven offline only; TAX-087 exercises them live.
- A production deployment needs a real secret store and TLS endpoints; the `host.docker.internal` allowance is for local development only.
- Revisit when the Vati service process exists or the adapters become a package.
