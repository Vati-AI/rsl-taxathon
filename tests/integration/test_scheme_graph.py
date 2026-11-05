"""Behaviour tests for the scheme, beneficial ownership and intermediary graph (TAX-074).

Inputs come from data/synthetic/tax074_scheme_graph.json: two cases of tenant A (X and Y share
an adviser and a scheme) and a copy of case X under tenant B with the same record IDs.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "followthemoney"))
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))

import scheme_graph as sg  # noqa: E402
import tax_mapping as tm  # noqa: E402

FIXTURE = ROOT / "data" / "synthetic" / "tax074_scheme_graph.json"
INPUTS = json.loads(FIXTURE.read_text(encoding="utf-8"))["inputs"]
CASE_X, CASE_Y, CASE_Z = (copy.deepcopy(i) for i in INPUTS)
TENANT_A, TENANT_B = CASE_X["tenant_id"], CASE_Z["tenant_id"]


def node(data: dict[str, Any], record_id: str, kind: str = "party") -> str:
    return tm.entity_id(data["tenant_id"], kind, record_id)


def scope(data: dict[str, Any], role: str = "auditor") -> sg.GraphScope:
    return sg.GraphScope(data["tenant_id"], data["case_id"], role)


def store(*inputs: dict[str, Any]) -> sg.SchemeGraphStore:
    s = sg.SchemeGraphStore()
    for data in inputs or (CASE_X, CASE_Y, CASE_Z):
        s.ingest(data)
    return s


def edges_by_record(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["provenance"]["source"]["record_id"]: e for e in graph["edges"].values()}


def walk(value: Any, path: str = ""):
    """Yield (path, key-or-string) for every key and string value."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from walk(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


class InputMixin:
    def refused(self, data: dict[str, Any], reason: str) -> sg.GraphInputError:
        with self.assertRaises(sg.GraphInputError) as ctx:  # type: ignore[attr-defined]
            sg.build_case_graph(data)
        self.assertEqual(ctx.exception.reason, reason)  # type: ignore[attr-defined]
        return ctx.exception


class ModelTests(InputMixin, unittest.TestCase):
    def test_new_records_map_to_locked_ftm_schemata(self) -> None:
        mapper, _ = sg.map_graph_input(CASE_X)
        counts: dict[str, int] = {}
        for e in mapper.entities:
            counts[e["schema"]] = counts.get(e["schema"], 0) + 1
        self.assertEqual(counts["Directorship"], 2)
        self.assertEqual(counts["Representation"], 2)
        self.assertEqual(counts["Project"], 1)
        self.assertEqual(counts["ProjectParticipant"], 3)
        self.assertEqual(counts["Ownership"], 5)
        tm.validate_entities(mapper.entities)  # ranges and required properties hold

    def test_legal_beneficial_director_adviser_and_intermediary_are_separate(self) -> None:
        graph = sg.build_case_graph(CASE_X)
        by = edges_by_record(graph)
        expect = {
            "OWN-X-1": ("legal_ownership", "HOLD-1", "TP-X"),
            "OWN-X-2": ("beneficial_ownership", "P-1", "TP-X"),
            "DIR-X-1": ("directorship", "P-2", "TP-X"),
            "REP-X-1": ("adviser_to", "ADV-1", "TP-X"),
            "REP-X-2": ("intermediary_for", "NOM-1", "P-1"),
        }
        for record, (kind, source, target) in expect.items():
            with self.subTest(record=record):
                self.assertEqual(by[record]["type"], kind)
                self.assertEqual(by[record]["source"], node(CASE_X, source))
                self.assertEqual(by[record]["target"], node(CASE_X, target))
        self.assertEqual(by["REP-X-2"]["role"], "nominee_shareholder")
        # P-1 is both a beneficial owner of TP-X and a director of HOLD-1: two edges, two roles.
        p1 = [e["type"] for e in graph["edges"].values() if e["source"] == node(CASE_X, "P-1")]
        self.assertEqual(sorted(set(p1)), ["beneficial_ownership", "directorship"])

    def test_ownership_type_is_never_guessed(self) -> None:
        data = copy.deepcopy(CASE_X)
        own = data["entity_bundle"]["ownership"]
        del own[3]["ownership_type"]  # OWN-X-4 states no type
        own[4]["ownership_type"] = "indirect"
        own[1]["ownership_type"] = "ultimate"
        by = edges_by_record(sg.build_case_graph(data))
        self.assertEqual(by["OWN-X-4"]["type"], "ownership_type_unstated")
        self.assertIsNone(by["OWN-X-4"]["ownership_type"])
        self.assertEqual(by["OWN-X-5"]["type"], "indirect_ownership")
        self.assertEqual(by["OWN-X-2"]["type"], "beneficial_ownership")
        self.assertEqual(by["OWN-X-2"]["ownership_type"], "ultimate")

    def test_scheme_is_a_grouping_object_linked_to_the_case_with_evidence(self) -> None:
        graph = sg.build_case_graph(CASE_X)
        scheme = node(CASE_X, "SCH-1", "scheme")
        self.assertEqual(graph["nodes"][scheme]["type"], "scheme")
        link = edges_by_record(graph)["LINK-X-1"]
        self.assertEqual((link["source"], link["target"]), (graph["case_node_id"], scheme))
        self.assertEqual(link["evidence_status"], "cited")
        members = sorted(
            graph["nodes"][e["source"]]["record_id"]
            for e in graph["edges"].values()
            if e["type"] == "scheme_participation" and e["target"] == scheme
        )
        self.assertEqual(members, ["ADV-1", "MGMT-1", "TP-X"])
        subject = [e for e in graph["edges"].values() if e["type"] == "case_subject"]
        self.assertEqual([e["target"] for e in subject], [node(CASE_X, "TP-X")])

    def test_scheme_links_and_participation_need_evidence(self) -> None:
        for key in ("case_scheme_links", "schemes", "scheme_participants"):
            with self.subTest(key=key):
                data = copy.deepcopy(CASE_X)
                data[key][0]["evidence"] = []
                self.refused(data, "missing_evidence")
        data = copy.deepcopy(CASE_X)
        data["case_scheme_links"][0]["scheme_id"] = "SCH-404"
        self.refused(data, "unknown_scheme")

    def test_relationship_records_are_checked(self) -> None:
        cases = [
            ("directorships", "organization_id", "P-1", "invalid_directorship_organization"),
            ("directorships", "director_id", "NOBODY", "unknown_party"),
            ("directorships", "role", "shadow_boss", "invalid"),
            ("representations", "client_id", "ADV-1", "self_relationship"),
            ("representations", "end_date", "2020-01-01", "invalid_date"),
        ]
        for key, field, value, reason in cases:
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(CASE_X)
                data[key][0][field] = value
                self.refused(data, reason)


class ContradictionTests(InputMixin, unittest.TestCase):
    def test_conflicting_sources_stay_separate_provenance_backed_edges(self) -> None:
        graph = sg.build_case_graph(CASE_X)
        by = edges_by_record(graph)
        declared, registered = by["OWN-X-2"], by["OWN-X-3"]
        self.assertNotEqual(declared["edge_id"], registered["edge_id"])
        self.assertEqual((declared["percentage"], registered["percentage"]), ("60", "75"))
        bundle_ev = {e["evidence_id"]: e for e in CASE_X["entity_bundle"]["evidence"]}
        for edge, file_name, extraction in (
            (declared, "bo-declaration.pdf", {"extraction_id": "EXT-DECL-1", "revision": 1}),
            (registered, "registry-extract.csv", None),
        ):
            [cite] = edge["provenance"]["evidence"]
            self.assertEqual(bundle_ev[cite["evidence_id"]]["file_name"], file_name)
            self.assertEqual(
                cite["document_sha256"], bundle_ev[cite["evidence_id"]]["document_sha256"]
            )
            self.assertEqual(cite["extraction"], extraction)
            self.assertEqual(edge["provenance"]["ftm_schema"], "Ownership")
            self.assertRegex(edge["provenance"]["entity_digest"], r"^[0-9a-f]{64}$")

        result = store(CASE_X).neighbourhood(scope(CASE_X), node(CASE_X, "TP-X"), max_depth=1)
        [conflict] = result["data"]["contradictions"]
        self.assertEqual(conflict["reason"], "disagreeing_ownership_records")
        self.assertEqual(conflict["status"], "unresolved")
        self.assertEqual(conflict["percentages"], ["60", "75"])
        self.assertEqual(
            sorted(conflict["edge_ids"]), sorted([declared["edge_id"], registered["edge_id"]])
        )
        # The legal owner's 100% does not contradict beneficial ownership.
        self.assertNotIn(by["OWN-X-1"]["edge_id"], conflict["edge_ids"])

    def test_beneficial_stakes_over_100_percent_are_listed(self) -> None:
        data = copy.deepcopy(CASE_X)
        data["entity_bundle"]["ownership"][2]["owner_id"] = "MGMT-1"  # OWN-X-3: another owner
        graph = sg.build_case_graph(data)
        found = sg.contradictions(graph["edges"].values())
        [over] = [c for c in found if c["reason"] == "beneficial_ownership_exceeds_100_percent"]
        self.assertEqual(over["record_ids"], ["OWN-X-2", "OWN-X-3", "OWN-X-4"])
        self.assertEqual(over["total_percentage"], "175")
        # In the fixture P-1's two records disagree; counted once (60 + 40) they fit in 100%.
        reasons = [
            c["reason"] for c in sg.contradictions(sg.build_case_graph(CASE_X)["edges"].values())
        ]
        self.assertEqual(reasons, ["disagreeing_ownership_records"])

    def test_truncation_never_hides_the_other_side_of_a_contradiction(self) -> None:
        graph = sg.build_case_graph(CASE_X)
        one = edges_by_record(graph)["OWN-X-2"]["edge_id"]
        [conflict] = sg._touching(graph, {one})
        self.assertEqual(
            conflict["edge_ids_not_returned"], [edges_by_record(graph)["OWN-X-3"]["edge_id"]]
        )

    def test_a_changed_record_is_refused_and_a_new_source_is_added_alongside(self) -> None:
        s = store(CASE_X)
        before = s.neighbourhood(scope(CASE_X), max_depth=4)
        changed = copy.deepcopy(CASE_X)
        changed["entity_bundle"]["ownership"][1]["percentage"] = "65"
        with self.assertRaises(sg.GraphInputError) as ctx:
            s.ingest(changed)
        self.assertEqual(ctx.exception.code, "idempotency_conflict")
        self.assertEqual(s.neighbourhood(scope(CASE_X), max_depth=4), before)

        added = copy.deepcopy(CASE_X)
        extra = copy.deepcopy(added["entity_bundle"]["ownership"][1])
        extra.update(id="OWN-X-6", percentage="65", source=tm_src("ownership", "OWN-X-6"))
        added["entity_bundle"]["ownership"].append(extra)
        self.assertEqual(s.ingest(added)["status"], "applied")
        edges = s.neighbourhood(scope(CASE_X), node(CASE_X, "TP-X"), max_depth=1)["data"]["edges"]
        p1 = sorted(
            e["percentage"]
            for e in edges
            if e["source"] == node(CASE_X, "P-1") and e["type"] == "beneficial_ownership"
        )
        self.assertEqual(p1, ["60", "65", "75"])


def tm_src(kind: str, rid: str) -> dict[str, str]:
    return {"system": "synthetic_fixture", "record_type": kind, "record_id": rid}


class NoGuiltByAssociationTests(InputMixin, unittest.TestCase):
    def all_outputs(self, s: sg.SchemeGraphStore) -> list[dict[str, Any]]:
        out = []
        for data in (CASE_X, CASE_Y, CASE_Z):
            graph = sg.build_case_graph(data)
            out.append(graph)
            for n in graph["nodes"]:
                out.append(s.neighbourhood(scope(data), n, max_depth=4, max_nodes=200))
                out.append(s.connections(scope(data), graph["case_node_id"], n, max_paths=25))
        return out

    def test_no_output_carries_a_labelling_word_or_label_field(self) -> None:
        s = store()
        for output in self.all_outputs(s):
            for where, text in walk(output):
                self.assertEqual(sg.labelling_terms(text), [], f"{where}: {text}")
                self.assertNotIn(text, sg.LABEL_FIELDS, where)

    def test_a_flag_on_an_input_party_does_not_propagate(self) -> None:
        flagged = copy.deepcopy(CASE_X)
        p1 = next(p for p in flagged["entity_bundle"]["parties"] if p["id"] == "P-1")
        p1.update(risk_flag="high risk", fraud_score="0.97", labels=["suspicious"])
        # The TAX-036 mapper ignores fields it does not know, so the graph is identical.
        self.assertEqual(sg.build_case_graph(flagged), sg.build_case_graph(CASE_X))
        s = store(flagged)
        around = s.neighbourhood(scope(flagged), node(flagged, "P-1"), max_depth=4)
        text = json.dumps(around)
        for word in ("risk", "fraud", "suspicious", "0.97", "flag"):
            self.assertNotIn(word, text)
        for n in around["data"]["nodes"]:
            self.assertEqual(
                set(n) - {"depth"},
                set(sg.build_case_graph(CASE_X)["nodes"][n["node_id"]]),
            )

    def test_labelling_fields_and_wording_are_refused(self) -> None:
        cases = [
            ("directorships", "flags", ["watch"], "risk_label_not_allowed"),
            ("representations", "red_flag", True, "risk_label_not_allowed"),
            ("scheme_participants", "risk_level", "high", "risk_label_not_allowed"),
            ("representations", "comment", "neutral", "unexpected_field"),
            ("schemes", "name", "Fraud scheme 2025", "risk_label_not_allowed"),
            ("schemes", "description", "A high-risk arrangement", "risk_label_not_allowed"),
            ("schemes", "description", "Possible sham structure", "risk_label_not_allowed"),
        ]
        for key, field, value, reason in cases:
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(CASE_X)
                data[key][0][field] = value
                self.refused(data, reason)
        data = copy.deepcopy(CASE_X)
        data["suspect_parties"] = ["P-1"]
        self.refused(data, "risk_label_not_allowed")

    def test_neutral_words_containing_label_letters_pass(self) -> None:
        self.assertEqual(sg.labelling_terms("Respect for asterisk shellfish exports"), [])
        self.assertEqual(sg.labelling_terms("RED-FLAG"), ["flag", "redflag"])

    def test_connections_are_descriptive_and_go_to_officer_review(self) -> None:
        s = store(CASE_X)
        result = s.connections(scope(CASE_X), node(CASE_X, "P-2"), node(CASE_X, "NOM-1"))
        paths = result["data"]["paths"]
        self.assertTrue(paths)
        shortest = paths[0]
        self.assertEqual(shortest["length"], 3)  # P-2 directs TP-X <- HOLD-1 <- NOM-1
        self.assertEqual([p["length"] for p in paths], sorted(p["length"] for p in paths))
        for path in paths:
            self.assertIsNone(path["interpretation"])
            for step in path["steps"]:
                self.assertIn("is recorded as", step["description"])
                self.assertIn(step["provenance"]["source"]["record_id"], step["description"])
            self.assertTrue(path["evidence_ids"])
        self.assertEqual(
            result["meta"]["review_route"],
            {"action": "finding.review", "decided_by": "officer", "interpretation": None},
        )


class ScopeTests(unittest.TestCase):
    def test_a_case_never_sees_what_only_another_case_recorded(self) -> None:
        s = store()
        adviser, scheme = node(CASE_X, "ADV-1"), node(CASE_X, "SCH-1", "scheme")
        self.assertEqual(adviser, node(CASE_Y, "ADV-1"))  # same tenant: shared nodes
        only_y = {node(CASE_Y, "TP-Y"), node(CASE_Y, "P-9")}
        only_x = {node(CASE_X, r) for r in ("TP-X", "HOLD-1", "P-1", "P-2", "NOM-1", "MGMT-1")}
        for start in (adviser, scheme, None):
            with self.subTest(start=start):
                x = s.neighbourhood(scope(CASE_X), start, max_depth=4, max_nodes=200)["data"]
                ids = {n["node_id"] for n in x["nodes"]}
                self.assertFalse(ids & only_y)
                for e in x["edges"]:
                    self.assertTrue(e["provenance"]["source"]["record_id"].find("-Y-") < 0)
                y = s.neighbourhood(scope(CASE_Y), start, max_depth=4, max_nodes=200)["data"]
                self.assertFalse({n["node_id"] for n in y["nodes"]} & only_x)
        with self.assertRaises(sg.GraphAccessError) as ctx:
            s.connections(scope(CASE_X), adviser, node(CASE_Y, "TP-Y"))
        self.assertEqual(ctx.exception.code, "not_found")

    def test_a_tenant_never_sees_another_tenant(self) -> None:
        s = store()
        self.assertNotEqual(node(CASE_X, "TP-X"), node(CASE_Z, "TP-X"))
        for tenant, case in ((TENANT_A, CASE_Z["case_id"]), (TENANT_B, CASE_X["case_id"])):
            with self.subTest(tenant=tenant, case=case):
                with self.assertRaises(sg.GraphAccessError) as ctx:
                    s.neighbourhood(sg.GraphScope(tenant, case, "supervisor"))
                self.assertEqual(ctx.exception.code, "not_found")
        with self.assertRaises(sg.GraphAccessError) as ctx:
            s.neighbourhood(scope(CASE_X), node(CASE_Z, "TP-X"))
        self.assertEqual(ctx.exception.code, "not_found")
        x = s.neighbourhood(scope(CASE_X), max_depth=4, max_nodes=200)
        self.assertNotIn(TENANT_B, json.dumps(x))

    def test_scheme_cases_only_lists_readable_cases_of_the_tenant(self) -> None:
        s = store()
        scheme = node(CASE_X, "SCH-1", "scheme")
        all_cases = [CASE_X["case_id"], CASE_Y["case_id"], CASE_Z["case_id"]]
        self.assertEqual(
            s.scheme_cases(TENANT_A, "auditor", scheme, all_cases),
            sorted([CASE_X["case_id"], CASE_Y["case_id"]]),
        )
        self.assertEqual(
            s.scheme_cases(TENANT_A, "auditor", scheme, [CASE_X["case_id"]]),
            [CASE_X["case_id"]],
        )
        self.assertEqual(s.scheme_cases(TENANT_B, "auditor", scheme, all_cases), [])
        with self.assertRaises(sg.GraphAccessError):
            s.scheme_cases(TENANT_A, "taxpayer", scheme, all_cases)

    def test_only_case_readers_may_query(self) -> None:
        s = store(CASE_X)
        for role in ("taxpayer", "", "admin"):
            with self.subTest(role=role):
                with self.assertRaises(sg.GraphAccessError) as ctx:
                    s.neighbourhood(scope(CASE_X, role))
                self.assertEqual(ctx.exception.code, "forbidden")

    def test_read_roles_match_the_identity_policy(self) -> None:
        try:
            from vati_tax.identity import policy
        except ImportError:  # pragma: no cover - backend package is optional here
            self.skipTest("vati_tax backend package not importable")
        self.assertEqual(sg.GRAPH_READ_ROLES, set(policy.ACTION_ROLES[policy.CASE_READ]))


class BoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = store(CASE_X)

    def test_depth_and_node_limits_report_truncation(self) -> None:
        full = self.s.neighbourhood(scope(CASE_X), max_depth=4, max_nodes=200)
        self.assertFalse(full["meta"]["is_truncated"])
        shallow = self.s.neighbourhood(scope(CASE_X), max_depth=1)
        self.assertTrue(shallow["meta"]["truncated"]["depth"])
        self.assertEqual({n["depth"] for n in shallow["data"]["nodes"]}, {0, 1})
        small = self.s.neighbourhood(scope(CASE_X), max_depth=4, max_nodes=3)
        self.assertTrue(small["meta"]["truncated"]["nodes"])
        self.assertEqual(len(small["data"]["nodes"]), 3)
        returned = {n["node_id"] for n in small["data"]["nodes"]}
        for e in small["data"]["edges"]:
            self.assertTrue({e["source"], e["target"]} <= returned)

    def test_path_limits_report_truncation(self) -> None:
        a, b = node(CASE_X, "P-2"), node(CASE_X, "NOM-1")
        one = self.s.connections(scope(CASE_X), a, b, max_paths=1)
        self.assertEqual(len(one["data"]["paths"]), 1)
        self.assertTrue(one["meta"]["truncated"]["paths"])
        near = self.s.connections(scope(CASE_X), a, b, max_depth=2)
        self.assertEqual(near["data"]["paths"], [])
        self.assertTrue(near["meta"]["truncated"]["depth"])

    def test_limits_outside_the_allowed_range_are_refused(self) -> None:
        for kwargs in ({"max_depth": 0}, {"max_depth": 5}, {"max_nodes": 201}, {"max_depth": True}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(sg.GraphInputError) as ctx:
                    self.s.neighbourhood(scope(CASE_X), **kwargs)
                self.assertEqual(ctx.exception.reason, "invalid_limit")
        with self.assertRaises(sg.GraphInputError):
            self.s.connections(scope(CASE_X), None, None, max_paths=26)  # type: ignore[arg-type]


class DeterminismTests(InputMixin, unittest.TestCase):
    def test_same_input_same_graph_regardless_of_record_order(self) -> None:
        first = json.dumps(sg.build_case_graph(CASE_X), sort_keys=True)
        shuffled = copy.deepcopy(CASE_X)
        for key in ("directorships", "representations", "scheme_participants"):
            shuffled[key].reverse()
        shuffled["entity_bundle"]["ownership"].reverse()
        shuffled["entity_bundle"]["parties"].reverse()
        self.assertEqual(json.dumps(sg.build_case_graph(shuffled), sort_keys=True), first)
        self.assertEqual(json.dumps(sg.build_case_graph(CASE_X), sort_keys=True), first)

    def test_replay_is_a_no_op(self) -> None:
        s = sg.SchemeGraphStore()
        applied = s.ingest(CASE_X)
        before = s.neighbourhood(scope(CASE_X), max_depth=4)
        replayed = s.ingest(copy.deepcopy(CASE_X))
        self.assertEqual(replayed["status"], "replayed")
        self.assertEqual(
            (replayed["node_count"], replayed["edge_count"]),
            (applied["node_count"], applied["edge_count"]),
        )
        self.assertEqual(s.neighbourhood(scope(CASE_X), max_depth=4), before)

    def test_only_synthetic_well_formed_inputs_are_accepted(self) -> None:
        data = copy.deepcopy(CASE_X)
        data["synthetic"] = False
        self.refused(data, "not_synthetic")
        data = copy.deepcopy(CASE_X)
        data["entity_bundle"]["tenant_id"] = TENANT_B
        self.refused(data, "tenant_mismatch")
        data = copy.deepcopy(CASE_X)
        data["case_id"] = "SYN-002"
        self.refused(data, "invalid")
        data = copy.deepcopy(CASE_X)
        data["extraction_revisions"][0]["document_sha256"] = "0" * 64
        self.refused(data, "provenance_hash_mismatch")
        data = copy.deepcopy(CASE_X)
        data["directorships"][0]["evidence"][0]["document_sha256"] = "0" * 64
        self.refused(data, "provenance_hash_mismatch")
        data = copy.deepcopy(CASE_X)
        data["entity_bundle"]["ownership"][0]["evidence"][0]["document_sha256"] = "0" * 64
        err = self.refused(data, "provenance_hash_mismatch")
        self.assertTrue(err.field.startswith("entity_bundle.ownership[0]"))
        data = copy.deepcopy(CASE_X)
        data["case_taxpayer_id"] = "NOBODY"
        self.refused(data, "unknown_party")


if __name__ == "__main__":
    unittest.main()
