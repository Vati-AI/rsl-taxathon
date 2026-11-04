"""Bench tests for persisted case IDs and service links (TAX-030).

These need a connected Frappe site and run with
``bench --site <site> run-tests --module vati_tax.ids.test_mapping_bench``
(``scripts/frappe_dev.py verify`` does this on a disposable site). Under plain
``unittest`` without a site they are skipped, not passed.
"""

from __future__ import annotations

import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    ON_SITE = bool(getattr(frappe.local, "site", None))
except ImportError:  # dev.py check runs without Frappe installed
    frappe = None
    IntegrationTestCase = unittest.TestCase
    ON_SITE = False

from vati_tax.ids.canonical import (
    CaseNotFound,
    IdempotencyConflict,
    InvalidMapping,
    MappingConflict,
    TenantBoundaryError,
)


def uid() -> str:
    return str(uuid.uuid4())


def key() -> str:
    return "synthetic-" + uuid.uuid4().hex


@unittest.skipUnless(ON_SITE, "needs a Frappe site: bench run-tests --module")
class CaseMappingBenchTests(IntegrationTestCase):
    def setUp(self) -> None:
        from vati_tax.ids import mapping

        self.m = mapping
        self.tenant_a, self.tenant_b = uid(), uid()
        self.case = mapping.register_case(
            tenant_id=self.tenant_a, idempotency_key=key(), taxpayer_id=uid()
        )
        self.case_id = self.case["case_id"]

    def link(self, **overrides):
        args = dict(
            tenant_id=self.tenant_a,
            case_id=self.case_id,
            service="jube",
            external_type="case",
            external_id="syn-jube-" + uuid.uuid4().hex[:8],
        )
        args.update(overrides)
        return self.m.record_link(**args)

    def count_links(self, **filters) -> int:
        return frappe.db.count("Vati Service Link", filters)

    def event(self, **payload):
        body = {"service": "jube", "external_type": "case", "external_id": "syn-jube-evt"}
        body.update(payload)
        return {
            "schema_version": "1",
            "event_id": uid(),
            "tenant_id": self.tenant_a,
            "case_id": self.case_id,
            "correlation_id": uid(),
            "type": "case.registered",
            "occurred_at": "2026-11-04T09:00:00Z",
            "idempotency_key": key(),
            "source_service": body["service"],
            "payload": body,
        }

    # --- canonical case identity -------------------------------------------------

    def test_registration_persists_immutable_uuid_and_display_id(self):
        self.assertEqual(str(uuid.UUID(self.case_id)), self.case_id)
        self.assertRegex(self.case["display_id"], r"^CASE-[0-9]{6}$")
        self.assertFalse(self.case["replayed"])
        doc = frappe.get_doc("Vati Case Identity", self.case_id)
        doc.display_id = "CASE-999999"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

    def test_registration_replay_returns_the_same_case(self):
        k = key()
        first = self.m.register_case(tenant_id=self.tenant_a, idempotency_key=k)
        again = self.m.register_case(tenant_id=self.tenant_a, idempotency_key=k)
        self.assertEqual(again["case_id"], first["case_id"])
        self.assertTrue(again["replayed"])
        self.assertEqual(frappe.db.count("Vati Case Identity", {"tenant_id": self.tenant_a}), 2)
        with self.assertRaises(IdempotencyConflict):
            self.m.register_case(tenant_id=self.tenant_a, idempotency_key=k, taxpayer_id=uid())

    def test_display_id_unique_per_tenant_only(self):
        self.m.register_case(tenant_id=self.tenant_a, idempotency_key=key(), display_id="VAT-0001")
        with self.assertRaises(MappingConflict):
            self.m.register_case(
                tenant_id=self.tenant_a, idempotency_key=key(), display_id="VAT-0001"
            )
        other = self.m.register_case(
            tenant_id=self.tenant_b, idempotency_key=key(), display_id="VAT-0001"
        )
        self.assertFalse(other["replayed"])

    def test_case_id_of_another_tenant_cannot_be_registered(self):
        with self.assertRaises(TenantBoundaryError):
            self.m.register_case(
                tenant_id=self.tenant_b, idempotency_key=key(), case_id=self.case_id
            )

    # --- service links -----------------------------------------------------------

    def test_links_to_jube_frappe_and_openaleph_are_persisted(self):
        self.link(external_id="syn-jube-0001")
        self.link(service="openaleph", external_type="collection", external_id="syn-col-1")
        self.link(service="openaleph", external_type="entity", external_id="syn-ent-1")
        self.link(service="openaleph", external_type="entity", external_id="syn-ent-2")
        self.link(service="frappe", external_type="Tax Submission", external_id="SUB-SYN-1")
        # Frappe names are only unique per DocType.
        self.link(service="frappe", external_type="Evidence Request", external_id="SUB-SYN-1")
        links = self.m.case_service_links(tenant_id=self.tenant_a, case_id=self.case_id)
        self.assertEqual(len(links), 6)
        self.assertEqual(
            links[0],
            {
                "service": "jube",
                "external_type": "case",
                "external_id": "syn-jube-0001",
                "sync": {"state": "synced", "last_attempt_at": None, "error_code": None},
            },
        )
        self.assertEqual(
            self.m.resolve_case(
                tenant_id=self.tenant_a,
                service="openaleph",
                external_type="entity",
                external_id="syn-ent-2",
            ),
            self.case_id,
        )

    def test_replaying_the_same_external_event_creates_one_link(self):
        event = self.event()
        first = self.m.apply_link_event(event)
        again = self.m.apply_link_event(dict(event))
        self.assertFalse(first["replayed"])
        self.assertTrue(again["replayed"])
        self.assertEqual(again["service_link"], first["service_link"])
        self.assertEqual(self.count_links(case_id=self.case_id), 1)

    def test_reused_event_id_with_a_changed_mapping_is_rejected(self):
        event = self.event()
        self.m.apply_link_event(event)
        changed = dict(event, payload=dict(event["payload"], external_id="syn-jube-other"))
        with self.assertRaises(IdempotencyConflict):
            self.m.apply_link_event(changed)
        self.assertEqual(self.count_links(case_id=self.case_id), 1)

    def test_upstream_cannot_report_another_services_ids(self):
        event = self.event()
        event["source_service"] = "openaleph"
        with self.assertRaises(InvalidMapping):
            self.m.apply_link_event(event)

    def test_conflicting_mapping_is_rejected(self):
        self.link(external_id="syn-jube-shared")
        other = self.m.register_case(tenant_id=self.tenant_a, idempotency_key=key())
        with self.assertRaises(MappingConflict) as ctx:
            self.link(case_id=other["case_id"], external_id="syn-jube-shared")
        self.assertEqual(ctx.exception.field_errors[0]["code"], "already_mapped")
        with self.assertRaises(MappingConflict) as ctx:
            self.link(external_id="syn-jube-second")  # one Jube case per canonical case
        self.assertEqual(ctx.exception.field_errors[0]["code"], "slot_taken")
        self.assertEqual(self.count_links(case_id=self.case_id), 1)

    def test_database_unique_keys_hold_even_without_the_service(self):
        first = self.link(external_id="syn-jube-db")
        other = self.m.register_case(tenant_id=self.tenant_a, idempotency_key=key())
        duplicate = frappe.get_doc(
            {
                "doctype": "Vati Service Link",
                "tenant_id": self.tenant_a,
                "case_id": other["case_id"],
                "service": "jube",
                "external_type": "case",
                "external_id": first["service_link"]["external_id"],
            }
        )
        with self.assertRaises(frappe.UniqueValidationError):
            duplicate.insert(ignore_permissions=True)

    def test_link_identity_is_immutable(self):
        self.link(external_id="syn-jube-fixed")
        name = frappe.db.get_value("Vati Service Link", {"case_id": self.case_id}, "name")
        doc = frappe.get_doc("Vati Service Link", name)
        doc.external_id = "syn-jube-moved"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        doc.reload()
        doc.sync_state = "failed"
        doc.error_code = "upstream_timeout"
        doc.save(ignore_permissions=True)  # sync fields stay mutable

    def test_tenant_boundary_is_enforced(self):
        self.link(external_id="syn-jube-a")
        # Tenant B cannot attach links to tenant A's case, by service or by direct insert.
        with self.assertRaises(TenantBoundaryError):
            self.link(tenant_id=self.tenant_b, external_id="syn-jube-b")
        foreign = frappe.get_doc(
            {
                "doctype": "Vati Service Link",
                "tenant_id": self.tenant_b,
                "case_id": self.case_id,
                "service": "openaleph",
                "external_type": "entity",
                "external_id": "syn-ent-b",
            }
        )
        with self.assertRaises(frappe.DoesNotExistError):
            foreign.insert(ignore_permissions=True)
        # Tenant B cannot read or resolve tenant A's mapping.
        with self.assertRaises(TenantBoundaryError):
            self.m.case_service_links(tenant_id=self.tenant_b, case_id=self.case_id)
        self.assertIsNone(
            self.m.resolve_case(
                tenant_id=self.tenant_b,
                service="jube",
                external_type="case",
                external_id="syn-jube-a",
            )
        )
        with self.assertRaises(CaseNotFound):
            self.m.case_service_links(tenant_id=self.tenant_a, case_id=uid())
        self.assertEqual(self.count_links(tenant_id=self.tenant_b), 0)

    def test_desk_system_manager_can_read_but_not_create_mappings(self):
        email = "tax030-manager@example.test"
        if not frappe.db.exists("User", email):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": "Synthetic",
                    "send_welcome_email": 0,
                    "roles": [{"role": "System Manager"}],
                }
            ).insert(ignore_permissions=True)
        frappe.set_user(email)
        try:
            self.assertTrue(frappe.has_permission("Vati Service Link", "read"))
            for doctype in ("Vati Case Identity", "Vati Service Link"):
                self.assertFalse(frappe.has_permission(doctype, "create"))
                self.assertFalse(frappe.has_permission(doctype, "write"))
            with self.assertRaises(frappe.PermissionError):
                frappe.get_doc(
                    {
                        "doctype": "Vati Case Identity",
                        "case_id": uid(),
                        "tenant_id": self.tenant_a,
                        "display_id": "CASE-123456",
                    }
                ).insert()
        finally:
            frappe.set_user("Administrator")
