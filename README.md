# Vati AI — RSL Taxathon

[Start here: PROJECT.md](PROJECT.md) · [Team onboarding](docs/ONBOARDING.md) · [Task backlog](tasks/README.md) · [Agent skills](.agents/skills) · [Architecture](docs/ARCHITECTURE.md)

A shared coding workspace for the RSL Challenge 2 platform: Jube risk/case management, Frappe application/taxpayer portal, OpenAleph/FtM evidence and relationships, yente matching, Docling extraction and LangGraph investigation workflows.

```sh
python3 scripts/dev.py check
python3 scripts/dev.py serve
```

Open localhost:58080/auditor/ or /taxpayer/. This dependency-free environment serves synthetic fixtures and starter UIs so all seven roles can begin without model keys or live RSL access. It is not a completed investigation product.

## AI-managed development

Use the role prompt in `docs/team/NAME.md`. The AI reads the project documents and skills, selects and claims an eligible task, implements/tests, updates task evidence/status and opens a PR. Humans review and merge. Original TAX-001–016 are planning epics; 65 implementation tasks TAX-018–082 cover foundation, pilot, expansion and future phases.

## Services and repository layout

Our code stays in this monorepo. Official upstream source is reproducibly fetched into ignored `external/` using `python3 scripts/upstreams.py fetch --all`; immutable revisions live in `integration/upstreams.lock.json`. No unnecessary forks. See [service setup](docs/SERVICE_SETUP.md).

Root Compose runs Vati PostgreSQL/Redis on separate loopback ports. Optional Docling Compose is provided. Vendor services use their supported stacks; live deployments and adapters must pass their assigned tasks before being called connected.

[Private task board](https://github.com/orgs/Vati-AI/projects/1) · [Setup PR](https://github.com/Vati-AI/rsl-taxathon/pull/17) · [Verified setup and blockers](docs/SETUP_STATUS.md)
