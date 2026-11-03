"""Exercise claims against a real local bare remote; no GitHub calls."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('task_tool', ROOT / 'scripts/task.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

class ClaimTests(unittest.TestCase):
    def run_git(self, cwd, *args):
        return subprocess.check_output(['git', *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()

    def test_second_agent_cannot_take_remote_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            remote, first, second = root/'remote.git', root/'first', root/'second'
            self.run_git(root, 'init', '--bare', '--initial-branch=main', str(remote))
            self.run_git(root, 'clone', str(remote), str(first))
            for key, value in [('user.name','Test'),('user.email','test@example.invalid')]:
                self.run_git(first, 'config', key, value)
            (first/'tasks').mkdir()
            task = json.loads((ROOT/'tasks/TAX-001.json').read_text())
            task.update(kind='task', status='READY')
            (first/'tasks/TAX-001.json').write_text(json.dumps(task))
            self.run_git(first, 'add', '.')
            self.run_git(first, 'commit', '-m', 'seed')
            self.run_git(first, 'push', '-u', 'origin', 'main')
            self.run_git(root, 'clone', str(remote), str(second))
            for key, value in [('user.name','Test'),('user.email','test@example.invalid')]:
                self.run_git(second, 'config', key, value)
            original = tool.ROOT
            try:
                tool.ROOT = first
                tool.start(argparse.Namespace(id='TAX-001', agent='agent-one'))
                claimed = json.loads((first/'tasks/TAX-001.json').read_text())
                self.assertEqual('agent-one', claimed['claimed_by'])
                first_sha = self.run_git(first, 'rev-parse', 'HEAD')
                tool.ROOT = second
                with self.assertRaisesRegex(ValueError, 'Remote claim branch exists'):
                    tool.start(argparse.Namespace(id='TAX-001', agent='agent-two'))
                self.assertEqual(first_sha, self.run_git(second,'ls-remote','origin','refs/heads/task/TAX-001').split()[0])
            finally:
                tool.ROOT = original
