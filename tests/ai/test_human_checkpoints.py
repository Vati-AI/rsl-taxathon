"""TAX-047 persistent checkpoints and authenticated human resume (model-free)."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HAS_SQLITE_SAVER = (
    importlib.util.find_spec("langgraph") is not None
    and importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
)
TENANT = "tenant-synthetic"
CASE = "SYN-004"
RUN = "run-tax047"
PAYLOAD = {"requested_items": ["VAT working paper for 2025-Q4"], "finding_codes": ["gap"]}


class WorkerCrash(BaseException):
    """Simulates the worker process dying; not an ordinary handled error."""


class FakePort:
    """Idempotent outward action store, like the Vati/TAX-046 execution path."""

    def __init__(self, crash_after_apply: bool = False) -> None:
        self.calls: list[str] = []
        self.applied: dict[str, dict] = {}
        self.crash_after_apply = crash_after_apply

    def apply(self, key, action, approval):
        self.calls.append(key)
        self.applied.setdefault(key, {"decision_id": approval["decision_id"]})
        if self.crash_after_apply:
            self.crash_after_apply = False
            raise WorkerCrash()
        return {"applied": True, "replayed": len(self.calls) > 1}


def proposer(state):
    return "request_further_evidence", dict(PAYLOAD)


@unittest.skipUnless(HAS_SQLITE_SAVER, "LangGraph SQLite checkpointer not installed")
class HumanCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        from ai.workflows import human_checkpoints as hc
        from ai.workflows.fake_graph import create_investigation_state

        self.hc = hc
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "checkpoints.sqlite"
        self.config = hc.thread_config(TENANT, CASE, RUN)
        self.port = FakePort()
        self.savers: list = []
        self.initial = {
            **create_investigation_state(tenant_id=TENANT, case_id=CASE, run_id=RUN),
            "case_version": 4,
        }

    def tearDown(self) -> None:
        for saver in self.savers:
            saver.conn.close()
        self.tmp.cleanup()

    def worker(self, port=None):
        """A fresh worker: new connection, new compiled graph, same checkpoint file."""
        saver = self.hc.open_checkpointer(self.db)
        self.savers.append(saver)
        return self.hc.build_checkpointed_graph(
            proposer, port or self.port, saver, agent_id="agent:tax047"
        )

    def start(self):
        return self.worker().invoke(self.initial, self.config)

    def approval(self, **overrides):
        pending = self.hc.pending_action(self.worker(), self.config)
        approval = {
            "decision_id": "DEC-SYN-004-1",
            "status": "approved",
            "tenant_id": TENANT,
            "case_id": CASE,
            "draft_id": pending["action_id"],
            "outcome": pending["kind"],
            "case_version": pending["case_version"],
            "payload_digest": pending["payload_digest"],
            "proposed_by": "officer-auditor-a",
            "approved_by": "officer-supervisor-b",
            "approver_role": "supervisor",
        }
        return {**approval, **overrides}

    def actor(self, **overrides):
        actor = {
            "actor_id": "officer-supervisor-b",
            "tenant_id": TENANT,
            "roles": ["supervisor"],
            "authenticated": True,
        }
        return {**actor, **overrides}

    # --- persistence and interrupt ---------------------------------------------

    def test_run_interrupts_before_consequential_action_and_persists_digest(self) -> None:
        paused = self.start()
        self.assertTrue(paused["__interrupt__"])
        self.assertEqual(self.port.calls, [])
        pending = self.hc.pending_action(self.worker(), self.config)
        self.assertEqual(pending["kind"], "request_further_evidence")
        self.assertEqual(pending["case_version"], 4)
        self.assertEqual(pending["payload_digest"], self.hc.action_digest(PAYLOAD))
        self.assertEqual(pending["proposed_by"], "agent:tax047")

    def test_worker_process_restart_at_interrupt_then_authorized_resume_applies_once(self) -> None:
        script = textwrap.dedent(
            f"""
            import sys
            from pathlib import Path
            sys.path.insert(0, {str(ROOT)!r})
            from ai.workflows import human_checkpoints as hc
            from ai.workflows.fake_graph import create_investigation_state

            class Port:
                def apply(self, key, action, approval):
                    raise SystemExit("first worker must not apply any action")

            saver = hc.open_checkpointer(Path(sys.argv[1]))
            graph = hc.build_checkpointed_graph(
                lambda s: ("request_further_evidence", {PAYLOAD!r}), Port(), saver,
                agent_id="agent:tax047")
            state = {{**create_investigation_state(
                tenant_id={TENANT!r}, case_id={CASE!r}, run_id={RUN!r}), "case_version": 4}}
            out = graph.invoke(state, hc.thread_config({TENANT!r}, {CASE!r}, {RUN!r}))
            assert out["__interrupt__"]
            print("paused")
            """
        )
        env = {**os.environ, "LANGSMITH_TRACING": "false"}
        first = subprocess.run(
            [sys.executable, "-c", script, str(self.db)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("paused", first.stdout)

        final = self.hc.resume_with_approval(
            self.worker(), self.config, self.approval(), self.actor()
        )
        self.assertEqual(final["action_result"]["status"], "applied")
        self.assertEqual(final["action_result"]["decision_id"], "DEC-SYN-004-1")
        self.assertEqual(len(self.port.calls), 1)
        self.assertIsNone(self.hc.pending_action(self.worker(), self.config))

    # --- unauthorized resume ---------------------------------------------------

    def test_unauthorized_resumes_are_rejected_and_leave_the_run_paused(self) -> None:
        self.start()
        cases = {
            "not_authenticated": ({}, {"authenticated": False}),
            "tenant_mismatch": ({}, {"tenant_id": "tenant-other"}),
            "case_mismatch": ({"case_id": "SYN-001"}, {}),
            "decision_not_approved": ({"status": "proposed"}, {}),
            "draft_mismatch": ({"draft_id": "draft-other"}, {}),
            "outcome_mismatch": ({"outcome": "close_no_further_action"}, {}),
            "role_not_permitted": ({}, {"roles": ["auditor"]}),
            "actor_not_approver": ({}, {"actor_id": "officer-supervisor-c"}),
            "self_approval": (
                {"proposed_by": "officer-supervisor-b"},
                {},
            ),
            "missing_identity": ({"approved_by": None}, {"actor_id": None}),
            "stale_case_version": ({"case_version": 3}, {}),
            "payload_mismatch": ({"payload_digest": "0" * 64}, {}),
        }
        for code, (approval_changes, actor_changes) in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(self.hc.ResumeRejected) as raised:
                    self.hc.resume_with_approval(
                        self.worker(),
                        self.config,
                        self.approval(**approval_changes),
                        self.actor(**actor_changes),
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertIsNotNone(self.hc.pending_action(self.worker(), self.config))
        self.assertEqual(self.port.calls, [])
        # The legitimate approver can still resume afterwards.
        final = self.hc.resume_with_approval(
            self.worker(), self.config, self.approval(), self.actor()
        )
        self.assertEqual(final["action_result"]["status"], "applied")

    def test_direct_command_resume_cannot_bypass_validation(self) -> None:
        from langgraph.types import Command

        self.start()
        graph = self.worker()
        out = graph.invoke(
            Command(
                resume={
                    "approval": self.approval(case_version=3),
                    "actor": self.actor(),
                }
            ),
            self.config,
        )
        self.assertTrue(out["__interrupt__"])
        self.assertEqual(out["__interrupt__"][0].value["last_rejection"], "stale_case_version")
        self.assertEqual(self.port.calls, [])

    def test_agent_identity_cannot_approve_its_own_draft(self) -> None:
        self.start()
        with self.assertRaises(self.hc.ResumeRejected) as raised:
            self.hc.resume_with_approval(
                self.worker(),
                self.config,
                self.approval(approved_by="agent:tax047"),
                self.actor(actor_id="agent:tax047"),
            )
        self.assertEqual(raised.exception.code, "self_approval")

    def test_other_tenant_thread_has_nothing_to_resume(self) -> None:
        self.start()
        other = self.hc.thread_config("tenant-other", CASE, RUN)
        with self.assertRaises(self.hc.ResumeRejected) as raised:
            self.hc.resume_with_approval(
                self.worker(), other, self.approval(), self.actor(tenant_id="tenant-other")
            )
        self.assertEqual(raised.exception.code, "no_pending_checkpoint")

    # --- no duplicate side effect ----------------------------------------------

    def test_second_resume_after_completion_does_not_reapply(self) -> None:
        self.start()
        approval = self.approval()
        self.hc.resume_with_approval(self.worker(), self.config, approval, self.actor())
        with self.assertRaises(self.hc.ResumeRejected) as raised:
            self.hc.resume_with_approval(self.worker(), self.config, approval, self.actor())
        self.assertEqual(raised.exception.code, "no_pending_checkpoint")
        self.assertEqual(len(self.port.calls), 1)

    def test_crash_after_side_effect_replays_with_same_key_and_applies_once(self) -> None:
        port = FakePort(crash_after_apply=True)
        self.worker(port).invoke(self.initial, self.config)
        approval = self.approval()
        with self.assertRaises(WorkerCrash):
            self.hc.resume_with_approval(self.worker(port), self.config, approval, self.actor())
        # A restarted worker continues from the persisted checkpoint.
        final = self.hc.recover_run(self.worker(port), self.config)
        self.assertEqual(final["action_result"]["status"], "applied")
        self.assertEqual(len(port.calls), 2)
        self.assertEqual(len(set(port.calls)), 1)  # same idempotency key both times
        self.assertEqual(len(port.applied), 1)  # one outward effect
        with self.assertRaises(self.hc.ResumeRejected) as raised:
            self.hc.recover_run(self.worker(port), self.config)
        self.assertEqual(raised.exception.code, "nothing_to_recover")

    def test_recovery_cannot_skip_a_pending_approval(self) -> None:
        self.start()
        with self.assertRaises(self.hc.ResumeRejected) as raised:
            self.hc.recover_run(self.worker(), self.config)
        self.assertEqual(raised.exception.code, "approval_required")
        self.assertEqual(self.port.calls, [])

    def test_port_failure_is_recorded_as_failure_for_human_review(self) -> None:
        class FailingPort:
            def apply(self, key, action, approval):
                raise TimeoutError("upstream timeout")

        self.worker(FailingPort()).invoke(self.initial, self.config)
        final = self.hc.resume_with_approval(
            self.worker(FailingPort()), self.config, self.approval(), self.actor()
        )
        self.assertEqual(final["action_result"]["status"], "failed")
        self.assertEqual(final["action_result"]["detail"], {"error": "TimeoutError"})
        self.assertIs(final["requires_human_review"], True)

    # --- configuration ---------------------------------------------------------

    def test_prohibited_or_unknown_actions_are_never_drafted(self) -> None:
        saver = self.hc.open_checkpointer(self.db)
        self.savers.append(saver)
        graph = self.hc.build_checkpointed_graph(
            lambda s: ("refund_denial", {}), self.port, saver, agent_id="agent:tax047"
        )
        with self.assertRaises(ValueError):
            graph.invoke(self.initial, self.config)
        self.assertEqual(self.port.calls, [])

    def test_persistent_store_requires_an_explicit_file(self) -> None:
        with self.assertRaises(ValueError):
            self.hc.open_checkpointer(Path(":memory:"))
        with self.assertRaises(ValueError):
            self.hc.thread_config(TENANT, "", RUN)


if __name__ == "__main__":
    unittest.main()
