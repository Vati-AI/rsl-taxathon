"""Exercise claims against a real local bare remote; no GitHub calls."""

import argparse
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("task_tool", ROOT / "scripts/task.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ClaimTests(unittest.TestCase):
    def run_git(self, cwd, *args):
        return subprocess.check_output(
            ["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL
        ).strip()

    def test_second_agent_cannot_take_remote_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            remote, first, second = root / "remote.git", root / "first", root / "second"
            self.run_git(root, "init", "--bare", "--initial-branch=main", str(remote))
            self.run_git(root, "clone", str(remote), str(first))
            for key, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
                self.run_git(first, "config", key, value)
            (first / "tasks").mkdir()
            task = json.loads((ROOT / "tasks/TAX-001.json").read_text())
            task.update(kind="task", status="READY")
            (first / "tasks/TAX-001.json").write_text(json.dumps(task))
            self.run_git(first, "add", ".")
            self.run_git(first, "commit", "-m", "seed")
            self.run_git(first, "push", "-u", "origin", "main")
            self.run_git(root, "clone", str(remote), str(second))
            for key, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
                self.run_git(second, "config", key, value)
            original = tool.ROOT
            try:
                tool.ROOT = first
                tool.start(argparse.Namespace(id="TAX-001", agent="agent-one"))
                claimed = json.loads((first / "tasks/TAX-001.json").read_text())
                self.assertEqual("agent-one", claimed["claimed_by"])
                first_sha = self.run_git(first, "rev-parse", "HEAD")
                tool.ROOT = second
                with self.assertRaisesRegex(ValueError, "Remote claim branch exists"):
                    tool.start(argparse.Namespace(id="TAX-001", agent="agent-two"))
                self.assertEqual(
                    first_sha,
                    self.run_git(second, "ls-remote", "origin", "refs/heads/task/TAX-001").split()[
                        0
                    ],
                )
            finally:
                tool.ROOT = original

    def test_refresh_preserves_task_and_new_main_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, agent, teammate = root / "remote.git", root / "agent", root / "teammate"
            self.run_git(root, "init", "--bare", "--initial-branch=main", str(remote))
            self.run_git(root, "clone", str(remote), str(agent))
            self.run_git(agent, "config", "user.name", "Test")
            self.run_git(agent, "config", "user.email", "test@example.invalid")
            (agent / "tasks").mkdir()
            (agent / "tasks/TAX-001.json").write_text(json.dumps({"branch": "task/TAX-001"}))
            self.run_git(agent, "add", ".")
            self.run_git(agent, "commit", "-m", "seed")
            self.run_git(agent, "push", "-u", "origin", "main")
            self.run_git(root, "clone", str(remote), str(teammate))
            self.run_git(teammate, "config", "user.name", "Test")
            self.run_git(teammate, "config", "user.email", "test@example.invalid")
            self.run_git(agent, "switch", "-c", "task/TAX-001")
            (agent / "task-work.txt").write_text("agent work")
            self.run_git(agent, "add", ".")
            self.run_git(agent, "commit", "-m", "task work")
            (teammate / "shared.txt").write_text("teammate work")
            self.run_git(teammate, "add", ".")
            self.run_git(teammate, "commit", "-m", "main work")
            self.run_git(teammate, "push", "origin", "main")
            original = tool.ROOT
            try:
                tool.ROOT = agent
                tool.refresh(argparse.Namespace(id="TAX-001"))
                self.assertEqual("agent work", (agent / "task-work.txt").read_text())
                self.assertEqual("teammate work", (agent / "shared.txt").read_text())
                self.assertEqual("task/TAX-001", self.run_git(agent, "branch", "--show-current"))
                # Dirty changes are not stashed, discarded or merged over.
                (agent / "task-work.txt").write_text("uncommitted work")
                with self.assertRaisesRegex(ValueError, "Commit intended changes"):
                    tool.refresh(argparse.Namespace(id="TAX-001"))
                self.assertEqual("uncommitted work", (agent / "task-work.txt").read_text())
            finally:
                tool.ROOT = original
