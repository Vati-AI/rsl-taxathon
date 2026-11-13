"""Bench tests for the TAX-031 submission and tax-domain DocTypes.

Run on a disposable site (``scripts/frappe_dev.py verify`` does this)::

    bench --site SITE run-tests --module vati_tax.submissions.test_submission_bench

Under plain ``unittest`` without a site this module is skipped, not passed. All
users, tenants, taxpayers and figures are synthetic.
"""

from __future__ import annotations

import unittest
import uuid

try:
    import frappe
    from frappe.tests import IntegrationTestCase

    from vati_tax.identity import policy
    from vati_tax.identity.permissions import clear_principal_cache
    from vati_tax.identity.provision import ensure_service_identity, ensure_tenant, grant_membership
    from vati_tax.submissions.domain import SIMULATOR_KEY
    from vati_tax.submissions.guard import InvalidDomainInput
except ImportError as exc:  # pragma: no cover - CI without a bench
    raise unittest.SkipTest(f"Frappe is not installed ({exc}); run with bench run-tests")

if not getattr(frappe.local, "site", None):
    raise unittest.SkipTest("Needs a Frappe site; run with bench --site SITE run-tests")

TAXPAYER = "Vati Taxpayer"
SUBMISSION = "Vati Submission"
VAT_RETURN = "Vati VAT Return"
REFUND = "Vati Refund Claim"
IMPORTED = "Vati Imported Service"
DETAILS = (VAT_RETURN, REFUND, IMPORTED)
RUN = uuid.uuid4().hex[:8]


def uid() -> str:
    return str(uuid.uuid4())


def make_user(name: str) -> str:
    user = f"tax031-{name}-{RUN}@example.test"
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": f"Synthetic {name}",
            "send_welcome_email": 0,
        }  # fmt: skip
    ).insert(ignore_permissions=True)
    return user


class SubmissionDocTypeTests(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.t1 = ensure_tenant(uid(), "Synthetic tenant one")
        cls.t2 = ensure_tenant(uid(), "Synthetic tenant two")
        cls.tp_a = cls.taxpayer(cls.t1, "Synthetic Mountain Wholesale", "123456001")
        cls.tp_b = cls.taxpayer(cls.t1, "Synthetic Border Retail", "123456002")
        cls.tp_c = cls.taxpayer(cls.t2, "Synthetic Highland Imports", "123456001")
        cls.users = {}
        grants: list[tuple[str, str, policy.Role, str | None]] = [
            ("taxpayer-a", cls.t1, policy.TAXPAYER, cls.tp_a),
            ("taxpayer-b", cls.t1, policy.TAXPAYER, cls.tp_b),
            ("taxpayer-c", cls.t2, policy.TAXPAYER, cls.tp_c),
            ("auditor-t1", cls.t1, policy.AUDITOR, None),
            ("auditor-t2", cls.t2, policy.AUDITOR, None),
            ("specialist-t1", cls.t1, policy.SPECIALIST, None),
            ("supervisor-t1", cls.t1, policy.SUPERVISOR, None),
        ]
        for name, tenant, role, taxpayer in grants:
            cls.users[name] = make_user(name)
            grant_membership(cls.users[name], tenant, role, taxpayer)
        cls.users["service-t1"] = ensure_service_identity(
            f"tax031-service-{RUN}@example.test", [cls.t1]
        )
        # One draft VAT return submission per taxpayer A and B, created by them.
        cls.sub_a = cls.as_("taxpayer-a", cls.new_submission, cls.t1, cls.tp_a)
        cls.ret_a = cls.as_("taxpayer-a", cls.new_return, cls.sub_a)
        cls.sub_b = cls.as_("taxpayer-b", cls.new_submission, cls.t1, cls.tp_b)
        cls.ret_b = cls.as_("taxpayer-b", cls.new_return, cls.sub_b)
        cls.imp_b = cls.as_("taxpayer-b", cls.new_import, cls.sub_b)
        cls.sub_c = cls.as_("taxpayer-c", cls.new_submission, cls.t2, cls.tp_c)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.rollback()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        clear_principal_cache()
        # TAX-131: portal sends stand in for RSL only where the simulator runs (TAX-125).
        self._simulator = frappe.local.conf.get(SIMULATOR_KEY)
        frappe.local.conf[SIMULATOR_KEY] = True

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.conf[SIMULATOR_KEY] = self._simulator
        frappe.db.rollback()

    # Helpers ---------------------------------------------------------------

    @staticmethod
    def taxpayer(tenant: str, name: str, tin: str) -> str:
        return (
            frappe.get_doc(
                {
                    "doctype": TAXPAYER,
                    "tenant_id": tenant,
                    "display_name": name,
                    "taxpayer_type": "company",
                    "tin": tin,
                }  # fmt: skip
            )
            .insert()
            .name
        )

    @classmethod
    def as_(cls, user: str, fn, *args, **kwargs):
        frappe.set_user(cls.users[user])
        clear_principal_cache()
        try:
            return fn(*args, **kwargs)
        finally:
            frappe.set_user("Administrator")

    @staticmethod
    def new_submission(tenant, taxpayer, submission_type="vat_return", period="2025-Q4", **kw):
        return (
            frappe.get_doc(
                {
                    "doctype": SUBMISSION,
                    "tenant_id": tenant,
                    "taxpayer_id": taxpayer,
                    "submission_type": submission_type,
                    "period": period,
                    **kw,
                }  # fmt: skip
            )
            .insert()
            .name
        )

    @staticmethod
    def parent(submission):
        return frappe.db.get_value(
            SUBMISSION, submission, ["tenant_id", "taxpayer_id"], as_dict=True
        )

    @classmethod
    def new_return(cls, submission, **figures):
        p = cls.parent(submission)
        values = {"taxable_supplies": "0", "output_vat": "0.00", "taxable_purchases": "166666.67",
                  "input_vat": "15000.00", **figures}  # fmt: skip
        return (
            frappe.get_doc(
                {
                    "doctype": VAT_RETURN,
                    "submission_id": submission,
                    "tenant_id": p.tenant_id,
                    "taxpayer_id": p.taxpayer_id,
                    **values,
                }  # fmt: skip
            )
            .insert()
            .name
        )

    @classmethod
    def new_import(cls, submission, **values):
        p = cls.parent(submission)
        row = {"supplier_name": "Synthetic Foreign Consulting", "supplier_country": "ZA",
               "service_description": "Remote consulting", "invoice_date": "2025-11-15",
               "amount": "50000.00", "vat_accounted": "0", "declared_treatment": "unsure",
               "evidence_ids": [uid()], **values}  # fmt: skip
        return (
            frappe.get_doc(
                {
                    "doctype": IMPORTED,
                    "submission_id": submission,
                    "tenant_id": p.tenant_id,
                    "taxpayer_id": p.taxpayer_id,
                    **row,
                }  # fmt: skip
            )
            .insert()
            .name
        )

    def visible(self, user: str, doctype: str) -> set[str]:
        return set(self.as_(user, frappe.get_list, doctype, pluck="name", limit=0))

    def denied(self, user: str, fn, *args, exc=frappe.PermissionError):
        with self.assertRaises(exc):
            self.as_(user, fn, *args)

    # Valid flow ------------------------------------------------------------

    def test_valid_flow_stores_exact_decimals_and_server_fields(self):
        sub = frappe.get_doc(SUBMISSION, self.sub_a)
        self.assertEqual(sub.name, sub.submission_id)
        self.assertEqual((sub.status, sub.version, sub.currency), ("draft", 1, "LSL"))
        self.assertTrue(sub.reference.startswith("VAT-"))
        self.assertEqual((str(sub.period_start), str(sub.period_end)), ("2025-10-01", "2025-12-31"))
        ret = frappe.get_doc(VAT_RETURN, self.ret_a)
        self.assertEqual(ret.period, "2025-Q4")
        # The DECIMAL columns hold the exact declared values.
        raw = frappe.db.sql(
            "select cast(taxable_purchases as char), cast(net_vat as char)"
            " from `tabVati VAT Return` where name=%s",
            self.ret_a,
        )[0]
        self.assertEqual([v.rstrip("0") for v in raw], ["166666.67", "-15000."])

    def test_a_send_rsl_did_not_refer_is_refused_without_the_simulator(self):
        # TAX-131 (ADR-043 §5): without the simulator a portal send is not a referred case.
        frappe.local.conf[SIMULATOR_KEY] = None

        def declare_and_submit():
            doc = frappe.get_doc(SUBMISSION, self.sub_a)
            doc.declaration = 1
            doc.save()
            doc.status = "submitted"
            doc.save()

        with self.assertRaises(frappe.PermissionError) as ctx:
            self.as_("taxpayer-a", declare_and_submit)
        self.assertIn("Online Refunds Application", str(ctx.exception))
        self.assertEqual(frappe.db.get_value(SUBMISSION, self.sub_a, "status"), "draft")
        self.assertIsNone(frappe.db.get_value(SUBMISSION, self.sub_a, "case_id"))

    def test_submit_requires_declaration_and_figures_then_locks(self):
        def submit(name):
            doc = frappe.get_doc(SUBMISSION, name)
            doc.status = "submitted"
            doc.save()
            return doc

        empty = self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a,
                         declaration=1)  # fmt: skip
        with self.assertRaises(InvalidDomainInput):
            self.as_("taxpayer-a", submit, empty)  # no VAT return figures
        with self.assertRaises(InvalidDomainInput):
            self.as_("taxpayer-a", submit, self.sub_a)  # declaration not confirmed

        def declare_and_submit():
            doc = frappe.get_doc(SUBMISSION, self.sub_a)
            doc.declaration = 1
            doc.save()
            return submit(self.sub_a)

        doc = self.as_("taxpayer-a", declare_and_submit)
        self.assertEqual((doc.status, doc.version, doc.submitted_by),
                         ("submitted", 3, self.users["taxpayer-a"]))  # fmt: skip

        def edit_return():
            ret = frappe.get_doc(VAT_RETURN, self.ret_a)
            ret.input_vat = "20000.00"
            ret.save()

        self.denied("taxpayer-a", edit_return)
        self.denied("taxpayer-a", self.new_import, self.sub_a)
        self.denied("taxpayer-a", frappe.delete_doc, VAT_RETURN, self.ret_a)

        def withdraw():
            d = frappe.get_doc(SUBMISSION, self.sub_a)
            d.status = "withdrawn"
            d.save()

        self.denied("taxpayer-a", withdraw)

        # Officer asks for a correction; the taxpayer may then edit and resubmit.
        def needs_correction(reason):
            d = frappe.get_doc(SUBMISSION, self.sub_a)
            d.status = "correction_needed"
            d.correction_reason = reason
            d.save()

        self.denied("taxpayer-a", needs_correction, "self-service")
        self.denied("service-t1", needs_correction, "automated")
        self.denied("specialist-t1", needs_correction, "specialist")
        with self.assertRaises(InvalidDomainInput):
            self.as_("auditor-t1", needs_correction, "")
        self.as_("auditor-t1", needs_correction, "Add the second purchase invoice.")
        self.as_("taxpayer-a", edit_return)
        doc = self.as_("taxpayer-a", submit, self.sub_a)
        self.assertEqual((doc.status, doc.correction_reason), ("submitted", None))

    def test_refund_claim_cannot_exceed_linked_return(self):
        # Make A's VAT return refer to a refund submission for the same period.
        sub = self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a, "refund_claim")

        def claim(amount, vat_return=self.ret_a, reason="excess_input_vat"):
            return (
                frappe.get_doc(
                    {
                        "doctype": REFUND,
                        "submission_id": sub,
                        "tenant_id": self.t1,
                        "taxpayer_id": self.tp_a,
                        "claimed_amount": amount,
                        "claim_reason": reason,
                        "vat_return_id": vat_return,
                    }  # fmt: skip
                )
                .insert()
                .name
            )

        with self.assertRaises(InvalidDomainInput):
            self.as_("taxpayer-a", claim, "15000.01")
        with self.assertRaises(InvalidDomainInput):
            self.as_("taxpayer-a", claim, "0.00")
        with self.assertRaises(InvalidDomainInput):  # taxpayer B's return
            self.as_("taxpayer-a", claim, "100.00", self.ret_b)
        name = self.as_("taxpayer-a", claim, "15000.00")
        self.assertEqual(frappe.db.get_value(REFUND, name, "claimed_amount"), 15000.0)
        with self.assertRaises(frappe.UniqueValidationError):  # one claim per submission
            self.as_("taxpayer-a", claim, "1.00")
        with self.assertRaises(InvalidDomainInput):  # a refund claim is not a VAT return
            self.as_("taxpayer-a", self.new_return, sub)

    def test_idempotency_key_is_unique_per_taxpayer(self):
        key = "portal-retry-" + uuid.uuid4().hex
        self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a, idempotency_key=key)
        with self.assertRaises(frappe.UniqueValidationError):
            self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a, idempotency_key=key)
        self.as_("taxpayer-b", self.new_submission, self.t1, self.tp_b, idempotency_key=key)

    def test_taxpayer_registry_rules(self):
        doc = frappe.get_doc(TAXPAYER, self.tp_a)
        self.assertEqual((doc.tin_masked, doc.taxpayer_id), ("******001", doc.name))
        with self.assertRaises(frappe.UniqueValidationError):  # TIN unique per tenant
            self.taxpayer(self.t1, "Duplicate", "123456001")
        for bad in ({"tin": "12AB"}, {"taxpayer_type": "alien"}, {"registered_on": "2099-01-01"}):
            with self.subTest(bad=bad), self.assertRaises(InvalidDomainInput):
                frappe.get_doc(
                    {
                        "doctype": TAXPAYER,
                        "tenant_id": self.t1,
                        "display_name": "Synthetic X",
                        "taxpayer_type": "company",
                        **bad,
                    }  # fmt: skip
                ).insert()

    # Malformed amounts and invalid periods ---------------------------------------

    def test_malformed_amounts_are_rejected_not_cast_to_zero(self):
        sub = self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a)
        for value in ("abc", "1e5", "12,50", "1.234", "-1.00", "NaN", "1000000000000", None, True):
            with self.subTest(value=value), self.assertRaises(InvalidDomainInput):
                self.as_("taxpayer-a", self.new_return, sub, input_vat=value)
        self.assertFalse(frappe.db.exists(VAT_RETURN, {"submission_id": sub}))
        for field, value in [("amount", "0"), ("amount", "5,000"), ("vat_accounted", "-1")]:
            with self.subTest(field=field, value=value), self.assertRaises(InvalidDomainInput):
                self.as_("taxpayer-a", self.new_import, sub, **{field: value})
        with self.assertRaises(InvalidDomainInput):
            frappe.get_doc(
                {
                    "doctype": VAT_RETURN,
                    "submission_id": sub,
                    "tenant_id": self.t1,
                    "taxpayer_id": self.tp_a,
                    "currency": "ZAR",
                    "taxable_supplies": "0",
                    "output_vat": "0",
                    "taxable_purchases": "0",
                    "input_vat": "0",
                }  # fmt: skip
            ).insert()

    def test_invalid_periods_are_rejected(self):
        for period in ("2026-13", "2026-Q5", "26-09", "2026-9", "1999-12", "2099-01", ""):
            with (
                self.subTest(period=period),
                self.assertRaises((InvalidDomainInput, frappe.MandatoryError)),
            ):
                self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a, period=period)
        sub = self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a, period="2025-11")
        self.assertEqual(
            frappe.db.get_value(SUBMISSION, sub, "period_end").isoformat(), "2025-11-30"
        )

        def change_period():
            d = frappe.get_doc(SUBMISSION, sub)
            d.period = "2025-10"
            d.save()

        self.denied("taxpayer-a", change_period, exc=frappe.CannotChangeConstantError)
        for values in ({"invoice_date": "2026-01-01"}, {"supplier_country": "LS"}):
            with self.subTest(values=values), self.assertRaises(InvalidDomainInput):
                self.as_("taxpayer-a", self.new_import, self.sub_a, **values)

    # Unauthorized writes and isolation ---------------------------------------

    def test_taxpayers_only_reach_their_own_records(self):
        expected = {
            TAXPAYER: ({self.tp_a}, {self.tp_b, self.tp_c}),
            SUBMISSION: ({self.sub_a}, {self.sub_b, self.sub_c}),
            VAT_RETURN: ({self.ret_a}, {self.ret_b}),
            IMPORTED: (set(), {self.imp_b}),
        }
        for doctype, (own, others) in expected.items():
            with self.subTest(doctype=doctype):
                seen = self.visible("taxpayer-a", doctype)
                self.assertTrue(own <= seen)
                self.assertFalse(seen & others)
                for name in others:
                    self.denied("taxpayer-a", lambda d=doctype, n=name: frappe.get_doc(d, n)
                                .check_permission("read"))  # fmt: skip

    def test_taxpayer_cannot_write_another_taxpayers_records(self):
        def tamper(doctype, name, field, value):
            doc = frappe.get_doc(doctype, name)
            doc.set(field, value)
            doc.save()

        for doctype, name, field, value in [
            (SUBMISSION, self.sub_b, "contact_email", "x@example.test"),
            (VAT_RETURN, self.ret_b, "input_vat", "1.00"),
            (IMPORTED, self.imp_b, "amount", "1.00"),
            (TAXPAYER, self.tp_b, "display_name", "Tampered"),
        ]:
            with self.subTest(doctype=doctype):
                self.denied("taxpayer-a", tamper, doctype, name, field, value)
        self.denied("taxpayer-a", frappe.delete_doc, IMPORTED, self.imp_b)
        self.assertEqual(frappe.db.get_value(VAT_RETURN, self.ret_b, "input_vat"), 15000.0)

    def test_taxpayer_cannot_create_for_another_taxpayer_or_tenant(self):
        for tenant, taxpayer in [(self.t1, self.tp_b), (self.t2, self.tp_c), (self.t2, self.tp_a)]:
            with self.subTest(tenant=tenant, taxpayer=taxpayer):
                self.denied("taxpayer-a", self.new_submission, tenant, taxpayer)

        # Own tenant/taxpayer on the record, but pointing at B's submission.
        def attach_to_b():
            return frappe.get_doc(
                {
                    "doctype": IMPORTED,
                    "submission_id": self.sub_b,
                    "tenant_id": self.t1,
                    "taxpayer_id": self.tp_a,
                    "supplier_name": "X",
                    "supplier_country": "ZA",
                    "service_description": "X",
                    "invoice_date": "2025-11-01",
                    "amount": "1",
                    "vat_accounted": "0",
                    "declared_treatment": "unsure",
                }  # fmt: skip
            ).insert()

        self.denied("taxpayer-a", attach_to_b, exc=InvalidDomainInput)
        # Operator path: a taxpayer from another tenant on a tenant-one submission.
        with self.assertRaises(InvalidDomainInput):
            self.new_submission(self.t1, self.tp_c)

    def test_taxpayer_cannot_set_server_or_officer_fields(self):
        sub = self.as_("taxpayer-a", self.new_submission, self.t1, self.tp_a,
                       status="submitted", case_id=uid(), version=9)  # fmt: skip
        self.assertEqual(frappe.db.get_value(SUBMISSION, sub, ["status", "case_id", "version"]),
                         ("draft", None, 1))  # fmt: skip
        for field, value in [
            ("case_id", uid()),
            ("correction_reason", "self-approved"),
            ("submitted_at", "2025-01-01 00:00:00"),
            ("version", 7),
            ("reference", "VAT-0000-000001"),
            ("status", "correction_needed"),
        ]:

            def tamper(f=field, v=value):
                doc = frappe.get_doc(SUBMISSION, sub)
                doc.set(f, v)
                doc.save()

            with self.subTest(field=field):
                self.denied("taxpayer-a", tamper)

    def test_staff_read_in_tenant_but_never_rewrite_declared_figures(self):
        for user in ("auditor-t1", "specialist-t1", "supervisor-t1", "service-t1"):
            with self.subTest(user=user):
                self.assertTrue({self.sub_a, self.sub_b} <= self.visible(user, SUBMISSION))
                self.assertNotIn(self.sub_c, self.visible(user, SUBMISSION))
                self.assertTrue({self.ret_a, self.ret_b} <= self.visible(user, VAT_RETURN))

                def tamper():
                    doc = frappe.get_doc(VAT_RETURN, self.ret_a)
                    doc.input_vat = "99999.00"
                    doc.save()

                self.denied(user, tamper)
                self.denied(user, self.new_import, self.sub_a)
                self.denied(user, self.new_submission, self.t1, self.tp_a)
        self.assertEqual(self.visible("auditor-t2", SUBMISSION) & {self.sub_a, self.sub_b}, set())
        self.denied("auditor-t2", lambda: frappe.get_doc(SUBMISSION, self.sub_a)
                    .check_permission("read"))  # fmt: skip

        def auditor_edits_declaration():
            doc = frappe.get_doc(SUBMISSION, self.sub_b)
            doc.contact_email = "officer@example.test"
            doc.save()

        self.denied("auditor-t1", auditor_edits_declaration)

    def test_service_links_case_once_and_only_in_its_tenant(self):
        case_id = uid()

        def link(name, value):
            doc = frappe.get_doc(SUBMISSION, name)
            doc.case_id = value
            doc.save()

        self.as_("service-t1", link, self.sub_b, case_id)
        self.assertEqual(frappe.db.get_value(SUBMISSION, self.sub_b, "case_id"), case_id)
        self.denied("service-t1", link, self.sub_b, uid(), exc=frappe.CannotChangeConstantError)
        self.denied("service-t1", link, self.sub_c, uid())
        self.denied("auditor-t1", link, self.sub_a, uid())
