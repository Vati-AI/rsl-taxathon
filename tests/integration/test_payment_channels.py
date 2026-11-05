"""Behaviour tests for the lawful payment-channel adapter contracts (TAX-079).

Fixtures: data/synthetic/tax079_payment_channels.json (five submissions for one invented
taxpayer: two overlapping bank statements, a mobile-money statement, a foreign-payment advice
and a digital-wallet export) and the CSV originals next to it. No network, no models.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for extra in ("integration/payment_channels", "integration/followthemoney", "backend/vati_tax", ""):
    path = str(ROOT / extra)
    if path not in sys.path:
        sys.path.insert(0, path)

import channel_adapters as ca  # noqa: E402
import channel_contracts as cc  # noqa: E402
import scheme_graph as sg  # noqa: E402
import tax_mapping as tm  # noqa: E402
from vati_tax.identity import policy  # noqa: E402

FIXTURE = ROOT / "data" / "synthetic" / "tax079_payment_channels.json"
FILES = ROOT / "data" / "synthetic" / "tax079_payment_channels"
SUBMISSIONS = json.loads(FIXTURE.read_text(encoding="utf-8"))["submissions"]
OPENAPI = json.loads((ROOT / "integration" / "api" / "openapi.json").read_text(encoding="utf-8"))
ERROR_CODES = set(OPENAPI["components"]["schemas"]["ErrorCode"]["enum"])
TENANT_A = SUBMISSIONS[0]["envelope"]["tenant_id"]
TENANT_B = "20000000-0000-4000-8000-000000000002"
CASE = SUBMISSIONS[0]["envelope"]["case_id"]
OTHER_CASE = "79000000-0000-4000-8000-000000000099"
TAXPAYER = SUBMISSIONS[0]["envelope"]["taxpayer_id"]
# Keys that would carry a judgement about a payment, party or method onto a record.
JUDGEMENT_KEYS = {"risk", "score", "signal", "flag", "flags", "label", "labels", "suspicious"}


def submission(name: str) -> dict[str, Any]:
    return copy.deepcopy(next(s for s in SUBMISSIONS if s["file"] == name))


BANK_Q1 = "bank-statement-2026q1.csv"
BANK_MA = "bank-statement-2026-mar-apr.csv"
MOBILE = "mobile-money-2026q1.csv"
FOREIGN = "foreign-payments-2026q1.csv"
WALLET = "digital-wallet-2026q1.csv"


def document(sub: dict[str, Any]) -> bytes:
    return (FILES / sub["file"]).read_bytes()


def with_document(sub: dict[str, Any], raw: bytes) -> dict[str, Any]:
    """The submission with a different supplied file, its SHA-256 and size updated."""
    sub = copy.deepcopy(sub)
    ev = sub["envelope"]["provenance"]["evidence"]
    ev["document_sha256"] = hashlib.sha256(raw).hexdigest()
    ev["size"] = len(raw)
    sub["raw"] = raw
    return sub


def ingest(store: cc.ChannelRecordStore, sub: dict[str, Any]) -> dict[str, Any]:
    raw = sub.get("raw") or document(sub)
    return store.ingest(sub["envelope"], raw, cc.Actor(**sub["actor"]), as_of=sub["as_of"])


def loaded(*names: str) -> cc.ChannelRecordStore:
    store = cc.ChannelRecordStore()
    for sub in SUBMISSIONS if not names else [submission(n) for n in names]:
        ingest(store, sub)
    return store


def scope(role: str = "auditor", tenant: str = TENANT_A, case: str = CASE) -> cc.ChannelScope:
    return cc.ChannelScope(tenant, case, role)


def all_records(store: cc.ChannelRecordStore, **kwargs: Any) -> list[dict[str, Any]]:
    return store.records(scope(), limit=cc.MAX_LIMIT, **kwargs)["data"]


def by_ref(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["record_set"]["id"], r["transaction"]["provider_reference"]): r for r in records}


def walk(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)
    elif isinstance(value, str):
        yield value


def to_tenant_b(sub: dict[str, Any]) -> dict[str, Any]:
    sub = copy.deepcopy(sub)
    sub["envelope"]["tenant_id"] = TENANT_B
    sub["actor"]["tenant_id"] = TENANT_B
    return sub


class Refusal:
    def assertRefused(self, code: str, reason: str, fn, *args, **kwargs) -> cc.ChannelContractError:  # type: ignore[no-untyped-def]
        with self.assertRaises(cc.ChannelContractError) as ctx:  # type: ignore[attr-defined]
            fn(*args, **kwargs)
        self.assertEqual((ctx.exception.code, ctx.exception.reason), (code, reason))  # type: ignore[attr-defined]
        self.assertIn(ctx.exception.code, ERROR_CODES)  # type: ignore[attr-defined]
        return ctx.exception


class FixtureAndContractTests(unittest.TestCase):
    def test_fixtures_are_synthetic_and_hash_pinned(self) -> None:
        channels = set()
        for sub in SUBMISSIONS:
            env = sub["envelope"]
            self.assertIs(env["synthetic"], True)
            self.assertIs(env["provenance"]["provider"]["synthetic"], True)
            raw = document(sub)
            ev = env["provenance"]["evidence"]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), ev["document_sha256"])
            self.assertEqual(len(raw), ev["size"])
            channels.add(env["provenance"]["channel"])
        self.assertEqual(channels, set(ca.ADAPTERS))

    def test_each_channel_has_an_offline_adapter_contract(self) -> None:
        self.assertEqual(
            set(ca.ADAPTERS),
            {"bank_statement", "mobile_money", "foreign_payment", "digital_wallet"},
        )
        for channel, adapter in ca.ADAPTERS.items():
            self.assertEqual(adapter.channel, channel)
            self.assertEqual(adapter.media_type, "text/csv")
            self.assertIn(adapter.media_type, tm.MEDIA_TYPES)
            self.assertTrue(adapter.document_format.endswith("_csv.v1"))
            self.assertTrue(adapter.adapter_version)

    def test_modules_open_no_network_or_process(self) -> None:
        banned = {"socket", "ssl", "urllib", "http", "requests", "httpx", "ftplib", "smtplib"}
        banned |= {"subprocess", "os", "aiohttp", "websocket"}
        for name in ("channel_adapters.py", "channel_contracts.py"):
            tree = ast.parse((ROOT / "integration" / "payment_channels" / name).read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertFalse(imported & banned, name)

    def test_role_sets_equal_the_tax029_policy(self) -> None:
        self.assertEqual(cc.REGISTER_ROLES, policy.ACTION_ROLES[policy.EVIDENCE_REGISTER])
        self.assertEqual(cc.READ_ROLES, policy.ACTION_ROLES[policy.EVIDENCE_READ])
        self.assertEqual(cc.AUTHORISE_ROLES, policy.ACTION_ROLES[policy.EVIDENCE_REQUEST_AUTHORISE])
        self.assertEqual(cc.STAFF_ROLES, policy.STAFF_ROLES)
        self.assertIn(policy.EVIDENCE_REQUEST_AUTHORISE, policy.HUMAN_ONLY_ACTIONS)
        self.assertIn(policy.EVIDENCE_REGISTER, policy.TAXPAYER_OWNED_ACTIONS)


class IngestAndProvenanceTests(unittest.TestCase):
    def test_every_record_carries_permission_and_provenance(self) -> None:
        store = loaded()
        records = all_records(store)
        self.assertEqual(len(records), 19)
        files = {s["envelope"]["provenance"]["evidence"]["evidence_id"]: s for s in SUBMISSIONS}
        for r in records:
            perm, prov = r["permission"], r["provenance"]
            self.assertIn(perm["basis"], cc.BASIS_METHODS)
            for key in ("reference", "requested_by", "scope", "granted_at", "expires_at"):
                self.assertTrue(perm[key], key)
            self.assertEqual(perm["scope"]["case_id"], r["case_id"])
            sub = files[prov["evidence_ref"]["evidence_id"]]
            self.assertEqual(
                prov["evidence_ref"]["document_sha256"], hashlib.sha256(document(sub)).hexdigest()
            )
            self.assertRegex(prov["evidence_ref"]["locator"]["record"], r"^line:\d+$")
            self.assertIn(prov["retrieval_method"], cc.BASIS_METHODS[perm["basis"]])
            adapter = ca.ADAPTERS[r["channel"]]
            self.assertEqual(
                prov["adapter"],
                {
                    "adapter_id": adapter.adapter_id,
                    "adapter_version": adapter.adapter_version,
                    "document_format": adapter.document_format,
                },
            )
            self.assertIs(prov["provider"]["synthetic"], True)
            self.assertTrue(prov["received_at"] and prov["recorded_at"])
            self.assertIsNone(r["interpretation"])

    def test_bases_and_methods_in_the_fixtures(self) -> None:
        records = all_records(loaded())
        seen = {(r["permission"]["basis"], r["provenance"]["retrieval_method"]) for r in records}
        self.assertEqual(
            seen,
            {
                ("taxpayer_supplied", "taxpayer_upload"),
                ("taxpayer_supplied", "officer_upload"),
                ("statutory_request", "statutory_response"),
                ("taxpayer_consent", "consented_provider_file"),
            },
        )
        statutory = next(r for r in records if r["permission"]["basis"] == "statutory_request")
        self.assertEqual(statutory["permission"]["legal_authority"]["status"], "unverified")
        self.assertEqual(statutory["permission"]["approved_by"]["role"], "supervisor")

    def test_money_is_decimal_strings_with_iso_currency(self) -> None:
        for r in all_records(loaded()):
            amount = r["transaction"]["amount"]
            if amount is None:
                continue
            self.assertRegex(amount["amount"], r"^\d+\.\d{2}$")
            self.assertRegex(amount["currency"], r"^[A-Z]{3}$")
        records = by_ref(all_records(loaded()))
        self.assertEqual(
            records[("RS-BANK-2026Q1", "BS-0003")]["transaction"]["amount"],
            {"amount": "860.40", "currency": "LSL"},
        )

    def test_exchange_rates_only_when_supplied(self) -> None:
        store = loaded(FOREIGN)
        records = by_ref(all_records(store))
        fx1 = records[("RS-FX-2026Q1", "FX-0001")]["transaction"]["details"]["conversion"]
        self.assertEqual(fx1["check"], "consistent")
        self.assertEqual(fx1["exchange_rate"], "18.250000")
        fx3 = records[("RS-FX-2026Q1", "FX-0003")]
        self.assertIsNone(fx3["transaction"]["details"]["conversion"]["exchange_rate"])
        self.assertEqual(fx3["transaction"]["details"]["conversion"]["check"], "not_checked")
        self.assertEqual([g["code"] for g in fx3["gaps"]], ["exchange_rate_not_supplied"])
        self.assertEqual(fx3["gaps"][0]["interpretation"], "incomplete_data")
        analysis = store.analysis(scope())
        (inconsistent,) = analysis["contradictions"]
        self.assertEqual(inconsistent["code"], "booked_conversion_inconsistent")
        self.assertEqual(
            inconsistent["detail"]["expected"], {"amount": "9150.00", "currency": "LSL"}
        )
        self.assertEqual(inconsistent["status"], "unresolved")

    def test_virtual_asset_without_supplied_value_keeps_quantity_and_a_gap(self) -> None:
        records = by_ref(all_records(loaded(WALLET)))
        valued = records[("RS-WAL-2026Q1", "WL-0002")]
        self.assertEqual(valued["transaction"]["amount"], {"amount": "1450.00", "currency": "LSL"})
        unvalued = records[("RS-WAL-2026Q1", "WL-0003")]
        self.assertIsNone(unvalued["transaction"]["amount"])
        self.assertEqual(unvalued["transaction"]["details"]["quantity"], "0.002")
        self.assertEqual([g["code"] for g in unvalued["gaps"]], ["fiat_value_not_supplied"])


class PermissionRefusalTests(Refusal, unittest.TestCase):
    def refused(self, sub: dict[str, Any], code: str, reason: str) -> None:
        store = cc.ChannelRecordStore()
        error = self.assertRefused(code, reason, ingest, store, sub)
        body = error.error_response("00000000-0000-4000-8000-000000000000")["error"]
        self.assertEqual(body["code"], code)
        self.assertEqual(body["field_errors"][0]["code"], reason)
        self.assertNotIn("Synthetic", body["message"])  # no document content in errors
        self.assertRefused("not_found", "not_found", store.records, scope())

    def test_missing_permission_is_refused(self) -> None:
        sub = submission(BANK_Q1)
        sub["envelope"]["permission"] = None
        self.refused(sub, "forbidden", "lawful_basis_missing")
        sub["envelope"]["permission"] = submission(BANK_Q1)["envelope"]["permission"]
        sub["envelope"]["permission"]["basis"] = "officer_curiosity"
        self.refused(sub, "forbidden", "lawful_basis_missing")

    def test_expired_and_not_yet_valid_permissions_are_refused(self) -> None:
        sub = submission(BANK_Q1)
        sub["as_of"] = "2026-10-02T08:00:01Z"
        self.refused(sub, "forbidden", "permission_expired")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["received_at"] = "2026-04-02T07:59:59Z"
        sub["as_of"] = "2026-04-02T08:00:00Z"
        self.refused(sub, "forbidden", "received_outside_permission")
        sub = submission(BANK_Q1)
        sub["envelope"]["permission"]["expires_at"] = "2027-05-01T00:00:00Z"
        self.refused(sub, "invalid_domain_input", "permission_too_long")

    def test_scope_covers_channel_account_period_and_subject(self) -> None:
        sub = submission(BANK_Q1)
        sub["envelope"]["permission"]["scope"]["channels"] = ["mobile_money"]
        self.refused(sub, "forbidden", "out_of_scope_channel")
        sub = submission(BANK_Q1)
        sub["envelope"]["permission"]["scope"]["account_refs"] = ["SYN-ACC-0002"]
        self.refused(sub, "forbidden", "out_of_scope_account")
        sub = submission(BANK_Q1)
        sub["envelope"]["permission"]["scope"]["period_end"] = "2026-03-30"
        self.refused(sub, "forbidden", "out_of_scope_period")
        sub = submission(BANK_Q1)
        sub["envelope"]["permission"]["scope"]["case_id"] = OTHER_CASE
        self.refused(sub, "forbidden", "permission_subject_mismatch")

    def test_basis_must_match_retrieval_method_and_approvals(self) -> None:
        sub = submission(BANK_MA)
        sub["envelope"]["provenance"]["retrieval_method"] = "officer_upload"
        self.refused(sub, "forbidden", "basis_retrieval_mismatch")
        sub = submission(BANK_MA)
        sub["envelope"]["permission"]["approved_by"] = None
        self.refused(sub, "forbidden", "approval_missing")
        sub = submission(BANK_MA)
        sub["envelope"]["permission"]["approved_by"]["role"] = "specialist"
        self.refused(sub, "forbidden", "approver_not_permitted")
        sub = submission(BANK_MA)
        sub["envelope"]["permission"]["approved_by"]["role"] = "service"
        self.refused(sub, "forbidden", "approver_not_permitted")
        sub = submission(BANK_MA)
        sub["envelope"]["permission"]["legal_authority"] = None
        self.refused(sub, "forbidden", "legal_authority_missing")
        sub = submission(BANK_MA)
        sub["envelope"]["permission"]["requested_by"] = {
            "user_id": "svc-outbox",
            "role": "service",
            "taxpayer_id": None,
        }
        self.refused(sub, "forbidden", "requester_not_permitted")

    def test_consent_comes_from_the_described_taxpayer(self) -> None:
        sub = submission(MOBILE)
        sub["envelope"]["permission"]["approved_by"]["taxpayer_id"] = (
            "79100000-0000-4000-8000-000000000002"
        )
        self.refused(sub, "forbidden", "approver_not_permitted")
        sub = submission(MOBILE)
        sub["envelope"]["permission"]["approved_by"] = None
        self.refused(sub, "forbidden", "approval_missing")
        sub = submission(MOBILE)
        sub["envelope"]["permission"]["requested_by"]["role"] = "specialist"
        self.refused(sub, "forbidden", "requester_not_permitted")

    def test_actor_must_hold_the_policy_action_in_scope(self) -> None:
        sub = submission(BANK_Q1)
        sub["actor"]["tenant_id"] = TENANT_B
        self.refused(sub, "tenant_scope_denied", "tenant_scope_denied")
        sub = submission(BANK_Q1)
        sub["actor"]["taxpayer_id"] = "79100000-0000-4000-8000-000000000002"
        self.refused(sub, "forbidden", "role_not_permitted")
        sub = submission(BANK_Q1)
        sub["actor"]["user_id"] = "tp-user-other"
        self.refused(sub, "forbidden", "requester_not_permitted")
        sub = submission(BANK_Q1)  # an officer cannot record a "taxpayer upload"
        sub["actor"] = {
            "user_id": "officer-a1",
            "role": "auditor",
            "tenant_id": TENANT_A,
            "taxpayer_id": None,
        }
        self.refused(sub, "forbidden", "role_not_permitted")
        sub = submission(FOREIGN)  # an officer upload is not a service action
        sub["actor"] = {
            "user_id": "svc-outbox",
            "role": "service",
            "tenant_id": TENANT_A,
            "taxpayer_id": None,
        }
        self.refused(sub, "forbidden", "role_not_permitted")
        sub = submission(FOREIGN)
        sub["actor"]["role"] = "operator"
        self.refused(sub, "forbidden", "role_not_permitted")


class InvalidInputTests(Refusal, unittest.TestCase):
    def bad(self, sub: dict[str, Any], reason: str, code: str = "invalid_domain_input") -> None:
        self.assertRefused(code, reason, ingest, cc.ChannelRecordStore(), sub)

    def edit_bank(self, old: str, new: str) -> dict[str, Any]:
        raw = document(submission(BANK_Q1)).replace(old.encode(), new.encode(), 1)
        return with_document(submission(BANK_Q1), raw)

    def test_provenance_must_match_the_supplied_file(self) -> None:
        sub = submission(BANK_Q1)
        sub["raw"] = document(sub) + b"\n"
        self.bad(sub, "provenance_hash_mismatch")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["evidence"]["size"] += 1
        self.bad(sub, "provenance_size_mismatch")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["evidence"]["media_type"] = "application/pdf"
        self.bad(sub, "unsupported_media_type")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["document_format"] = "mobile_money_csv.v1"
        self.bad(sub, "unsupported_format")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["channel"] = "card_network"
        self.bad(sub, "unsupported_channel")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["retrieval_method"] = "web_scrape"
        self.bad(sub, "invalid")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["provider"]["synthetic"] = False
        self.bad(sub, "not_synthetic")
        sub = submission(BANK_Q1)
        sub["envelope"]["synthetic"] = False
        self.bad(sub, "not_synthetic")
        sub = submission(BANK_Q1)
        sub["envelope"]["provenance"]["received_at"] = "2026-04-02 08:15"
        self.bad(sub, "invalid_timestamp")

    def test_malformed_files_are_refused(self) -> None:
        self.bad(self.edit_bank("12500.00", "1.25e4"), "invalid_amount")
        self.bad(self.edit_bank("12500.00", "12500.005"), "invalid_amount")
        self.bad(self.edit_bank("12500.00", "-12500.00"), "invalid_amount")
        self.bad(self.edit_bank(",LSL,", ",lsl,"), "invalid_currency")
        self.bad(self.edit_bank("2026-01-05,2026-01-05", "2026-02-30,2026-01-05"), "invalid_date")
        self.bad(self.edit_bank("BS-0002", "BS-0001"), "duplicate_transaction_ref")
        self.bad(self.edit_bank(",credit,", ",refund,"), "invalid")
        self.bad(self.edit_bank("Yarn purchase", "=HYPERLINK(1)"), "unsafe_cell")
        self.bad(self.edit_bank("Yarn purchase", "Yarn,purchase"), "ragged_row")
        self.bad(self.edit_bank("narration", "notes"), "unexpected_column")
        self.bad(
            self.edit_bank("SYN-ACC-0001,BS-0003", "SYN-ACC-0002,BS-0003"), "multiple_accounts"
        )
        self.bad(with_document(submission(BANK_Q1), b"\xff\xfe"), "invalid_encoding")
        header = document(submission(BANK_Q1)).split(b"\n")[0] + b"\n"
        self.bad(with_document(submission(BANK_Q1), header), "no_transactions")

    def test_foreign_and_wallet_pairs_must_be_complete(self) -> None:
        raw = document(submission(FOREIGN)).replace(
            b",18.250000,Synthetic Bank One advice,", b",18.250000,,", 1
        )
        self.bad(with_document(submission(FOREIGN), raw), "incomplete_pair")
        raw = document(submission(FOREIGN)).replace(
            b"2000.00,LSL,,,", b"2000.00,ZAR,1.000000,Desk,", 1
        )
        self.bad(with_document(submission(FOREIGN), raw), "unexpected_exchange_rate")
        raw = document(submission(WALLET)).replace(
            b"1450.00,LSL,Synthetic exchange statement", b"1450.00,LSL,", 1
        )
        self.bad(with_document(submission(WALLET), raw), "incomplete_pair")
        raw = document(submission(WALLET)).replace(b"SYNC,0.015", b"SYNC,0", 1)
        self.bad(with_document(submission(WALLET), raw), "invalid_quantity")

    def test_envelope_rejects_unknown_fields(self) -> None:
        sub = submission(BANK_Q1)
        sub["envelope"]["priority"] = "high"
        self.bad(sub, "unexpected_field")
        sub = submission(BANK_Q1)
        sub["envelope"]["permission"]["scope"]["all_accounts"] = True
        self.bad(sub, "unexpected_field")


class NoLabelTests(Refusal, unittest.TestCase):
    def outputs(self, store: cc.ChannelRecordStore) -> list[Any]:
        s = scope()
        graph = sg.build_case_graph(store.scheme_graph_input(s))
        return [
            store.records(s, limit=cc.MAX_LIMIT, include_superseded=True),
            store.analysis(s),
            store.ftm_bundle(s),
            graph,
        ]

    def test_no_labelling_words_or_judgement_fields_in_any_output(self) -> None:
        for output in self.outputs(loaded()):
            for text in walk(output):
                self.assertEqual(sg.labelling_terms(text), [], text)
                self.assertNotIn(text.lower(), JUDGEMENT_KEYS)
        self.assertEqual(sg.labelling_terms(cc.NOTICE), [])

    def test_labels_cannot_enter_through_files_or_envelopes(self) -> None:
        store = cc.ChannelRecordStore()
        raw = document(submission(BANK_Q1)).replace(
            b"Yarn purchase", b"Suspicious yarn purchase", 1
        )
        self.assertRefused(
            "invalid_domain_input",
            "risk_label_not_allowed",
            ingest,
            store,
            with_document(submission(BANK_Q1), raw),
        )
        raw = document(submission(BANK_Q1)).replace(b",narration", b",risk_score", 1)
        self.assertRefused(
            "invalid_domain_input",
            "risk_label_not_allowed",
            ingest,
            store,
            with_document(submission(BANK_Q1), raw),
        )
        sub = submission(BANK_Q1)
        sub["envelope"]["risk_flag"] = True
        self.assertRefused("invalid_domain_input", "risk_label_not_allowed", ingest, store, sub)
        sub = submission(BANK_Q1)
        sub["envelope"]["account_holder"]["name"] = "High-risk weaver"
        self.assertRefused("invalid_domain_input", "risk_label_not_allowed", ingest, store, sub)

    def test_payment_method_alone_never_produces_an_entry(self) -> None:
        """The same receipt recorded as a transfer, a cash deposit, a mobile-money cash-in, a
        wallet payout or a same-currency foreign transfer yields no contradiction, gap or
        judgement; records differ only in descriptive details and provenance."""
        line = "SYN-ACC-0001,BS-0001,2026-01-05,,{t},credit,500.00,LSL,Synthetic Customer A,,Sale\n"
        header = document(submission(BANK_Q1)).split(b"\n")[0].decode() + "\n"
        views = []
        for kind in ("transfer", "cash_deposit", "cheque", "card_payment"):
            raw = (header + line.format(t=kind)).encode()
            store = cc.ChannelRecordStore()
            ingest(store, with_document(submission(BANK_Q1), raw))
            views.append((store, kind))
        mm_header = document(submission(MOBILE)).split(b"\n")[0].decode() + "\n"
        for kind in ("cash_in", "p2p_transfer", "merchant_payment"):
            raw = (
                mm_header + f"SYN-MM-0001,MM-0001,2026-01-05,{kind},received,500.00,,LSL,"
                "Synthetic Customer A,,,Sale\n"
            ).encode()
            store = cc.ChannelRecordStore()
            ingest(store, with_document(submission(MOBILE), raw))
            views.append((store, kind))
        w_header = document(submission(WALLET)).split(b"\n")[0].decode() + "\n"
        for row in (
            "SYN-WAL-0001,WL-0001,2026-01-05,in,fiat_balance,LSL,500.00,,,,Synthetic Customer A,,Sale\n",
            "SYN-WAL-0001,WL-0001,2026-01-05,in,virtual_asset,SYNC,0.01,500.00,LSL,Quote,"
            "Synthetic Customer A,,Sale\n",
        ):
            store = cc.ChannelRecordStore()
            ingest(store, with_document(submission(WALLET), (w_header + row).encode()))
            views.append((store, "wallet"))
        f_header = document(submission(FOREIGN)).split(b"\n")[0].decode() + "\n"
        row = "SYN-ACC-0001,FX-0001,2026-02-10,,incoming,500.00,LSL,,,,,Synthetic Customer A,,za,,Sale\n"
        store = cc.ChannelRecordStore()
        ingest(store, with_document(submission(FOREIGN), (f_header + row).encode()))
        views.append((store, "foreign"))

        shapes = set()
        for store, kind in views:
            analysis = store.analysis(scope())
            self.assertEqual(analysis, {"contradictions": [], "context": [], "gaps": []}, kind)
            (record,) = all_records(store)
            self.assertEqual(
                record["transaction"]["amount"], {"amount": "500.00", "currency": "LSL"}
            )
            self.assertEqual(record["transaction"]["direction"], "inflow")
            self.assertEqual(record["contradiction_ids"], [])
            self.assertIsNone(record["interpretation"])
            shapes.add(tuple(sorted(record)))
            (link,) = store.ftm_bundle(scope())["channel_links"]
            self.assertEqual((link["gaps"], link["contradiction_ids"]), ([], []))
            self.assertFalse(set(walk(record)) & JUDGEMENT_KEYS)
        self.assertEqual(len(shapes), 1)

    def test_changing_only_the_method_changes_only_the_method_detail(self) -> None:
        header = document(submission(BANK_Q1)).split(b"\n")[0].decode() + "\n"
        results = []
        for kind in ("transfer", "cash_deposit"):
            raw = (
                header + f"SYN-ACC-0001,BS-0001,2026-01-05,,{kind},credit,500.00,LSL,A,,Sale\n"
            ).encode()
            store = cc.ChannelRecordStore()
            ingest(store, with_document(submission(BANK_Q1), raw))
            (record,) = all_records(store)
            results.append(record["transaction"])
        self.assertEqual(results[0]["details"]["transaction_type"], "transfer")
        self.assertEqual(results[1]["details"]["transaction_type"], "cash_deposit")
        for t in results:
            t["details"].pop("transaction_type")
        self.assertEqual(results[0], results[1])


class VersioningAndReplayTests(Refusal, unittest.TestCase):
    def correction(self, previous_sha: str, version: int = 2) -> dict[str, Any]:
        raw = document(submission(BANK_Q1)).replace(b"860.40", b"806.40", 1)
        sub = with_document(submission(BANK_Q1), raw)
        env = sub["envelope"]
        env["record_set_version"] = version
        env["supersedes"] = {"record_set_version": version - 1, "record_set_sha256": previous_sha}
        env["correction_reason"] = "Bank reissued the statement with a corrected card amount"
        env["provenance"]["evidence"]["evidence_id"] = "79200000-0000-4000-8000-000000000011"
        env["provenance"]["evidence"]["file_name"] = "bank-statement-2026q1-reissued.csv"
        sub["as_of"] = "2026-04-05T08:00:00Z"
        return sub

    def test_replay_is_a_no_op_and_changed_content_conflicts(self) -> None:
        store = cc.ChannelRecordStore()
        first = ingest(store, submission(BANK_Q1))
        before = all_records(store)
        again = ingest(store, submission(BANK_Q1))
        self.assertEqual(again["status"], "replayed")
        self.assertEqual(again["record_ids"], first["record_ids"])
        self.assertEqual(all_records(store), before)
        changed = submission(BANK_Q1)
        changed["envelope"]["permission"]["reference"] = "SUB-2026-0999"
        self.assertRefused("idempotency_conflict", "changed_record", ingest, store, changed)
        reused = self.correction(first["record_set_sha256"])
        reused["envelope"]["provenance"]["evidence"]["evidence_id"] = submission(BANK_Q1)[
            "envelope"
        ]["provenance"]["evidence"]["evidence_id"]
        self.assertRefused("idempotency_conflict", "changed_record", ingest, store, reused)
        self.assertEqual(all_records(store), before)

    def test_records_are_immutable_and_corrections_are_new_versions(self) -> None:
        store = cc.ChannelRecordStore()
        first = ingest(store, submission(BANK_Q1))
        view = all_records(store)
        view[0]["transaction"]["amount"]["amount"] = "1.00"
        self.assertNotEqual(all_records(store)[0]["transaction"]["amount"]["amount"], "1.00")

        self.assertRefused(
            "version_conflict", "stale_or_missing_version", ingest, store, self.correction("a" * 64)
        )
        self.assertRefused(
            "version_conflict",
            "stale_or_missing_version",
            ingest,
            store,
            self.correction(first["record_set_sha256"], 3),
        )
        second = ingest(store, self.correction(first["record_set_sha256"]))
        self.assertEqual(second["status"], "applied")
        current = by_ref(all_records(store))
        self.assertEqual(
            current[("RS-BANK-2026Q1", "BS-0003")]["transaction"]["amount"]["amount"], "806.40"
        )
        self.assertEqual(len(current), 5)
        everything = all_records(store, include_superseded=True)
        self.assertEqual(len(everything), 10)
        old = [r for r in everything if r["status"] == "superseded"]
        self.assertEqual({r["record_id"] for r in old}, set(first["record_ids"]))
        self.assertIn("860.40", {r["transaction"]["amount"]["amount"] for r in old})
        history = store.history(scope(), "RS-BANK-2026Q1")
        self.assertEqual([h["record_set_version"] for h in history], [1, 2])
        self.assertEqual(history[1]["supersedes"]["record_set_sha256"], first["record_set_sha256"])
        # A correction does not change its subject.
        third = self.correction(second["record_set_sha256"], 3)
        third["envelope"]["account_holder"]["name"] = "Another Synthetic Co"
        self.assertRefused("invalid_domain_input", "invalid", ingest, store, third)

    def test_output_is_independent_of_ingest_order(self) -> None:
        forward, backward = cc.ChannelRecordStore(), cc.ChannelRecordStore()
        for sub in SUBMISSIONS:
            ingest(forward, copy.deepcopy(sub))
        for sub in reversed(SUBMISSIONS):
            ingest(backward, copy.deepcopy(sub))
        for name in ("analysis", "ftm_bundle", "scheme_graph_input"):
            a, b = getattr(forward, name)(scope()), getattr(backward, name)(scope())
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), name)
        self.assertEqual(all_records(forward), all_records(backward))
        self.assertEqual(
            forward.ftm_bundle(scope())["digests"], backward.ftm_bundle(scope())["digests"]
        )

    def test_paging_is_bounded_and_stable(self) -> None:
        store = loaded()
        self.assertRefused("invalid_domain_input", "invalid", store.records, scope(), limit=101)
        seen, cursor = [], None
        while True:
            page = store.records(scope(), limit=4, cursor=cursor)
            seen += [r["record_id"] for r in page["data"]]
            if not page["meta"]["has_more"]:
                break
            cursor = page["meta"]["next_cursor"]
        self.assertEqual(seen, [r["record_id"] for r in all_records(store)])
        self.assertEqual(seen, sorted(seen))


class IsolationTests(Refusal, unittest.TestCase):
    def test_tenant_and_case_isolation(self) -> None:
        store = loaded()
        for sub in SUBMISSIONS:
            ingest(store, to_tenant_b(sub))
        a = all_records(store)
        b = store.records(scope(tenant=TENANT_B), limit=cc.MAX_LIMIT)["data"]
        self.assertEqual(len(a), len(b))
        self.assertEqual({r["tenant_id"] for r in a}, {TENANT_A})
        self.assertEqual({r["tenant_id"] for r in b}, {TENANT_B})
        self.assertFalse({r["record_id"] for r in a} & {r["record_id"] for r in b})
        ents_a = {e["id"] for e in store.ftm_bundle(scope())["entities"]}
        ents_b = {e["id"] for e in store.ftm_bundle(scope(tenant=TENANT_B))["entities"]}
        self.assertFalse(ents_a & ents_b)
        # Contradictions are computed per (tenant, case) only.
        self.assertEqual(len(store.analysis(scope())["contradictions"]), 2)
        self.assertRefused("not_found", "not_found", store.records, scope(case=OTHER_CASE))
        self.assertRefused("not_found", "not_found", store.ftm_bundle, scope(case=OTHER_CASE))

    def test_readers_need_evidence_read(self) -> None:
        store = loaded()
        for role in ("auditor", "specialist", "supervisor", "service"):
            self.assertTrue(store.records(scope(role))["data"])
        for role in ("taxpayer", "operator"):
            self.assertRefused("forbidden", "forbidden", store.records, scope(role))
            self.assertRefused("forbidden", "forbidden", store.ftm_bundle, scope(role))

    def test_other_case_records_never_join_a_case(self) -> None:
        store = loaded(BANK_Q1)
        sub = submission(BANK_MA)
        env = sub["envelope"]
        env["case_id"] = OTHER_CASE
        env["permission"]["scope"]["case_id"] = OTHER_CASE
        ingest(store, sub)
        self.assertEqual(store.analysis(scope())["contradictions"], [])
        self.assertEqual(len(all_records(store)), 5)


class FtmMappingTests(unittest.TestCase):
    def test_payments_map_to_ftm_with_provenance_and_contradictions(self) -> None:
        store = loaded()
        mapped = store.ftm_bundle(scope())
        tm.validate_entities(mapped["entities"])
        payments = [e for e in mapped["entities"] if e["schema"] == "Payment"]
        records = {r["record_id"]: r for r in all_records(store)}
        self.assertEqual(len(payments), 18)  # 19 records, one virtual asset without a value
        self.assertEqual([u["reason"] for u in mapped["unmapped"]], [["fiat_value_not_supplied"]])
        holder = tm.entity_id(TENANT_A, "party", "TP-MALUTI")
        links = {link["entity_id"]: link for link in mapped["channel_links"]}
        exts = {x["entity_id"]: x for x in mapped["extensions"]}
        for p in payments:
            ext, link = exts[p["id"]], links[p["id"]]
            record = records[link["record_id"]]
            self.assertEqual(ext["source"]["record_type"], f"payment_channel.{record['channel']}")
            self.assertEqual(ext["evidence"], [record["provenance"]["evidence_ref"]])
            self.assertEqual(p["properties"]["amount"], [record["transaction"]["amount"]["amount"]])
            side = "beneficiary" if record["transaction"]["direction"] == "inflow" else "payer"
            self.assertEqual(p["properties"][side], [holder])
            self.assertEqual(link["provenance"], record["provenance"])
            self.assertEqual(link["permission"]["reference"], record["permission"]["reference"])
            self.assertEqual(link["contradiction_ids"], record["contradiction_ids"])
            self.assertIsNone(link["interpretation"])
        contradicted = [x for x in mapped["channel_links"] if x["contradiction_ids"]]
        self.assertEqual(len(contradicted), 3)  # both BS-0004 copies and FX-0002
        self.assertEqual(
            {c["code"] for c in mapped["contradictions"]},
            {"same_transaction_reference_differs", "booked_conversion_inconsistent"},
        )

    def test_counterparties_are_never_joined_on_names(self) -> None:
        mapped = loaded().ftm_bundle(scope())
        collectors = [
            p for p in mapped["bundle"]["parties"] if p["name"] == "Synthetic Overseas Buyer"
        ]
        self.assertEqual(len(collectors), 2)
        self.assertEqual(len({p["id"] for p in collectors}), 2)

    def test_bundle_is_a_valid_tax074_graph_input_without_derived_edges(self) -> None:
        store = loaded()
        graph = sg.build_case_graph(store.scheme_graph_input(scope()))
        self.assertEqual({e["type"] for e in graph["edges"].values()}, {"case_subject"})
        graphs = sg.SchemeGraphStore()
        self.assertEqual(graphs.ingest(store.scheme_graph_input(scope()))["status"], "applied")
        self.assertEqual(graphs.ingest(store.scheme_graph_input(scope()))["status"], "replayed")


if __name__ == "__main__":
    unittest.main()
