"""Behaviour tests for the yente candidate review adapter (TAX-037).

The fake follows the locked yente routes (``GET /catalog``, ``POST /match/{dataset}``) and
returns the candidates and scores recorded in the TAX-027 smoke test
(integration/yente/README.md) for the committed synthetic dataset. Vati entities come from the
TAX-036 FtM mapping. All data is synthetic.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integration" / "yente"))
sys.path.insert(0, str(ROOT / "integration" / "followthemoney"))
sys.path.insert(0, str(ROOT / "integration" / "api"))
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))

import candidate_review as cr  # noqa: E402
import contract  # noqa: E402
import tax_mapping as tm  # noqa: E402

TENANT = "10000000-0000-4000-8000-000000000001"
CASE = "40000000-0000-4000-8000-000000000002"
OFFICER = "20000000-0000-4000-8000-000000000001"
SUPERVISOR = "20000000-0000-4000-8000-000000000003"
EVIDENCE = "50000000-0000-4000-8000-000000000001"
DATASET = "vati_synthetic"
VERSION = "2026110301"
NOW = "2026-11-05T09:00:00Z"
YENTE_DATA = ROOT / "integration" / "yente" / "data" / "vati_synthetic.ftm.json"

# Results recorded against the real yente 5.5.0 stack in TAX-027 (query name -> id, score).
RECORDED = {
    "SBR Trading": [("vati-syn-co-border-retail", 1.0)],
    "Synthetic Highland Imports": [
        ("vati-syn-co-highland-imports", 0.943),
        ("vati-syn-co-highland-import-services", 0.880),
    ],
    "Quartz Lantern Fisheries": [],
}


class FakeYente:
    """``GET /catalog`` and ``POST /match/{dataset}`` shaped like the locked yente source."""

    def __init__(self, version: str = VERSION, index_version: str | None = VERSION) -> None:
        self.version = version
        self.index_version = index_version
        self.entities = {
            e["id"]: e
            for e in (json.loads(line) for line in YENTE_DATA.read_text("utf-8").splitlines())
        }
        self.calls: list[tuple[str, str, Any]] = []
        self.status: int | None = None
        self.results: dict[str, list[tuple[str, float]]] = dict(RECORDED)

    def __call__(self, method: str, path: str, body: Any = None) -> cr.Response:
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.status is not None:
            return cr.Response(self.status, b'{"detail": "fake failure"}')
        if method == "GET" and path == "/catalog":
            current = self.index_version == self.version
            return self._json(
                {
                    "datasets": [
                        {
                            "name": DATASET,
                            "title": "Vati synthetic taxpayer entities (test data only)",
                            "version": self.version,
                            "index_version": self.index_version,
                            "load": True,
                        }
                    ],
                    "current": [DATASET] if current else [],
                    "outdated": [] if current else [DATASET],
                    "index_stale": not current,
                }
            )
        parsed = urllib.parse.urlsplit(path)
        if method == "POST" and parsed.path.startswith("/match/"):
            if parsed.path != f"/match/{DATASET}":
                return cr.Response(404, b'{"detail": "Dataset not found"}')
            params = urllib.parse.parse_qs(parsed.query)
            threshold = float(params["threshold"][0])
            limit = int(params["limit"][0])
            responses = {}
            for key, query in body["queries"].items():
                names = query["properties"].get("name", [])
                hits = [h for n in names for h in self.results.get(n, [])]
                results = [
                    {
                        "id": eid,
                        "caption": self.entities[eid]["properties"]["name"][0],
                        "schema": self.entities[eid]["schema"],
                        "properties": copy.deepcopy(self.entities[eid]["properties"]),
                        "datasets": [DATASET],
                        "referents": [],
                        "target": False,
                        "score": score,
                        "explanations": {},
                        "match": score >= threshold,
                    }
                    for eid, score in hits
                    if score >= threshold
                ][:limit]
                responses[key] = {
                    "status": 200,
                    "results": results,
                    "total": {"value": len(results), "relation": "eq"},
                    "query": query,
                }
            return self._json({"responses": responses, "limit": limit})
        return cr.Response(404, b'{"detail": "Not found"}')

    @staticmethod
    def _json(body: Any) -> cr.Response:
        return cr.Response(200, json.dumps(body).encode("utf-8"))


def party(pid: str, name: str, *, kind: str = "company", **extra: Any) -> dict[str, Any]:
    p: dict[str, Any] = {
        "id": pid,
        "kind": kind,
        "name": name,
        "role": "counterparty",
        "source": {"system": "synthetic_fixture", "record_type": "party", "record_id": pid},
    }
    p.update(extra)
    return p


def mapped(*parties: dict[str, Any]) -> list[dict[str, Any]]:
    """Vati FtM party entities from the TAX-036 mapping."""
    result = tm.map_bundle(
        {"version": tm.BUNDLE_VERSION, "tenant_id": TENANT, "parties": list(parties)}
    )
    return result.entities


def one(entities: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(entities) == 1
    return entities[0]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.yente = FakeYente()
        self.ledger = cr.ReviewLedger(TENANT)
        self.spec = contract.load_spec()
        self.key = 0

    def candidates(self, entity: dict[str, Any]) -> cr.CandidateSet:
        return cr.match(self.yente, DATASET, [entity], retrieved_at=NOW)[entity["id"]]

    def decide(
        self, cs: cr.CandidateSet, candidate_id: str, outcome: str, **kw: Any
    ) -> cr.Decision:
        self.key += 1
        args: dict[str, Any] = {
            "case_id": CASE,
            "candidate_id": candidate_id,
            "outcome": outcome,
            "reviewed_by": OFFICER,
            "reviewer_role": "auditor",
            "rationale": "Checked against the case registry extract.",
            "decided_at": NOW,
            "idempotency_key": f"review-key-{self.key:04d}",
            "current_version": VERSION,
            "case_evidence_ids": [EVIDENCE],
        }
        args.update(kw)
        return self.ledger.record(cs, **args)

    def assert_contract(self, candidates: list[dict[str, Any]]) -> None:
        schema = self.spec["components"]["schemas"]["MatchCandidate"]
        for c in candidates:
            self.assertEqual(contract.validate(c, schema, self.spec), [], c)


class Normalization(Base):
    def test_alias_match_is_versioned_unreviewed_and_contract_valid(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading", country="ls")))
        cs = self.candidates(entity)
        self.assertEqual([c.candidate_id for c in cs.candidates], ["vati-syn-co-border-retail"])
        c = cs.candidates[0]
        self.assertEqual((c.dataset, c.dataset_version, c.score), (DATASET, VERSION, "1.0000"))
        self.assertEqual(c.caption, "Synthetic Border Retail (Pty) Ltd")
        self.assertFalse(cs.ambiguous)
        out = self.ledger.contract_candidates(cs)
        self.assertEqual(out[0]["review_status"], "unreviewed")
        self.assertIsNone(out[0]["reviewed_by"])
        self.assert_contract(out)

    def test_query_sends_only_matching_properties_one_query_per_call(self) -> None:
        entity = one(
            mapped(
                party(
                    "CP-1",
                    "SBR Trading",
                    country="ls",
                    registration_number="LS-SYN-0002",
                    tax_number="TIN-SYN-0002",
                )
            )
        )
        cr.match(self.yente, DATASET, [entity], retrieved_at=NOW)
        method, path, body = self.yente.calls[1]
        self.assertEqual(method, "POST")
        self.assertTrue(path.startswith(f"/match/{DATASET}?"))
        self.assertIn("threshold=0.7000", path)
        self.assertEqual(list(body["queries"]), ["q"])
        props = body["queries"]["q"]["properties"]
        self.assertEqual(
            props,
            {
                "name": ["SBR Trading"],
                "jurisdiction": ["ls"],
                "registrationNumber": ["LS-SYN-0002"],
            },
        )
        self.assertNotIn("taxNumber", json.dumps(body))

    def test_person_query_uses_nationality(self) -> None:
        entity = one(mapped(party("P-1", "L. S. Director", kind="person", country="ls")))
        query = cr.build_query(entity)
        self.assertEqual(query["schema"], "Person")
        self.assertEqual(query["properties"]["nationality"], ["ls"])

    def test_no_candidates_for_an_unrelated_name(self) -> None:
        entity = one(mapped(party("CP-9", "Quartz Lantern Fisheries")))
        cs = self.candidates(entity)
        self.assertEqual(cs.candidates, ())
        self.assertEqual(self.ledger.contract_candidates(cs), [])

    def test_non_party_schemas_are_not_matched(self) -> None:
        with self.assertRaises(cr.ReviewError) as err:
            cr.build_query({"id": "x", "schema": "Payment", "properties": {"amount": ["1"]}})
        self.assertEqual(err.exception.reason, "unsupported_schema")

    def test_stale_index_and_unloaded_dataset_are_refused(self) -> None:
        self.yente = FakeYente(version="2026110501", index_version=VERSION)
        entity = one(mapped(party("CP-1", "SBR Trading")))
        with self.assertRaises(cr.ReviewError) as err:
            self.candidates(entity)
        self.assertEqual(
            (err.exception.code, err.exception.reason), ("upstream_unavailable", "index_stale")
        )
        self.assertTrue(err.exception.retryable)
        with self.assertRaises(cr.ReviewError) as err:
            cr.match(FakeYente(), "opensanctions", [entity], retrieved_at=NOW)
        self.assertEqual(err.exception.code, "not_found")

    def test_upstream_failures_map_to_contract_error_codes(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading")))
        for status, code, retryable in [
            (503, "upstream_unavailable", True),
            (429, "rate_limited", True),
            (400, "invalid_request", False),
        ]:
            self.yente.status = status
            with self.assertRaises(cr.ReviewError) as err:
                self.candidates(entity)
            self.assertEqual((err.exception.code, err.exception.retryable), (code, retryable))

    def test_result_from_another_dataset_is_rejected(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading")))
        original = self.yente.__call__

        def foreign(method: str, path: str, body: Any = None) -> cr.Response:
            resp = original(method, path, body)
            if method == "POST":
                data = json.loads(resp.body)
                data["responses"]["q"]["results"][0]["datasets"] = ["us_ofac_sdn"]
                return cr.Response(200, json.dumps(data).encode())
            return resp

        with self.assertRaises(cr.ReviewError) as err:
            cr.match(foreign, DATASET, [entity], retrieved_at=NOW)
        self.assertEqual(err.exception.reason, "foreign_dataset")


class Review(Base):
    def test_score_alone_never_confirms(self) -> None:
        # Score 1.0 and match: true, but nothing independent agrees.
        entity = one(mapped(party("CP-1", "SBR Trading")))
        cs = self.candidates(entity)
        with self.assertRaises(cr.ReviewError) as err:
            self.decide(cs, "vati-syn-co-border-retail", "confirmed")
        self.assertEqual(err.exception.reason, "score_only")
        self.assertEqual(self.ledger.decisions, [])
        self.assertEqual(self.ledger.links(), [])

    def test_alias_confirmed_with_agreeing_registration_number(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading", registration_number="ls syn 0002")))
        cs = self.candidates(entity)
        self.assertEqual(cs.candidates[0].corroboration["registrationNumber"], "agrees")
        d = self.decide(cs, "vati-syn-co-border-retail", "confirmed")
        self.assertEqual(d.basis, ("registrationNumber",))
        out = self.ledger.contract_candidates(cs)
        self.assertEqual((out[0]["review_status"], out[0]["reviewed_by"]), ("confirmed", OFFICER))
        self.assert_contract(out)
        [link] = self.ledger.links()
        self.assertEqual(
            (link.entity_id, link.candidate_id, link.dataset_version),
            (entity["id"], "vati-syn-co-border-retail", VERSION),
        )

    def test_false_match_with_conflicting_identifier_cannot_be_confirmed(self) -> None:
        # Same alias, different registration number: a false match despite score 1.0.
        entity = one(mapped(party("CP-2", "SBR Trading", registration_number="LS-SYN-9999")))
        cs = self.candidates(entity)
        self.assertEqual(cs.candidates[0].score, "1.0000")
        self.assertEqual(cs.candidates[0].corroboration["registrationNumber"], "conflicts")
        with self.assertRaises(cr.ReviewError) as err:
            self.decide(cs, "vati-syn-co-border-retail", "confirmed", evidence_ids=[EVIDENCE])
        self.assertEqual(err.exception.reason, "identifier_conflict")
        d = self.decide(
            cs, "vati-syn-co-border-retail", "rejected", rationale="Different registration."
        )
        self.assertEqual(self.ledger.contract_candidates(cs)[0]["review_status"], "rejected")
        self.assertEqual(d.score_at_review, "1.0000")
        self.assertEqual(self.ledger.links(), [])

    def test_ambiguous_candidates_need_something_that_singles_one_out(self) -> None:
        entity = one(mapped(party("CP-3", "Synthetic Highland Imports", country="ls")))
        cs = self.candidates(entity)
        self.assertTrue(cs.ambiguous)
        self.assertEqual(
            [(c.candidate_id, c.score) for c in cs.candidates],
            [
                ("vati-syn-co-highland-imports", "0.9430"),
                ("vati-syn-co-highland-import-services", "0.8800"),
            ],
        )
        # Country agreement alone is not identity.
        self.assertEqual(cs.candidates[0].corroboration["country"], "agrees")
        with self.assertRaises(cr.ReviewError) as err:
            self.decide(cs, "vati-syn-co-highland-imports", "confirmed")
        self.assertEqual(err.exception.reason, "score_only")
        # The officer can record that they cannot tell; the candidate stays unreviewed.
        d = self.decide(
            cs, "vati-syn-co-highland-imports", "ambiguous", rationale="Two similar firms."
        )
        self.assertEqual(d.outcome, "ambiguous")
        self.assertEqual(
            [c["review_status"] for c in self.ledger.contract_candidates(cs)],
            ["unreviewed", "unreviewed"],
        )
        # Cited case evidence lets the officer decide; history keeps the ambiguous decision.
        self.decide(cs, "vati-syn-co-highland-imports", "confirmed", evidence_ids=[EVIDENCE])
        self.assertEqual([x.outcome for x in self.ledger.decisions], ["ambiguous", "confirmed"])
        self.assertEqual(
            [c["review_status"] for c in self.ledger.contract_candidates(cs)],
            ["confirmed", "unreviewed"],
        )

    def test_ambiguous_set_with_distinguishing_registration_number(self) -> None:
        entity = one(
            mapped(party("CP-3", "Synthetic Highland Imports", registration_number="LS-SYN-1001"))
        )
        cs = self.candidates(entity)
        services = cs.get("vati-syn-co-highland-import-services")
        assert services is not None
        self.assertEqual(services.corroboration["registrationNumber"], "conflicts")
        d = self.decide(cs, "vati-syn-co-highland-imports", "confirmed")
        self.assertEqual(d.basis, ("registrationNumber",))
        with self.assertRaises(cr.ReviewError) as err:
            self.decide(
                cs, "vati-syn-co-highland-import-services", "confirmed", evidence_ids=[EVIDENCE]
            )
        self.assertEqual(err.exception.reason, "identifier_conflict")

    def test_only_one_confirmed_candidate_per_entity_and_version(self) -> None:
        entity = one(mapped(party("CP-3", "Synthetic Highland Imports")))
        cs = self.candidates(entity)
        self.decide(cs, "vati-syn-co-highland-imports", "confirmed", evidence_ids=[EVIDENCE])
        with self.assertRaises(cr.ReviewError) as err:
            self.decide(
                cs, "vati-syn-co-highland-import-services", "confirmed", evidence_ids=[EVIDENCE]
            )
        self.assertEqual(err.exception.reason, "already_confirmed")
        self.decide(
            cs, "vati-syn-co-highland-imports", "rejected", rationale="Wrong firm after all."
        )
        self.decide(
            cs, "vati-syn-co-highland-import-services", "confirmed", evidence_ids=[EVIDENCE]
        )
        self.assertEqual(
            [link.candidate_id for link in self.ledger.links()],
            ["vati-syn-co-highland-import-services"],
        )

    def test_original_identities_are_never_overwritten(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading", registration_number="LS-SYN-0002")))
        before_entity = copy.deepcopy(entity)
        before_yente = copy.deepcopy(self.yente.entities)
        cs = self.candidates(entity)
        self.decide(cs, "vati-syn-co-border-retail", "confirmed")
        self.assertEqual(entity, before_entity)
        self.assertEqual(self.yente.entities, before_yente)
        # The candidate keeps yente's own name and aliases; nothing is merged into the Vati entity.
        c = cs.candidates[0]
        self.assertEqual(c.properties["alias"], ("SBR Trading", "Border Retail Synthetic"))
        self.assertNotIn("alias", entity["properties"])
        self.assertEqual(entity["properties"]["name"], ["SBR Trading"])
        # Only GET and POST calls; nothing is written back to yente.
        self.assertTrue(
            all(
                m in ("GET", "POST") and not p.startswith("/updatez")
                for m, p, _ in self.yente.calls
            )
        )

    def test_decisions_are_bound_to_the_dataset_version(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading", registration_number="LS-SYN-0002")))
        cs = self.candidates(entity)
        self.decide(cs, "vati-syn-co-border-retail", "confirmed")
        # yente re-indexes a new version: the old candidates can no longer be reviewed ...
        with self.assertRaises(cr.ReviewError) as err:
            self.decide(cs, "vati-syn-co-border-retail", "rejected", current_version="2026110501")
        self.assertEqual(
            (err.exception.code, err.exception.reason),
            ("version_conflict", "dataset_version_changed"),
        )
        # ... and fresh candidates for the new version start unreviewed.
        self.yente = FakeYente(version="2026110501", index_version="2026110501")
        fresh = self.candidates(entity)
        self.assertEqual(fresh.dataset_version, "2026110501")
        self.assertEqual(self.ledger.contract_candidates(fresh)[0]["review_status"], "unreviewed")

    def test_service_identities_and_taxpayers_cannot_review(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading", registration_number="LS-SYN-0002")))
        cs = self.candidates(entity)
        for role in ("service", "taxpayer", "agent"):
            with self.assertRaises(cr.ReviewError) as err:
                self.decide(cs, "vati-syn-co-border-retail", "confirmed", reviewer_role=role)
            self.assertEqual(err.exception.code, "forbidden")
        self.decide(
            cs,
            "vati-syn-co-border-retail",
            "rejected",
            reviewed_by=SUPERVISOR,
            reviewer_role="supervisor",
        )

    def test_reviewer_roles_match_the_identity_policy(self) -> None:
        try:
            from vati_tax.identity import policy
        except ImportError:  # pragma: no cover - backend package is optional here
            self.skipTest("vati_tax backend package not importable")
        self.assertEqual(cr.REVIEWER_ROLES, set(policy.ACTION_ROLES[policy.FINDING_REVIEW]))

    def test_rationale_officer_and_evidence_are_checked(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading")))
        cs = self.candidates(entity)
        cid = "vati-syn-co-border-retail"
        cases = [
            ({"rationale": "  "}, "missing_rationale"),
            ({"reviewed_by": "officer@example.test"}, "invalid"),
            ({"outcome": "merged"}, "invalid"),
            ({"evidence_ids": ["50000000-0000-4000-8000-00000000ffff"]}, "unknown_evidence"),
        ]
        for kwargs, reason in cases:
            args = {"outcome": "rejected", **kwargs}
            with self.assertRaises(cr.ReviewError) as err:
                self.decide(cs, cid, **args)
            self.assertEqual(err.exception.reason, reason, kwargs)
        with self.assertRaises(cr.ReviewError) as err:
            self.decide(cs, "vati-syn-co-mountain-wholesale", "rejected")
        self.assertEqual(err.exception.code, "not_found")
        self.assertEqual(self.ledger.decisions, [])

    def test_idempotent_replay_and_key_reuse(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading")))
        cs = self.candidates(entity)
        args = dict(
            case_id=CASE,
            candidate_id="vati-syn-co-border-retail",
            outcome="rejected",
            reviewed_by=OFFICER,
            reviewer_role="auditor",
            rationale="Not this firm.",
            decided_at=NOW,
            idempotency_key="review-replay-0001",
            current_version=VERSION,
        )
        first = self.ledger.record(cs, **args)  # type: ignore[arg-type]
        again = self.ledger.record(cs, **{**args, "decided_at": "2026-11-05T09:05:00Z"})  # type: ignore[arg-type]
        self.assertIs(first, again)
        self.assertEqual(len(self.ledger.decisions), 1)
        with self.assertRaises(cr.ReviewError) as err:
            self.ledger.record(cs, **{**args, "rationale": "Changed my mind."})  # type: ignore[arg-type]
        self.assertEqual(err.exception.code, "idempotency_conflict")

    def test_decision_record_keeps_who_why_and_which_version(self) -> None:
        entity = one(mapped(party("CP-1", "SBR Trading", registration_number="LS-SYN-0002")))
        cs = self.candidates(entity)
        d = self.decide(cs, "vati-syn-co-border-retail", "confirmed", evidence_ids=[EVIDENCE])
        record = d.to_dict()
        self.assertEqual(
            {
                k: record[k]
                for k in ("outcome", "reviewed_by", "dataset", "dataset_version", "case_id")
            },
            {
                "outcome": "confirmed",
                "reviewed_by": OFFICER,
                "dataset": DATASET,
                "dataset_version": VERSION,
                "case_id": CASE,
            },
        )
        self.assertEqual(record["basis"], ["registrationNumber", "evidence"])
        self.assertEqual(record["evidence_ids"], [EVIDENCE])
        self.assertTrue(record["rationale"])


if __name__ == "__main__":
    unittest.main()
