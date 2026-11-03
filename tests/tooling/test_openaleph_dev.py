"""Behaviour tests for the isolated OpenAleph developer stack tooling (TAX-026)."""

import contextlib
import importlib.util
import io
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "openaleph_dev", ROOT / "scripts" / "openaleph_dev.py"
)
assert spec and spec.loader
openaleph_dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(openaleph_dev)
# Active configuration only; comments explain upstream behaviour and must not count as config.
COMPOSE = "\n".join(
    line
    for line in (ROOT / "integration" / "openaleph" / "docker-compose.openaleph.yml")
    .read_text()
    .splitlines()
    if not line.lstrip().startswith("#")
)


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".local" / "openaleph.env"
        patcher = mock.patch.object(openaleph_dev, "ENV_FILE", self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def generate(self) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            openaleph_dev.ensure_env()
        return out.getvalue()

    def test_every_database_uri_uses_the_generated_password(self):
        printed = self.generate()
        env = openaleph_dev.read_env()
        password = env["OA_POSTGRES_PASSWORD"]
        uris = [env["ALEPH_DATABASE_URI"], *(env[k] for k in openaleph_dev.DB_URI_KEYS)]
        self.assertEqual(len(set(uris)), 1)
        self.assertIn(f":{password}@postgres/", uris[0])
        self.assertNotIn("aleph:aleph@", self.env.read_text())
        self.assertNotIn(password, printed)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_multi_user_login_is_enforced(self):
        self.generate()
        env = openaleph_dev.read_env()
        self.assertEqual(env["ALEPH_SINGLE_USER"], "false")
        self.assertEqual(env["ALEPH_PASSWORD_LOGIN"], "true")

    def test_older_env_files_gain_missing_uris_without_changing_secrets(self):
        self.generate()
        lines = [
            line
            for line in self.env.read_text().splitlines()
            if not line.startswith(("OPENALEPH_DB_URI=", "PROCRASTINATE_DB_URI="))
        ]
        self.env.write_text("\n".join(lines) + "\n")
        before = openaleph_dev.read_env()
        self.generate()
        after = openaleph_dev.read_env()
        self.assertEqual(after["OPENALEPH_DB_URI"], before["ALEPH_DATABASE_URI"])
        self.assertEqual(after["ALEPH_SECRET_KEY"], before["ALEPH_SECRET_KEY"])


class ComposeIsolationTests(unittest.TestCase):
    def test_only_ui_is_published_and_only_on_loopback(self):
        published = [line.strip() for line in COMPOSE.splitlines() if "ports:" in line]
        self.assertEqual(published, ['ports: ["127.0.0.1:${OPENALEPH_PORT:-55580}:8080"]'])

    def test_images_are_pinned(self):
        images = [
            line.split("image:", 1)[1].strip() for line in COMPOSE.splitlines() if "image:" in line
        ]
        self.assertTrue(images)
        for image in images:
            with self.subTest(image=image):
                self.assertNotIn(":latest", image)
                self.assertTrue("@sha256:" in image or ":" in image.rsplit("/", 1)[-1])
        self.assertIn("ingest-file:5.4.0-rc4@sha256:", COMPOSE)
        self.assertNotIn("container_name", COMPOSE)
        self.assertIn("name: rsl-taxathon-openaleph", COMPOSE)


if __name__ == "__main__":
    unittest.main()
