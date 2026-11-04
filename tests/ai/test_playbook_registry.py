"""TAX-061 versioned tool/playbook registry: approval, digests, operations, replay."""

import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = ROOT / "ai/workflows/playbook_registry.synthetic.json"
RECORDS = json.loads((ROOT / "data/synthetic/tax024_records.json").read_text())
TENANT = "tenant-synthetic"
HAS_SQLITE_SAVER = (
    importlib.util.find_spec("langgraph") is not None
    and importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
)


@unittest.skipUnless(HAS_SQLITE_SAVER, "LangGraph SQLite checkpointer not installed")
class PlaybookRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        from ai.workflows import playbook_registry as pr
        from ai.workflows.fake_graph import create_investigation_state
        from ai.workflows.refund_lanes import records_source

        self.pr = pr
        self.doc = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        self.records = {(TENANT, r["case_id"]): copy.deepcopy(r) for r in RECORDS}
        self.source = records_source(self.records)
        self.create_state = create_investigation_state
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def registry(self, doc=None, root: Path = ROOT):
        return self.pr.parse_registry(doc if doc is not None else self.doc, root=root)

    def entry(self, doc, kind: str, name: str) -> dict:
        return next(e for e in doc["entries"] if e["kind"] == kind and e["name"] == name)

    def state(self, case_id: str, run_id: str = "run-1"):
        return self.create_state(tenant_id=TENANT, case_id=case_id, run_id=run_id)

    # --- registry contents and policy -----------------------------------------

    def test_shipped_registry_pins_every_approved_version_to_current_source(self) -> None:
        registry = self.pr.load_registry()
        names = {(e.kind, e.name) for e in registry.entries()}
        self.assertTrue(
            {
                ("playbook", "refund_lanes"),
                ("playbook", "imported_services"),
                ("playbook", "related_party"),
                ("playbook", "work_arrangement"),
                ("tool", "policy_gap_gate"),
            }
            <= names
        )
        for entry in registry.entries():
            with self.subTest(entry=entry.key):
                self.assertEqual(registry.require_approved(*entry.key), entry)
                self.assertTrue({"case_scoped", "advisory_only"} <= entry.provenance)
                self.assertIsNotNone(entry.approved_by)
                self.assertNotEqual(entry.approved_by, entry.proposed_by)

    def test_registry_policy_violations_are_refused_at_load(self) -> None:
        def mutate(change):
            doc = copy.deepcopy(self.doc)
            change(doc)
            return doc

        cases = {
            "not synthetic": lambda d: d.__setitem__("synthetic", False),
            "real-looking approver": lambda d: self.entry(d, "tool", "policy_gap_gate").__setitem__(
                "approved_by", "jane.supervisor"
            ),
            "self approval": lambda d: self.entry(d, "tool", "policy_gap_gate").__setitem__(
                "proposed_by", "SYN-SUPERVISOR-01"
            ),
            "unknown operation": lambda d: self.entry(d, "tool", "policy_gap_gate")[
                "allowed_operations"
            ].append("send_email"),
            "action without checkpoint": lambda d: self.entry(d, "playbook", "related_party")[
                "allowed_operations"
            ].append("apply_approved_action"),
            "source outside ai": lambda d: self.entry(d, "tool", "policy_gap_gate")[
                "source_files"
            ].append("scripts/dev.py"),
            "unknown builder": lambda d: self.entry(d, "playbook", "related_party").__setitem__(
                "name", "shell_playbook"
            ),
            "unknown tool reference": lambda d: self.entry(
                d, "playbook", "related_party"
            ).__setitem__("tools", ["missing_tool@1.0"]),
            "proposed with approver": lambda d: self.entry(
                d, "tool", "policy_gap_gate"
            ).__setitem__("status", "proposed"),
            "two approved versions": lambda d: d["entries"].append(
                {**self.entry(d, "tool", "policy_gap_gate"), "version": "1.1"}
            ),
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(self.pr.RegistryError):
                    self.registry(mutate(change))

    # --- rejection of unapproved versions --------------------------------------

    def test_unregistered_proposed_and_retired_versions_are_rejected(self) -> None:
        doc = copy.deepcopy(self.doc)
        approved = self.entry(doc, "playbook", "related_party")
        doc["entries"].append(
            {
                **approved,
                "version": "2.0",
                "status": "proposed",
                "approved_by": None,
                "approved_on": None,
            }
        )
        doc["entries"].append(
            {
                **approved,
                "version": "0.9",
                "status": "retired",
                "approved_by": None,
                "approved_on": None,
            }
        )
        registry = self.registry(doc)
        for version, code in (
            ("3.0", "version_not_registered"),
            ("2.0", "version_proposed"),
            ("0.9", "version_retired"),
        ):
            with self.subTest(version=version):
                with self.assertRaises(self.pr.PlaybookRejected) as raised:
                    self.pr.run_playbook(
                        registry, "related_party", version, self.source, self.state("SYN-002")
                    )
                self.assertEqual(raised.exception.code, code)

    def test_changed_source_makes_the_approved_version_unapproved(self) -> None:
        root = Path(self.tmp.name)
        for entry in self.doc["entries"]:
            for name in entry["source_files"]:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, root / name)
        registry = self.registry(root=root)
        registry.require_approved("playbook", "related_party", "1.0")
        target = root / "ai/tools/related_party.py"
        target.write_text(target.read_text() + "\n# unreviewed edit\n")
        for kind, name in (("tool", "related_party_review_tool"), ("playbook", "related_party")):
            with self.subTest(name=name):
                with self.assertRaises(self.pr.PlaybookRejected) as raised:
                    registry.require_approved(kind, name, "1.0")
                self.assertEqual(raised.exception.code, "source_digest_mismatch")
        # Unrelated playbooks stay approved.
        registry.require_approved("playbook", "imported_services", "1.0")

    def test_playbook_with_unapproved_tool_is_rejected(self) -> None:
        doc = copy.deepcopy(self.doc)
        tool = self.entry(doc, "tool", "related_party_review_tool")
        tool.update(status="proposed", approved_by=None, approved_on=None)
        with self.assertRaises(self.pr.PlaybookRejected) as raised:
            self.registry(doc).require_approved("playbook", "related_party", "1.0")
        self.assertEqual(raised.exception.code, "version_proposed")

    def test_tampered_digest_is_rejected(self) -> None:
        doc = copy.deepcopy(self.doc)
        self.entry(doc, "playbook", "refund_lanes")["content_digest"] = "0" * 64
        with self.assertRaises(self.pr.PlaybookRejected) as raised:
            self.registry(doc).require_approved("playbook", "refund_lanes", "1.0")
        self.assertEqual(raised.exception.code, "source_digest_mismatch")

    def test_line_endings_do_not_change_the_digest(self) -> None:
        root = Path(self.tmp.name)
        name = "ai/tools/related_party.py"
        (root / name).parent.mkdir(parents=True)
        text = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        (root / name).write_bytes(text.replace(b"\n", b"\r\n"))
        self.assertEqual(self.pr.source_digest((name,), root), self.pr.source_digest((name,), ROOT))

    # --- approved runs, provenance and replay ----------------------------------

    def test_approved_playbook_runs_with_provenance_stamp(self) -> None:
        registry = self.pr.load_registry()
        final, record = self.pr.run_playbook(
            registry, "related_party", "1.0", self.source, self.state("SYN-002")
        )
        (finding,) = final["findings"]
        stamp = finding["provenance"]
        entry = registry.require_approved("playbook", "related_party", "1.0")
        self.assertEqual(stamp["name"], "related_party")
        self.assertEqual(stamp["version"], "1.0")
        self.assertEqual(stamp["content_digest"], entry.content_digest)
        self.assertEqual(stamp["registry_version"], registry.registry_version)
        self.assertEqual(record["content_digest"], entry.content_digest)
        self.assertEqual(record["case_id"], "SYN-002")

    def test_known_approved_version_replays_to_the_same_output(self) -> None:
        registry = self.pr.load_registry()
        for name, case_id in (
            ("refund_lanes", "SYN-001"),
            ("imported_services", "SYN-004"),
            ("related_party", "SYN-002"),
        ):
            with self.subTest(name=name):
                _, record = self.pr.run_playbook(
                    registry, name, "1.0", self.source, self.state(case_id)
                )
                replay = self.pr.replay_run(registry, record, self.source, self.state(case_id))
                self.assertEqual(replay["status"], "replay_matched")
                self.assertEqual(replay["output_digest"], record["output_digest"])

    def test_replay_detects_changed_inputs_and_records(self) -> None:
        registry = self.pr.load_registry()
        _, record = self.pr.run_playbook(
            registry, "related_party", "1.0", self.source, self.state("SYN-002")
        )
        with self.assertRaises(self.pr.PlaybookRejected) as raised:
            self.pr.replay_run(registry, record, self.source, self.state("SYN-002", run_id="run-2"))
        self.assertEqual(raised.exception.code, "input_mismatch")
        with self.assertRaises(self.pr.PlaybookRejected) as raised:
            self.pr.replay_run(
                registry, {**record, "content_digest": "f" * 64}, self.source, self.state("SYN-002")
            )
        self.assertEqual(raised.exception.code, "source_digest_mismatch")
        # Same version, same input, but the case evidence changed underneath: diverges.
        self.records[(TENANT, "SYN-002")]["payments"][0]["amount"] = "87199.99"
        replay = self.pr.replay_run(registry, record, self.source, self.state("SYN-002"))
        self.assertEqual(replay["status"], "replay_diverged")

    def test_checkpointed_playbook_runs_through_the_registry(self) -> None:
        from ai.workflows.human_checkpoints import open_checkpointer, thread_config

        saver = open_checkpointer(Path(self.tmp.name) / "cp.sqlite")
        try:
            state = {**self.state("SYN-003"), "case_version": 2}
            final, _ = self.pr.run_playbook(
                self.pr.load_registry(),
                "work_arrangement",
                "1.0",
                self.source,
                state,
                config=thread_config(TENANT, "SYN-003", "run-1"),
                build_options={"checkpointer": saver, "agent_id": "agent:tax061"},
            )
            self.assertTrue(final["__interrupt__"])
            self.assertEqual(final["findings"][0]["provenance"]["name"], "work_arrangement")
        finally:
            saver.conn.close()


if __name__ == "__main__":
    unittest.main()
