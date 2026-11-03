"""Behaviour tests for the isolated Jube developer stack tooling (TAX-025)."""

import contextlib
import importlib.util
import io
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("jube_dev", ROOT / "scripts" / "jube_dev.py")
assert spec and spec.loader
jube_dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jube_dev)
# Active configuration only; comments explain upstream risks and must not count as config.
COMPOSE = "\n".join(
    line
    for line in (ROOT / "integration" / "jube" / "docker-compose.jube.yml").read_text().splitlines()
    if not line.lstrip().startswith("#")
)


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".local" / "jube.env"
        patcher = mock.patch.object(jube_dev, "ENV_FILE", self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def generate(self) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            jube_dev.ensure_env()
        return out.getvalue()

    def test_generates_private_unique_secrets_without_printing_them(self):
        printed = self.generate()
        values = jube_dev.read_env()
        secrets = [values[k] for k in jube_dev.SECRET_KEYS]
        self.assertEqual(len(set(secrets)), len(secrets))
        self.assertTrue(all(len(s) >= 32 for s in secrets))
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        self.assertFalse(any(s in printed for s in secrets))
        self.assertEqual(values["JUBE_SOURCE_COMMIT"], jube_dev.locked_commit())

    def test_existing_secrets_are_never_regenerated(self):
        self.generate()
        before = self.env.read_text()
        self.generate()
        self.assertEqual(self.env.read_text(), before)

    def test_commit_drift_from_lock_is_refused(self):
        self.generate()
        self.env.write_text(
            self.env.read_text().replace("JUBE_SOURCE_COMMIT=", "JUBE_SOURCE_COMMIT=x")
        )
        with self.assertRaises(SystemExit):
            self.generate()


class ComposeIsolationTests(unittest.TestCase):
    def test_only_jube_is_published_and_only_on_loopback(self):
        published = [line.strip() for line in COMPOSE.splitlines() if "ports:" in line]
        self.assertEqual(published, ['ports: ["127.0.0.1:${JUBE_PORT:-55501}:5001"]'])

    def test_upstream_risks_are_not_reintroduced(self):
        for forbidden in ["container_name", "docker.sock", "redis-stack", ":latest"]:
            self.assertNotIn(forbidden, COMPOSE)
        self.assertIn('EnableSanctionLoader: "False"', COMPOSE)
        self.assertIn('EnableAskJooby: "False"', COMPOSE)
        self.assertIn("name: rsl-taxathon-jube", COMPOSE)


if __name__ == "__main__":
    unittest.main()
