# ai-data-qa

Owner: Leseli (reassigned from Yellow). See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

Independent AI evaluation lives here (docs/AI_STANDARD.md). `test_agent_evaluation.py` (TAX-056) runs the `ai/evaluation/agent_eval.py` harness and seeded-fault checks of the harness itself; the expected outputs and hostile fixtures are in `data/synthetic/tax056_evaluation_fixtures.json` and are written from the TAX-024 ground truth, not from playbook output. Run `python -m ai.evaluation.agent_eval` for the full report; adding or removing a known failure is a reviewed fixture change. Latest results: `docs/evidence/TAX-056/README.md`.
