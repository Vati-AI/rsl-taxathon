"""Behaviour tests for Frappe developer stack isolation and clean-site verification (TAX-019)."""

import importlib.util
import os
import re
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("frappe_dev", ROOT / "scripts" / "frappe_dev.py")
assert spec and spec.loader
frappe_dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frappe_dev)
COMPOSE = (ROOT / "docker-compose.frappe.yml").read_text()


class StackIsolationTests(unittest.TestCase):
    def test_default_stack_and_ports_are_unchanged_for_existing_checkouts(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            cmd = frappe_dev.configure(frappe_dev.DEFAULT_PROJECT, 58000, 59000)
            self.assertEqual(os.environ["FRAPPE_HTTP_PORT"], "58000")
        self.assertEqual(cmd[:4], ["docker", "compose", "-p", "rsl-taxathon-frappe-dev"])
        self.assertIn("127.0.0.1:${FRAPPE_HTTP_PORT:-58000}:8000", COMPOSE)
        self.assertIn("127.0.0.1:${FRAPPE_SOCKETIO_PORT:-59000}:9000", COMPOSE)

    def test_second_checkout_gets_its_own_project_and_ports(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            cmd = frappe_dev.configure("rsl-taxathon-frappe-tax019", 58010, 59010)
            self.assertEqual(os.environ["FRAPPE_SOCKETIO_PORT"], "59010")
        self.assertEqual(cmd[3], "rsl-taxathon-frappe-tax019")

    def test_unrelated_projects_and_privileged_ports_are_refused(self):
        for project, http, socketio in [
            ("vati-hr-local", 58010, 59010),
            ("rsl-taxathon-frappe-X", 58010, 59010),
            ("rsl-taxathon-frappe-ok", 80, 59010),
        ]:
            with self.subTest(project=project, http=http), self.assertRaises(SystemExit):
                with mock.patch.dict(os.environ, {}, clear=False):
                    frappe_dev.configure(project, http, socketio)

    def test_ports_stay_on_loopback(self):
        for line in COMPOSE.splitlines():
            if line.strip().startswith("ports:"):
                self.assertEqual(
                    line.count("127.0.0.1:"), line.count(":8000") + line.count(":9000")
                )


class VerifySafetyTests(unittest.TestCase):
    def test_verify_site_names_are_disposable_and_unique_per_second(self):
        first = frappe_dev.verify_site_name(1_800_000_000)
        second = frappe_dev.verify_site_name(1_800_000_001)
        self.assertNotEqual(first, second)
        for name in (first, second):
            self.assertRegex(name, frappe_dev.VERIFY_SITE_RE)

    def test_in_container_guard_matches_host_naming_rule(self):
        guard = re.search(r"re\.match\(r'([^']+)'", frappe_dev.VERIFY)
        assert guard is not None
        self.assertEqual(guard.group(1), frappe_dev.VERIFY_SITE_RE.pattern)
        for protected in ["taxathon.localhost", "verify.localhost", "verify-x.localhost"]:
            with self.subTest(site=protected):
                self.assertIsNone(re.match(guard.group(1), protected))

    def test_verify_proves_two_migrations_and_never_drops_other_sites(self):
        self.assertEqual(frappe_dev.VERIFY.count("'migrate'"), 1)
        self.assertIn("for n in (1, 2):", frappe_dev.VERIFY)
        self.assertIn("if created and", frappe_dev.VERIFY)
        self.assertEqual(frappe_dev.VERIFY.count("'drop-site'"), 1)

    def test_user_throttle_is_raised_only_on_the_disposable_verify_site(self):
        # Site tests create more users than Frappe's default 60/hour limit.
        line = "cmd(['bench', '--site', site, 'set-config', '--parse', 'throttle_user_limit',"
        self.assertEqual(frappe_dev.VERIFY.count("throttle_user_limit"), 1)
        self.assertIn(line, frappe_dev.VERIFY)
        self.assertNotIn("throttle_user_limit", frappe_dev.INIT)
        self.assertNotIn("'-g'", frappe_dev.VERIFY.split("throttle_user_limit")[0].splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
