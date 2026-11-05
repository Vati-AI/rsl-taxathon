# ai-data-qa

Owner: Leseli (reassigned from Yellow). See `tasks/README.md` for work and `docs/ARCHITECTURE.md` for boundaries.

Independent AI evaluation lives here (docs/AI_STANDARD.md). `test_agent_evaluation.py` (TAX-056) runs the `ai/evaluation/agent_eval.py` harness and seeded-fault checks of the harness itself; the expected outputs and hostile fixtures are in `data/synthetic/tax056_evaluation_fixtures.json` and are written from the TAX-024 ground truth, not from playbook output. Run `python -m ai.evaluation.agent_eval` for the full report; adding or removing a known failure is a reviewed fixture change. Latest results: `docs/evidence/TAX-056/README.md`.

`test_taxpayer_assistant_eval.py` (TAX-102) checks the taxpayer-assistant scenarios in `data/synthetic/tax102_taxpayer_assistant_scenarios.json` and the `ai/evaluation/taxpayer_assistant_eval.py` checks: a reference fake must pass, and seeded leaking, fabricating and figure-changing fakes must fail. TAX-098/TAX-099 evaluate their assistant with `taxpayer_assistant_eval.evaluate(factory)`; `agent_eval` includes the same cases once an assistant is registered. Latest results: `docs/evidence/TAX-102/README.md`.
