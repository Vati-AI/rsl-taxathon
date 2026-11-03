# Team onboarding

1. Accept the GitHub organization invitation when your verified username is added.
2. Clone `https://github.com/Vati-AI/rsl-taxathon.git`. The expanded coding workspace from PR #17 is merged into main. Pull main before claiming work; Frappe and frontend design additions are merged through PRs #73 and #85.
3. Open the repository in Codex, Claude Code or your editor. Repository skills live in `.agents/skills`; Claude aliases point there. Other agents can read the same SKILL.md files explicitly.
4. Run `python3 scripts/dev.py check`, then `python3 scripts/dev.py serve`. Open http://127.0.0.1:58080/auditor/ or /taxpayer/.
5. Give your agent the role prompt in `docs/team/NAME.md`. It finds an eligible task, claims it and implements it. You review PRs; the agent maintains task state.

No real taxpayer data, cloud credentials or model key is needed to start UI/domain/fixture work. Integration tasks use locked vendor source and their own service setup. Model-backed runs need an approved model credential/data policy; deterministic graph tests do not.

Prerequisites: Python 3.11+, Git, GitHub CLI with repository access; Docker Compose for live services. Native Frappe development uses its vendor devcontainer/bench rather than installing system services on your machine. Check free ports and resources; do not stop other projects.

First tasks: Leseli TAX-020; Kananelo TAX-021; Edga TAX-019; Gupta TAX-022; Jane TAX-023; Yellow TAX-024; Keletso TAX-018 (then service setup tasks TAX-025–028). All seven can begin independently.

Prototype v1 uses [the shared engineering standard](ENGINEERING_STANDARD.md) and [toolchain checks](TOOLCHAIN.md).
