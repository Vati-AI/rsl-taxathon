"""Behaviour tests for Frappe developer stack isolation and clean-site verification (TAX-019)."""

import contextlib
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
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

    def test_extra_ca_bundle_is_mounted_and_trusted_without_disabling_verification(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "ca.pem"
            bundle.write_text("-----BEGIN CERTIFICATE-----\n")
            override = frappe_dev.extra_ca_override(str(bundle), local_dir=Path(tmp) / "local")
            text = override.read_text()
            self.assertIn(f"{bundle.resolve()}:{frappe_dev.CA_IN_CONTAINER}:ro", text)
            for name in frappe_dev.CA_VARIABLES:
                self.assertIn(f"{name}: {frappe_dev.CA_IN_CONTAINER}", text)
            self.assertNotIn("verify", text.lower().replace("vati-extra-ca", ""))
            with mock.patch.dict(os.environ, {"VATI_EXTRA_CA_BUNDLE": str(bundle)}):
                with mock.patch.object(frappe_dev, "ROOT", Path(tmp)):
                    cmd = frappe_dev.configure(frappe_dev.DEFAULT_PROJECT, 58000, 59000)
            self.assertEqual(
                cmd[-2:], ["-f", str(Path(tmp) / ".local/frappe-extra-ca.override.yml")]
            )
        self.assertIsNone(frappe_dev.extra_ca_override(None))
        with self.assertRaises(SystemExit):
            frappe_dev.extra_ca_override("/nonexistent/ca.pem")

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


ADAPTER_KEY = "case-adapter-" + "a" * 32
RISK_KEY = "risk-invoker-" + "b" * 32
DOCLING_KEY = "docling-" + "c" * 32
ALEPH_KEY = "aleph-" + "d" * 32


def write_local_config(root: Path, *, risk_guid: bool = True) -> None:
    """Synthetic stand-ins for the ignored files the service dev scripts generate."""
    (root / ".local").mkdir()
    (root / ".env").write_text(
        "# synthetic\nPOSTGRES_PASSWORD=x\nJUBE_BASE_URL=\nDOCLING_API_KEY=" + DOCLING_KEY + "\n"
    )
    jube = [
        "# Generated by scripts/jube_dev.py for local synthetic development only.",
        "JUBE_ADMIN_PASSWORD=synthetic-admin",
        "JUBE_PORT=55501",
        "JUBE_CASE_ADAPTER_API_KEY=" + ADAPTER_KEY,
        "JUBE_RISK_INVOKER_API_KEY=" + RISK_KEY,
    ]
    if risk_guid:
        jube.append("JUBE_RISK_MODEL_GUID=50000000-0000-4000-8000-000000000001")
    (root / ".local" / "jube.env").write_text("\n".join(jube) + "\n")
    (root / ".local" / "jube-case-workflow.json").write_text(
        json.dumps(
            {
                "workflow_version": "1.0.0",
                "vati_tenant_id": "10000000-0000-4000-8000-000000000001",
                "workflow_guid": "30000000-0000-4000-8000-000000000001",
                "status_guids": {"Received": "60000000-0000-4000-8000-000000000001"},
            }
        )
    )
    (root / ".local" / "openaleph.env").write_text(
        "OPENALEPH_PORT=55580\nOA_USER_A_API_KEY=" + ALEPH_KEY + "\nOA_COLLECTION_A_ID=3\n"
    )
    (root / ".local" / "yente.env").write_text("YENTE_UPDATE_TOKEN=t\nYENTE_PORT=55800\n")


class ServiceConfigTests(unittest.TestCase):
    """TAX-085: site service config comes only from ignored local files."""

    def test_local_files_become_container_reachable_service_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_local_config(root)
            services, missing = frappe_dev.service_config(root)
        self.assertEqual(missing, [])
        self.assertEqual(sorted(services), ["docling", "jube", "openaleph", "yente"])
        jube = services["jube"]
        self.assertEqual(jube["base_url"], "http://host.docker.internal:55501")
        self.assertEqual(jube["case_adapter_api_key"], ADAPTER_KEY)
        self.assertEqual(jube["risk_model_guid"], "50000000-0000-4000-8000-000000000001")
        self.assertEqual(jube["status_guids"], {"Received": "60000000-0000-4000-8000-000000000001"})
        self.assertEqual(services["docling"]["base_url"], "http://host.docker.internal:55001")
        self.assertEqual(services["openaleph"]["collection_id"], "3")
        self.assertEqual(
            services["yente"],
            {"base_url": "http://host.docker.internal:55800", "dataset": "vati_synthetic"},
        )

    def test_missing_local_config_leaves_services_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                frappe_dev.service_config(root),
                ({}, ["jube", "jube_risk", "docling", "openaleph", "yente"]),
            )
            write_local_config(root, risk_guid=False)
            services, missing = frappe_dev.service_config(root)
        self.assertEqual(missing, ["jube_risk"])
        self.assertNotIn("risk_invoker_api_key", services["jube"])

    def test_service_host_is_configurable_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_local_config(root)
            services, _ = frappe_dev.service_config(root, "gateway.local")
            self.assertEqual(services["jube"]["base_url"], "http://gateway.local:55501")
            for bad in ("x/../y", "host:1", "Host With Space", ""):
                with self.subTest(host=bad), self.assertRaises(SystemExit):
                    frappe_dev.service_config(root, bad)
        self.assertEqual(
            frappe_dev.container_url("https://jube.example.test/", "host.docker.internal"),
            "https://jube.example.test",
        )

    def test_secrets_are_not_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_local_config(root)
            out = io.StringIO()
            with mock.patch.object(frappe_dev, "ROOT", root), contextlib.redirect_stdout(out):
                env = frappe_dev.service_env("host.docker.internal")
        self.assertIn(ADAPTER_KEY, env["VATI_SERVICES_JSON"])
        for secret in (ADAPTER_KEY, RISK_KEY, DOCLING_KEY, ALEPH_KEY):
            self.assertNotIn(secret, out.getvalue())
        self.assertIn("docling, jube, openaleph, yente", out.getvalue())

    def test_site_factory_names_match_the_app(self):
        sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
        from vati_tax.integrations import config

        self.assertEqual(frappe_dev.SITE_FACTORIES, config.SITE_FACTORIES)


class ApplyServicesTests(unittest.TestCase):
    """The in-container site_config writer, run against a temporary site directory."""

    def apply(self, site: Path, services: dict) -> list:
        env = {
            "VATI_SERVICES_JSON": json.dumps(services),
            "VATI_SITE_FACTORIES": json.dumps(frappe_dev.SITE_FACTORIES),
        }
        scope: dict = {}
        with mock.patch.dict(os.environ, env):
            exec(frappe_dev.APPLY_SERVICES, scope)
            return scope["apply_services"](site)

    def test_writes_idempotently_keeps_other_keys_and_removes_stale_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            path = site / "site_config.json"
            path.write_text(json.dumps({"db_name": "synthetic", "vati_services": {"old": {}}}))
            services = {
                "jube": {"base_url": "http://host.docker.internal:55501", "risk_model_guid": "g"}
            }
            self.assertEqual(self.apply(site, services), ["jube"])
            first = path.read_text()
            self.assertEqual(self.apply(site, services), ["jube"])
            self.assertEqual(path.read_text(), first)
            conf = json.loads(first)
            self.assertEqual(conf["db_name"], "synthetic")
            self.assertEqual(conf["vati_services"], services)
            self.assertEqual(
                conf["vati_case_lifecycle"], frappe_dev.SITE_FACTORIES["vati_case_lifecycle"]
            )
            self.assertEqual(
                conf["vati_risk_evaluator"], frappe_dev.SITE_FACTORIES["vati_risk_evaluator"]
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            # Jube removed from local config: the site no longer names a factory, so the
            # decision store reports jube_not_configured instead of using stale secrets.
            self.assertEqual(self.apply(site, {}), [])
            conf = json.loads(path.read_text())
            for key in ("vati_services", "vati_case_lifecycle", "vati_risk_evaluator"):
                self.assertNotIn(key, conf)
            self.assertEqual(conf["db_name"], "synthetic")

    def test_risk_factory_needs_the_model_guid(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self.apply(site, {"jube": {"base_url": "http://host.docker.internal:55501"}})
            conf = json.loads((site / "site_config.json").read_text())
        self.assertIn("vati_case_lifecycle", conf)
        self.assertNotIn("vati_risk_evaluator", conf)


class AdapterPackagingTests(unittest.TestCase):
    def test_integration_is_mounted_read_only_next_to_ai(self):
        self.assertIn("- ./integration:/workspace/vati_shared/integration:ro", COMPOSE)
        self.assertIn("- ./ai:/workspace/vati_shared/ai:ro", COMPOSE)
        self.assertIn('extra_hosts: ["host.docker.internal:host-gateway"]', COMPOSE)

    def test_path_file_lists_existing_stdlib_adapter_directories_only(self):
        self.assertEqual(frappe_dev.SHARED_PATHS[0], "/workspace/vati_shared")
        for path in frappe_dev.SHARED_PATHS[1:]:
            local = ROOT / path.removeprefix("/workspace/vati_shared/")
            with self.subTest(path=path):
                self.assertTrue(local.is_dir())
                for module in local.glob("*.py"):
                    for line in module.read_text(encoding="utf-8").splitlines():
                        if line.startswith(("import ", "from ")) and "__future__" not in line:
                            top = line.split()[1].split(".")[0]
                            siblings = {p.stem for p in local.glob("*.py")}
                            self.assertTrue(
                                top in sys.stdlib_module_names or top in siblings,
                                f"{module.name} imports {top}",
                            )
        self.assertIn("VATI_SHARED_PATHS", frappe_dev.INIT)
        self.assertIn("vati_shared.pth", frappe_dev.INIT)

    def test_init_and_live_verify_apply_services_but_plain_verify_does_not(self):
        self.assertIn("configured=apply_services(site)", frappe_dev.INIT)
        self.assertEqual(frappe_dev.VERIFY.count("apply_services(root / 'sites' / site)"), 1)
        live = frappe_dev.VERIFY.split("if os.environ.get('VERIFY_LIVE') == '1':")[1]
        self.assertIn("apply_services(root / 'sites' / site)", live)
        # The live check runs after the site tests, so they never see service config.
        self.assertLess(
            frappe_dev.VERIFY.index("run-tests"), frappe_dev.VERIFY.index("live_check.run")
        )


if __name__ == "__main__":
    unittest.main()
