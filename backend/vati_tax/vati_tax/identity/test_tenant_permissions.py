"""Bench integration tests for TAX-029 roles and tenant permissions.

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE set-config allow_tests true
    bench --site SITE run-tests --module vati_tax.identity.test_tenant_permissions

Plain ``unittest discover`` (CI, no bench) skips this module. The tests create a
temporary custom DocType in the ``Vati Tax`` module with ``tenant_id`` and
``taxpayer_id`` fields, so they exercise the same convention that later Vati
DocTypes rely on, and remove it afterwards. All users and IDs are synthetic.
"""

import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    from vati_tax.identity import policy
    from vati_tax.identity.api import get_access_scope
    from vati_tax.identity.install import sync_roles
    from vati_tax.identity.permissions import (
        MEMBERSHIP,
        TENANT,
        MissingTenantContext,
        TenantScopeDenied,
        clear_principal_cache,
        require_action,
    )
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.portal import api as portal_api
    from vati_tax.portal.fixtures import FIXTURE_OWNERS, FIXTURE_TENANT_ID
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

PROBE = "Vati TAX029 Probe"
RUN = uuid.uuid4().hex[:8]


def email(name: str) -> str:
    return f"tax029-{name}-{RUN}@example.test"


def make_user(name: str) -> str:
    user = email(name)
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }
    ).insert(ignore_permissions=True)
    return user


def create_probe_doctype() -> None:
    if frappe.db.exists("DocType", PROBE):
        return
    perms = [
        ("System Manager", {"create": 1, "write": 1, "delete": 1}),
        ("Vati Taxpayer", {"create": 1, "write": 1}),
        ("Vati Auditor", {"write": 1}),
        ("Vati Specialist", {}),
        ("Vati Supervisor", {"write": 1, "delete": 1}),
        ("Vati Service", {"create": 1, "write": 1}),
    ]
    frappe.get_doc(
        {
            "doctype": "DocType",
            "name": PROBE,
            "module": "Vati Tax",
            "custom": 1,
            "autoname": "hash",
            "fields": [
                {"fieldname": "tenant_id", "fieldtype": "Data", "label": "Tenant"},
                {"fieldname": "taxpayer_id", "fieldtype": "Data", "label": "Taxpayer"},
                {"fieldname": "title", "fieldtype": "Data", "label": "Title"},
            ],
            "permissions": [{"role": role, "read": 1, **extra} for role, extra in perms],
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()


def probe(tenant_id: str, taxpayer_id: str | None, title: str = "synthetic"):
    return frappe.get_doc(
        {"doctype": PROBE, "tenant_id": tenant_id, "taxpayer_id": taxpayer_id, "title": title}
    )


class TenantPermissionTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        create_probe_doctype()
        cls.t1 = ensure_tenant(str(uuid.uuid4()), "Synthetic tenant one")
        cls.t2 = ensure_tenant(str(uuid.uuid4()), "Synthetic tenant two")
        cls.tp_a = str(uuid.uuid4())
        cls.tp_b = str(uuid.uuid4())
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer-a", cls.t1, policy.TAXPAYER, cls.tp_a),
            ("taxpayer-b", cls.t1, policy.TAXPAYER, cls.tp_b),
            ("auditor-t1", cls.t1, policy.AUDITOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
            ("specialist-t1", cls.t1, policy.SPECIALIST, None),
            ("supervisor-t1", cls.t1, policy.SUPERVISOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        cls.users["service-t1"] = ensure_service_identity(email("service-t1"), [cls.t1])
        cls.rec_a = probe(cls.t1, cls.tp_a, "taxpayer A").insert().name
        cls.rec_b = probe(cls.t1, cls.tp_b, "taxpayer B").insert().name
        cls.rec_t2 = probe(cls.t2, cls.tp_a, "same taxpayer id, other tenant").insert().name
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.rollback()
        if frappe.db.exists("DocType", PROBE):
            frappe.delete_doc("DocType", PROBE, force=True, ignore_permissions=True)
            frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.request = None

    def as_user(self, name: str) -> str:
        user = self.users[name]
        frappe.set_user(user)
        return user

    def visible(self, doctype: str = PROBE) -> set[str]:
        return set(frappe.get_list(doctype, pluck="name", limit_page_length=0))

    # Roles -----------------------------------------------------------------

    def test_roles_exist_with_expected_desk_access_and_sync_is_idempotent(self):
        for role, name in policy.FRAPPE_ROLES.items():
            self.assertEqual(
                frappe.db.get_value("Role", name, "desk_access"), int(policy.DESK_ACCESS[role])
            )
        self.assertEqual(sync_roles(), [])

    # Cross-taxpayer reads/writes ---------------------------------------------

    def test_taxpayer_lists_only_own_records(self):
        self.as_user("taxpayer-a")
        self.assertEqual(self.visible() & {self.rec_a, self.rec_b, self.rec_t2}, {self.rec_a})

    def test_taxpayer_cannot_read_another_taxpayers_record(self):
        self.as_user("taxpayer-a")
        self.assertTrue(frappe.has_permission(PROBE, "read", self.rec_a))
        self.assertFalse(frappe.has_permission(PROBE, "read", self.rec_b))
        self.assertFalse(frappe.has_permission(PROBE, "read", self.rec_t2))
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc(PROBE, self.rec_b).check_permission("read")

    def test_taxpayer_cannot_write_another_taxpayers_record(self):
        self.as_user("taxpayer-a")
        doc = frappe.get_doc(PROBE, self.rec_b)
        doc.title = "tampered"
        with self.assertRaises(frappe.PermissionError):
            doc.save()
        self.assertEqual(frappe.db.get_value(PROBE, self.rec_b, "title"), "taxpayer B")

    def test_taxpayer_cannot_create_for_another_taxpayer_or_tenant(self):
        self.as_user("taxpayer-a")
        for tenant, taxpayer in [(self.t1, self.tp_b), (self.t2, self.tp_a), (self.t1, None)]:
            with (
                self.subTest(tenant=tenant, taxpayer=taxpayer),
                self.assertRaises(frappe.PermissionError),
            ):
                probe(tenant, taxpayer).insert()
        created = probe(self.t1, self.tp_a, "own").insert()
        self.assertTrue(frappe.db.exists(PROBE, created.name))

    def test_taxpayer_cannot_move_own_record_to_another_taxpayer(self):
        self.as_user("taxpayer-a")
        doc = frappe.get_doc(PROBE, self.rec_a)
        doc.taxpayer_id = self.tp_b
        with self.assertRaises(frappe.PermissionError):
            doc.save()
        self.assertEqual(frappe.db.get_value(PROBE, self.rec_a, "taxpayer_id"), self.tp_a)

    def test_user_without_membership_sees_nothing(self):
        user = make_user("no-membership")
        frappe.get_doc("User", user).add_roles("Vati Auditor")
        frappe.set_user(user)
        self.assertEqual(self.visible() & {self.rec_a, self.rec_b, self.rec_t2}, set())
        self.assertFalse(frappe.has_permission(PROBE, "read", self.rec_a))

    # Cross-tenant staff and service access -------------------------------

    def test_staff_are_confined_to_their_tenant(self):
        records = {self.rec_a, self.rec_b, self.rec_t2}
        for name in ("auditor-t1", "specialist-t1", "supervisor-t1", "service-t1"):
            with self.subTest(user=name):
                self.as_user(name)
                self.assertEqual(self.visible() & records, {self.rec_a, self.rec_b})
                self.assertFalse(frappe.has_permission(PROBE, "read", self.rec_t2))
        self.as_user("auditor-t2")
        self.assertEqual(self.visible() & records, {self.rec_t2})

    def test_staff_cannot_write_other_tenant_records(self):
        self.as_user("auditor-t1")
        doc = frappe.get_doc(PROBE, self.rec_t2)
        doc.title = "tampered"
        with self.assertRaises(frappe.PermissionError):
            doc.save()
        own = frappe.get_doc(PROBE, self.rec_a)
        own.title = "reviewed by auditor"
        own.save()

    def test_service_identity_writes_only_in_member_tenants(self):
        self.as_user("service-t1")
        probe(self.t1, self.tp_a, "service callback").insert()
        with self.assertRaises(frappe.PermissionError):
            probe(self.t2, self.tp_a, "service callback").insert()

    def test_tenant_and_membership_lists_are_tenant_scoped(self):
        self.as_user("auditor-t1")
        self.assertEqual(self.visible(TENANT) & {self.t1, self.t2}, {self.t1})
        self.as_user("supervisor-t1")
        tenants = set(frappe.get_list(MEMBERSHIP, pluck="tenant_id", limit_page_length=0))
        self.assertIn(self.t1, tenants)
        self.assertNotIn(self.t2, tenants)
        self.as_user("taxpayer-a")
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list(MEMBERSHIP, pluck="name")

    def test_document_share_cannot_cross_taxpayer_or_tenant(self):
        for record, user in [(self.rec_b, "taxpayer-a"), (self.rec_a, "auditor-t2")]:
            with self.subTest(record=record, user=user), self.assertRaises(frappe.PermissionError):
                frappe.share.add_docshare(
                    PROBE, record, self.users[user], read=1, flags={"ignore_share_permission": 1}
                )
        frappe.share.add_docshare(
            PROBE,
            self.rec_a,
            self.users["specialist-t1"],
            read=1,
            flags={"ignore_share_permission": 1},
        )

    # Membership lifecycle --------------------------------------------------

    def test_disabled_membership_or_tenant_removes_access(self):
        user = make_user("auditor-temp")
        name = grant_membership(user, self.t1, policy.AUDITOR)
        frappe.set_user(user)
        self.assertTrue(frappe.has_permission(PROBE, "read", self.rec_a))
        frappe.set_user("Administrator")
        frappe.db.set_value(MEMBERSHIP, name, "enabled", 0)
        clear_principal_cache()
        frappe.set_user(user)
        self.assertFalse(frappe.has_permission(PROBE, "read", self.rec_a))

        frappe.set_user("Administrator")
        frappe.db.set_value(MEMBERSHIP, name, "enabled", 1)
        tenant = frappe.get_doc(TENANT, self.t1)
        tenant.enabled = 0
        tenant.save()
        try:
            frappe.set_user(user)
            self.assertFalse(frappe.has_permission(PROBE, "read", self.rec_a))
        finally:
            frappe.set_user("Administrator")
            tenant.reload()
            tenant.enabled = 1
            tenant.save()

    def test_membership_requires_matching_role_and_separate_identities(self):
        user = make_user("no-role")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc(
                {"doctype": MEMBERSHIP, "user": user, "tenant_id": self.t1, "vati_role": "auditor"}
            ).insert()
        taxpayer = self.users["taxpayer-a"]
        frappe.get_doc("User", taxpayer).add_roles("Vati Auditor")
        try:
            with self.assertRaises(frappe.ValidationError):
                frappe.get_doc(
                    {
                        "doctype": MEMBERSHIP,
                        "user": taxpayer,
                        "tenant_id": self.t2,
                        "vati_role": "auditor",
                    }
                ).insert()
        finally:
            frappe.get_doc("User", taxpayer).remove_roles("Vati Auditor")
        with self.assertRaises(frappe.ValidationError):
            grant_membership(self.users["auditor-t2"], self.t1, policy.TAXPAYER, "not-a-uuid")

    def test_only_operators_provision(self):
        self.as_user("supervisor-t1")
        with self.assertRaises(frappe.PermissionError):
            grant_membership(self.users["auditor-t2"], self.t1, policy.SUPERVISOR)

    # Decision actions ------------------------------------------------------

    def test_service_identity_cannot_take_decision_actions(self):
        self.as_user("service-t1")
        for action in sorted(policy.HUMAN_ONLY_ACTIONS):
            with self.subTest(action=action), self.assertRaises(frappe.PermissionError):
                require_action(action, self.t1, proposed_by=self.users["auditor-t1"])
        require_action(policy.INTEGRATION_CALLBACK, self.t1)

    def test_decision_approval_rules(self):
        self.as_user("auditor-t1")
        require_action(policy.DECISION_PROPOSE, self.t1)
        with self.assertRaises(frappe.PermissionError):
            require_action(
                policy.DECISION_APPROVE, self.t1, proposed_by=self.users["specialist-t1"]
            )
        self.as_user("supervisor-t1")
        require_action(policy.DECISION_APPROVE, self.t1, proposed_by=self.users["auditor-t1"])
        with self.assertRaises(frappe.PermissionError):
            require_action(
                policy.DECISION_APPROVE, self.t1, proposed_by=self.users["supervisor-t1"]
            )
        self.as_user("taxpayer-a")
        with self.assertRaises(frappe.PermissionError):
            require_action(policy.DECISION_PROPOSE, self.t1)

    def test_operator_gets_no_decision_bypass(self):
        with self.assertRaises(TenantScopeDenied):
            require_action(policy.DECISION_APPROVE, self.t1, proposed_by=self.users["auditor-t1"])

    def test_tenant_selection_errors(self):
        self.as_user("auditor-t1")
        with self.assertRaises(TenantScopeDenied) as denied:
            require_action(policy.CASE_READ, self.t2)
        self.assertEqual(denied.exception.http_status_code, 403)
        with self.assertRaises(MissingTenantContext) as missing:
            require_action(policy.CASE_READ, "not-a-uuid")
        self.assertEqual(missing.exception.http_status_code, 400)

    def test_access_scope_reads_tenant_header(self):
        self.as_user("supervisor-t1")
        frappe.local.request = Request(
            EnvironBuilder(headers={"X-Vati-Tenant-ID": self.t1.upper()}).get_environ()
        )
        scope = get_access_scope()
        self.assertEqual((scope["tenant_id"], scope["roles"]), (self.t1, ["supervisor"]))
        self.assertIn(policy.DECISION_APPROVE, scope["actions"])
        frappe.local.request = Request(
            EnvironBuilder(headers={"X-Vati-Tenant-ID": self.t2}).get_environ()
        )
        with self.assertRaises(TenantScopeDenied):
            get_access_scope()
        frappe.local.request = Request(EnvironBuilder().get_environ())
        with self.assertRaises(MissingTenantContext):
            get_access_scope()


class PortalScopeTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        ensure_tenant(FIXTURE_TENANT_ID, "Synthetic fixture tenant")
        cls.taxpayer = make_user("portal-taxpayer")
        grant_membership(
            cls.taxpayer, FIXTURE_TENANT_ID, policy.TAXPAYER, FIXTURE_OWNERS["SUB-SYN-0001"]
        )
        cls.officer = make_user("portal-officer")
        grant_membership(cls.officer, FIXTURE_TENANT_ID, policy.AUDITOR)

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_taxpayer_sees_only_own_submissions(self):
        # TAX-092: the list is live; fixture records are no longer served, and the
        # full own-versus-other scope is covered in portal/test_refund_claim_bench.py.
        frappe.set_user(self.taxpayer)
        listed = portal_api.get_submissions()
        self.assertEqual(listed["mode"], "live")
        self.assertNotIn("SUB-SYN-0001", [item["id"] for item in listed["data"]])
        for fixture_id in ("SUB-SYN-0001", "SUB-SYN-0002"):
            with self.assertRaises(frappe.DoesNotExistError):
                portal_api.get_submission(fixture_id)

    def test_non_taxpayers_are_denied(self):
        for user in (self.officer, "Administrator"):
            frappe.set_user(user)
            with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
                portal_api.get_submissions()
