"""Behaviour tests for the reduced demo-laptop profile (TAX-095)."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
assert spec and spec.loader
demo = importlib.util.module_from_spec(spec)
sys.modules["demo"] = demo
spec.loader.exec_module(demo)

from ai.models.config import load_settings  # noqa: E402

FAKE_GROQ = "gsk_test_" + "a" * 40
FAKE_GEMINI = "gem_test_" + "b" * 40


def probe_all(status):
    return lambda url: status


def no_ports(_projects):
    return {}


def settings(**env):
    return load_settings(env, env_file=None)


def hosted():
    return settings(
        VATI_MODEL_PROVIDER="hosted", GROQ_API_KEY=FAKE_GROQ, GEMINI_API_KEY=FAKE_GEMINI
    )


class ProfileTests(unittest.TestCase):
    def test_profile_starts_only_frappe_and_jube_and_records_the_rest(self):
        self.assertEqual(set(demo.LIVE), {"frappe", "jube"})
        self.assertEqual(demo.RECORDED_SERVICES, ("docling", "openaleph", "yente"))
        self.assertFalse(set(demo.LIVE) & set(demo.RECORDED_SERVICES))

    def test_capture_refuses_live_services_and_unknown_scenarios(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                demo.capture("jube", "service-smoke", Path(tmp), probe=lambda: {})
            with self.assertRaises(SystemExit):
                demo.capture("yente", "made-up", Path(tmp), probe=lambda: {})
            # No scenario probe exists yet, so nothing may be fabricated for SYN-001.
            with self.assertRaises(SystemExit):
                demo.capture("yente", "SYN-001", Path(tmp))


class LoopbackTests(unittest.TestCase):
    def test_live_compose_files_publish_loopback_only(self):
        for service in demo.LIVE.values():
            with self.subTest(service=service.name):
                ports = demo.published_ports(service.compose_file.read_text(encoding="utf-8"))
                self.assertTrue(ports)
                self.assertEqual(demo.non_loopback(ports), [])

    def test_inline_and_block_ports_are_parsed_and_wildcards_rejected(self):
        text = (
            "services:\n  a:\n    ports: [\"127.0.0.1:1:1\", '8080:80']\n"
            '  b:\n    ports:\n      - "0.0.0.0:2:2"\n      - 127.0.0.1:3:3\n    image: x\n'
        )
        ports = demo.published_ports(text)
        self.assertEqual(ports, ["127.0.0.1:1:1", "8080:80", "0.0.0.0:2:2", "127.0.0.1:3:3"])
        self.assertEqual(demo.non_loopback(ports), ["8080:80", "0.0.0.0:2:2"])

    def test_check_loopback_refuses_to_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "c.yml"
            bad.write_text('services:\n  x:\n    ports: ["5000:5000"]\n', encoding="utf-8")
            service = demo.LiveService("x", "p", bad, None, "/", frozenset({200}))
            with self.assertRaises(SystemExit):
                demo.check_loopback([service])

    def test_runtime_ports_flag_anything_beyond_loopback(self):
        column = (
            "127.0.0.1:58000->8000/tcp, 0.0.0.0:59000->9000/tcp, [::]:59000->9000/tcp, 3306/tcp"
        )
        self.assertEqual(
            demo.runtime_exposed(column), ["0.0.0.0:59000->9000/tcp", "[::]:59000->9000/tcp"]
        )

    def test_exposed_port_makes_the_demo_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = demo.collect_status(
                probe_all(200), Path(tmp), hosted(), lambda _p: {"c": ["0.0.0.0:1->1/tcp"]}
            )
        self.assertFalse(report["ready"])
        self.assertIs(report["loopback_only"], False)


def valid_record(**overrides):
    record = demo.build_record(
        "docling", "service-smoke", {"convert_status": 200}, "cmd", "2026-11-05T10:00:00Z"
    )
    record.update(overrides)
    return record


class RecordedProvenanceTests(unittest.TestCase):
    def test_built_record_carries_provenance_and_label(self):
        record = valid_record()
        self.assertEqual(demo.validate_record(record, "docling"), [])
        self.assertIs(record["recorded"], True)
        self.assertIn("RECORDED", record["label"])
        self.assertTrue(record["service_version"])
        self.assertRegex(record["source_commit"], r"^[0-9a-f]{40}$")

    def test_tampering_and_missing_provenance_are_rejected(self):
        cases = {
            "output_sha256_mismatch": valid_record(output={"convert_status": 500}),
            "recorded_flag_not_true": valid_record(recorded=False),
            "invalid_scenario": valid_record(scenario="anything"),
            "captured_at_in_future": valid_record(captured_at="2999-01-01T00:00:00Z"),
            "captured_at_without_timezone": valid_record(captured_at="2026-11-05T10:00:00"),
            "empty_service_version": valid_record(service_version=""),
            "service_does_not_match_location": valid_record(service="yente"),
        }
        for problem, record in cases.items():
            with self.subTest(problem=problem):
                self.assertIn(problem, demo.validate_record(record, "docling"))
        missing = valid_record()
        del missing["captured_at"]
        self.assertEqual(demo.validate_record(missing), ["missing_captured_at"])

    def test_credentials_in_output_are_refused(self):
        with self.assertRaises(SystemExit):
            demo.build_record("yente", "service-smoke", {"nested": {"api_key": "x"}}, "cmd")

    def test_capture_saves_a_loadable_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = demo.capture(
                "yente", "service-smoke", Path(tmp), probe=lambda: {"alias_ok": True}
            )
            self.assertEqual(path, Path(tmp) / "yente" / "service-smoke.json")
            loaded = demo.load_recorded(Path(tmp))
        self.assertEqual(len(loaded["yente"]["captures"]), 1)
        self.assertEqual(loaded["docling"]["captures"], [])

    def test_invalid_files_are_reported_not_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "docling"
            folder.mkdir()
            (folder / "service-smoke.json").write_text(
                json.dumps(valid_record(output={"changed": True})), encoding="utf-8"
            )
            (folder / "broken.json").write_text("{", encoding="utf-8")
            status = demo.recorded_status("docling", demo.load_recorded(Path(tmp)))
        self.assertEqual(status["state"], "unavailable")
        self.assertIn("output_sha256_mismatch", status["detail"])
        self.assertIn("unreadable", status["detail"])


class HealthAggregationTests(unittest.TestCase):
    def test_nothing_running_and_nothing_recorded_is_unavailable_never_faked(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = demo.collect_status(
                probe_all("unreachable (ConnectionRefusedError)"), Path(tmp), hosted(), no_ports
            )
        states = {s["service"]: s["state"] for s in report["services"]}
        self.assertEqual(
            states,
            {
                "frappe": "unavailable",
                "jube": "unavailable",
                "ai": "live",
                "docling": "unavailable",
                "openaleph": "unavailable",
                "yente": "unavailable",
            },
        )
        self.assertFalse(report["ready"])

    def test_live_recorded_and_hosted_make_a_ready_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            for service in demo.RECORDED_SERVICES:
                demo.capture(service, "service-smoke", Path(tmp), probe=lambda: {"ok": True})
            report = demo.collect_status(probe_all(200), Path(tmp), hosted(), no_ports)
        modes = {s["service"]: (s["profile_mode"], s["state"]) for s in report["services"]}
        self.assertEqual(modes["frappe"], ("live", "live"))
        self.assertEqual(modes["docling"], ("recorded", "recorded"))
        self.assertTrue(report["ready"])
        self.assertIn("not native-UI acceptance", report["notice"])

    def test_jube_unauthenticated_swagger_counts_as_up_but_server_errors_do_not(self):
        self.assertEqual(demo.live_status(demo.LIVE["jube"], probe_all(401))["state"], "live")
        self.assertEqual(
            demo.live_status(demo.LIVE["frappe"], probe_all(502))["state"], "unavailable"
        )


class ModelLevelTests(unittest.TestCase):
    def test_default_mock_is_labelled_and_blocks_readiness(self):
        status = demo.ai_status(settings(), probe_all(200))
        self.assertEqual(status["state"], "mock")
        self.assertEqual({r["level"] for r in status["roles"].values()}, {"mock"})

    def test_hosted_without_keys_is_mock_not_live(self):
        status = demo.ai_status(settings(VATI_MODEL_PROVIDER="hosted"), probe_all(200))
        self.assertEqual(status["state"], "mock")

    def test_unreachable_local_model_falls_to_template(self):
        env = {"VATI_MODEL_PROVIDER": "local", "OLLAMA_BASE_URL": "http://127.0.0.1:11434"}
        env["VATI_MODEL_LOCAL"] = "some-model"
        down = demo.ai_status(settings(**env), probe_all("unreachable (ConnectionRefusedError)"))
        up = demo.ai_status(settings(**env), probe_all(200))
        self.assertEqual({r["level"] for r in down["roles"].values()}, {"template"})
        self.assertEqual({r["level"] for r in up["roles"].values()}, {"local"})
        self.assertEqual(down["state"], "fallback")

    def test_disabled_document_model_reports_fallback(self):
        s = settings(
            VATI_MODEL_PROVIDER="hosted",
            GROQ_API_KEY=FAKE_GROQ,
            GEMINI_API_KEY=FAKE_GEMINI,
            VATI_MODEL_DOCUMENT="none",
        )
        status = demo.ai_status(s, probe_all(200))
        self.assertEqual(status["state"], "fallback")
        self.assertEqual(status["roles"]["document"]["level"], "template")
        self.assertEqual(status["roles"]["reasoning"]["level"], "hosted")


class NoSecretsTests(unittest.TestCase):
    def test_status_outputs_show_key_presence_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = demo.collect_status(probe_all(200), Path(tmp), hosted(), no_ports)
        ai = next(s for s in report["services"] if s["service"] == "ai")
        self.assertEqual(ai["keys_present"], {"groq": True, "gemini": True})
        for rendered in (json.dumps(report), demo.render_text(report), demo.render_html(report)):
            self.assertNotIn(FAKE_GROQ, rendered)
            self.assertNotIn(FAKE_GEMINI, rendered)

    def test_html_page_escapes_details(self):
        report = {
            "checked_at": "t",
            "notice": demo.NOTICE,
            "ready": False,
            "services": [
                {"service": "x", "profile_mode": "live", "state": "unavailable", "detail": "<b>"}
            ],
        }
        self.assertIn("&lt;b&gt;", demo.render_html(report))


class MemoryTests(unittest.TestCase):
    def test_docker_stats_parsing_and_peak(self):
        first = demo.parse_stats(
            ["rsl-taxathon-jube-jube-1\t512MiB / 2GiB", "rsl-a-1\t1.5GiB / 4GiB", "junk"]
        )
        second = demo.parse_stats(
            ["rsl-taxathon-jube-jube-1\t1GiB / 2GiB", "rsl-a-1\t100MiB / 4GiB"]
        )
        summary = demo.summarise_memory([first, second])
        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["peak_total_mib"], 2048.0)
        self.assertEqual(summary["peak_by_container_mib"]["rsl-taxathon-jube-jube-1"], 1024.0)
        self.assertEqual(demo.parse_size("12.5kB"), 12500)
        self.assertIsNone(demo.parse_size("lots"))

    def test_no_samples_is_zero_not_invented(self):
        self.assertEqual(demo.summarise_memory([])["peak_total_mib"], 0)


class CliTests(unittest.TestCase):
    def test_capture_requires_a_recorded_service(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            demo.main(["capture", "frappe"])


if __name__ == "__main__":
    unittest.main()
