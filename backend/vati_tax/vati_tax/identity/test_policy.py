"""Behaviour tests for the TAX-029 tenant/taxpayer/action policy (no Frappe needed).

The bench integration tests in ``test_tenant_permissions.py`` exercise the same
rules through Frappe DocType permissions on a disposable site.
"""

import unittest

from vati_tax.identity import policy as p

TENANT_1 = "10000000-0000-4000-8000-000000000001"
TENANT_2 = "20000000-0000-4000-8000-000000000002"
TAXPAYER_A = "a0000000-0000-4000-8000-00000000000a"
TAXPAYER_B = "b0000000-0000-4000-8000-00000000000b"


def principal(user: str, *memberships: p.Membership) -> p.Principal:
    return p.Principal(user=user, memberships=tuple(memberships))


TAXPAYER_A_USER = principal("a@example.test", p.Membership(TENANT_1, p.TAXPAYER, TAXPAYER_A))
AUDITOR_T1 = principal("auditor@example.test", p.Membership(TENANT_1, p.AUDITOR))
SPECIALIST_T1 = principal("specialist@example.test", p.Membership(TENANT_1, p.SPECIALIST))
SUPERVISOR_T1 = principal("supervisor@example.test", p.Membership(TENANT_1, p.SUPERVISOR))
SERVICE_T1 = principal("svc@service.example.test", p.Membership(TENANT_1, p.SERVICE))
NOBODY = principal("nobody@example.test")


def scope(who: p.Principal, tenant: str = TENANT_1) -> p.TenantScope:
    return p.resolve_tenant(who, tenant)


class TenantSelectionTests(unittest.TestCase):
    def test_missing_or_malformed_tenant_is_missing_context(self):
        for value in [None, "", "not-a-uuid", TENANT_1 + " ", "{" + TENANT_1 + "}", TENANT_1[:-1]]:
            with self.subTest(value=value):
                with self.assertRaises(p.MissingTenantContext) as ctx:
                    p.resolve_tenant(AUDITOR_T1, value)
                self.assertEqual(ctx.exception.code, "missing_tenant_context")

    def test_header_case_is_normalised(self):
        member = principal("a@example.test", p.Membership(TAXPAYER_A, p.AUDITOR))
        self.assertEqual(p.resolve_tenant(member, TAXPAYER_A.upper()).tenant_id, TAXPAYER_A)

    def test_header_outside_memberships_is_denied(self):
        with self.assertRaises(p.TenantScopeDenied) as ctx:
            p.resolve_tenant(AUDITOR_T1, TENANT_2)
        self.assertEqual(ctx.exception.code, "tenant_scope_denied")

    def test_no_membership_means_no_tenant(self):
        with self.assertRaises(p.TenantScopeDenied):
            p.resolve_tenant(NOBODY, TENANT_1)

    def test_scope_carries_only_selected_tenant(self):
        multi = principal(
            "svc@service.example.test",
            p.Membership(TENANT_1, p.SERVICE),
            p.Membership(TENANT_2, p.SERVICE),
        )
        selected = p.resolve_tenant(multi, TENANT_2)
        self.assertEqual(selected.tenant_id, TENANT_2)
        self.assertEqual(selected.roles, {p.SERVICE})
        self.assertEqual(scope(TAXPAYER_A_USER).taxpayer_ids, {TAXPAYER_A})


class RecordAccessTests(unittest.TestCase):
    def test_taxpayer_reaches_only_own_taxpayer_records(self):
        self.assertTrue(
            p.can_access_record(TAXPAYER_A_USER, TENANT_1, TAXPAYER_A, taxpayer_owned=True)
        )
        self.assertFalse(
            p.can_access_record(TAXPAYER_A_USER, TENANT_1, TAXPAYER_B, taxpayer_owned=True)
        )
        self.assertFalse(p.can_access_record(TAXPAYER_A_USER, TENANT_1, None, taxpayer_owned=True))

    def test_taxpayer_id_does_not_carry_across_tenants(self):
        self.assertFalse(
            p.can_access_record(TAXPAYER_A_USER, TENANT_2, TAXPAYER_A, taxpayer_owned=True)
        )

    def test_taxpayer_never_reaches_tenant_records_without_taxpayer_owner(self):
        self.assertFalse(p.can_access_record(TAXPAYER_A_USER, TENANT_1, None, taxpayer_owned=False))

    def test_staff_and_service_are_bounded_by_tenant(self):
        for who in (AUDITOR_T1, SPECIALIST_T1, SUPERVISOR_T1, SERVICE_T1):
            with self.subTest(user=who.user):
                self.assertTrue(p.can_access_record(who, TENANT_1, TAXPAYER_B, taxpayer_owned=True))
                self.assertFalse(
                    p.can_access_record(who, TENANT_2, TAXPAYER_B, taxpayer_owned=True)
                )

    def test_records_without_tenant_are_unreachable(self):
        for value in (None, ""):
            self.assertFalse(p.can_access_record(SUPERVISOR_T1, value, None, taxpayer_owned=False))

    def test_no_membership_reaches_nothing(self):
        self.assertFalse(p.can_access_record(NOBODY, TENANT_1, TAXPAYER_A, taxpayer_owned=True))


class QueryConditionTests(unittest.TestCase):
    @staticmethod
    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def test_taxpayer_condition_pins_tenant_and_taxpayer(self):
        sql = p.query_condition(TAXPAYER_A_USER, "`tabX`", taxpayer_owned=True, quote=self.quote)
        self.assertEqual(
            sql, f"((`tabX`.`tenant_id` = '{TENANT_1}' and `tabX`.`taxpayer_id` = '{TAXPAYER_A}'))"
        )

    def test_staff_condition_is_tenant_only(self):
        sql = p.query_condition(AUDITOR_T1, "`tabX`", taxpayer_owned=True, quote=self.quote)
        self.assertEqual(sql, f"(`tabX`.`tenant_id` in ('{TENANT_1}'))")

    def test_fail_closed_conditions(self):
        self.assertEqual(
            p.query_condition(NOBODY, "`tabX`", taxpayer_owned=True, quote=self.quote), "1=0"
        )
        self.assertEqual(
            p.query_condition(TAXPAYER_A_USER, "`tabX`", taxpayer_owned=False, quote=self.quote),
            "1=0",
        )

    def test_malformed_membership_values_never_reach_sql(self):
        hostile = principal(
            "x@example.test",
            p.Membership("' or 1=1 -- ", p.AUDITOR),
            p.Membership(TENANT_1, p.TAXPAYER, "' or 1=1 -- "),
        )
        self.assertEqual(
            p.query_condition(hostile, "`tabX`", taxpayer_owned=True, quote=self.quote), "1=0"
        )


class ActionMatrixTests(unittest.TestCase):
    def assertDenied(self, who, action, **kwargs):
        with self.assertRaises(p.ActionDenied):
            p.authorize_action(scope(who), action, **kwargs)

    def test_every_action_has_roles_and_service_holds_no_human_action(self):
        self.assertTrue(p.HUMAN_ONLY_ACTIONS <= set(p.ACTION_ROLES))
        for action in p.HUMAN_ONLY_ACTIONS:
            self.assertNotIn(p.SERVICE, p.ACTION_ROLES[action], action)

    def test_service_identity_cannot_take_decisions(self):
        for action in sorted(p.HUMAN_ONLY_ACTIONS):
            with self.subTest(action=action):
                self.assertDenied(SERVICE_T1, action, proposed_by="auditor@example.test")
        p.authorize_action(scope(SERVICE_T1), p.INTEGRATION_CALLBACK)
        p.authorize_action(scope(SERVICE_T1), p.DRAFT_RECORD)

    def test_service_guard_holds_even_if_matrix_is_widened(self):
        widened = dict(p.ACTION_ROLES)
        widened[p.DECISION_APPROVE] = frozenset(p.ROLES)
        original = p.ACTION_ROLES
        try:
            p.ACTION_ROLES = widened  # type: ignore[misc]
            self.assertDenied(SERVICE_T1, p.DECISION_APPROVE, proposed_by="auditor@example.test")
        finally:
            p.ACTION_ROLES = original  # type: ignore[misc]

    def test_taxpayer_cannot_take_officer_or_decision_actions(self):
        for action in sorted(set(p.ACTION_ROLES) - p.TAXPAYER_OWNED_ACTIONS):
            with self.subTest(action=action):
                self.assertDenied(
                    TAXPAYER_A_USER, action, taxpayer_id=TAXPAYER_A, proposed_by="x@example.test"
                )

    def test_taxpayer_acts_only_for_own_taxpayer(self):
        p.authorize_action(scope(TAXPAYER_A_USER), p.SUBMISSION_CREATE, taxpayer_id=TAXPAYER_A)
        self.assertDenied(TAXPAYER_A_USER, p.SUBMISSION_CREATE, taxpayer_id=TAXPAYER_B)
        self.assertDenied(TAXPAYER_A_USER, p.SUBMISSION_READ, taxpayer_id=None)
        self.assertDenied(TAXPAYER_A_USER, p.EVIDENCE_RESPOND, taxpayer_id=TAXPAYER_B)

    def test_only_supervisor_approves_and_never_own_proposal(self):
        for who in (AUDITOR_T1, SPECIALIST_T1):
            self.assertDenied(who, p.DECISION_APPROVE, proposed_by="someone@example.test")
        p.authorize_action(scope(SUPERVISOR_T1), p.DECISION_APPROVE, proposed_by=AUDITOR_T1.user)
        self.assertDenied(SUPERVISOR_T1, p.DECISION_APPROVE, proposed_by=SUPERVISOR_T1.user)
        self.assertDenied(SUPERVISOR_T1, p.DECISION_APPROVE, proposed_by=SUPERVISOR_T1.user.upper())
        self.assertDenied(SUPERVISOR_T1, p.DECISION_APPROVE)

    def test_officer_role_boundaries(self):
        p.authorize_action(scope(AUDITOR_T1), p.DECISION_PROPOSE)
        p.authorize_action(scope(AUDITOR_T1), p.EVIDENCE_REQUEST_AUTHORISE)
        self.assertDenied(AUDITOR_T1, p.CASE_ASSIGN)
        self.assertDenied(AUDITOR_T1, p.PLAYBOOK_APPROVE)
        self.assertDenied(AUDITOR_T1, p.SPECIALIST_OPINION_RECORD)
        p.authorize_action(scope(SPECIALIST_T1), p.SPECIALIST_OPINION_RECORD)
        self.assertDenied(SPECIALIST_T1, p.EVIDENCE_REQUEST_AUTHORISE)
        self.assertDenied(SPECIALIST_T1, p.SPECIALIST_REFER)
        self.assertDenied(SUPERVISOR_T1, p.SPECIALIST_OPINION_RECORD)
        self.assertDenied(AUDITOR_T1, p.INTEGRATION_CALLBACK)

    def test_unknown_action_is_denied(self):
        self.assertDenied(SUPERVISOR_T1, "refund.pay")

    def test_allowed_actions_match_authorize(self):
        for who in (TAXPAYER_A_USER, AUDITOR_T1, SPECIALIST_T1, SUPERVISOR_T1, SERVICE_T1):
            listed = set(p.allowed_actions(scope(who)))
            self.assertTrue(listed)
            if who is SERVICE_T1:
                self.assertFalse(listed & p.HUMAN_ONLY_ACTIONS)
            for action in listed:
                p.authorize_action(
                    scope(who), action, taxpayer_id=TAXPAYER_A, proposed_by="other@example.test"
                )


class MembershipRuleTests(unittest.TestCase):
    def test_taxpayer_membership_needs_taxpayer_uuid(self):
        for bad in (None, "", "A0000000-0000-4000-8000-00000000000A", "123"):
            with self.subTest(bad=bad), self.assertRaises(p.InvalidMembership):
                p.validate_membership(p.Membership(TENANT_1, p.TAXPAYER, bad))

    def test_staff_membership_cannot_carry_taxpayer(self):
        with self.assertRaises(p.InvalidMembership):
            p.validate_membership(p.Membership(TENANT_1, p.AUDITOR, TAXPAYER_A))

    def test_unknown_role_and_bad_tenant_are_rejected(self):
        with self.assertRaises(p.InvalidMembership):
            p.validate_membership(p.Membership(TENANT_1, "admin"))  # type: ignore[arg-type]
        with self.assertRaises(p.InvalidMembership):
            p.validate_membership(p.Membership("tenant-1", p.AUDITOR))

    def test_identity_kinds_cannot_mix(self):
        for pair in [
            (p.Membership(TENANT_1, p.TAXPAYER, TAXPAYER_A), p.Membership(TENANT_2, p.AUDITOR)),
            (p.Membership(TENANT_1, p.SERVICE), p.Membership(TENANT_2, p.SUPERVISOR)),
            (p.Membership(TENANT_1, p.SERVICE), p.Membership(TENANT_2, p.TAXPAYER, TAXPAYER_A)),
        ]:
            with self.subTest(pair=pair), self.assertRaises(p.InvalidMembership):
                p.validate_user_memberships(pair)

    def test_one_membership_per_tenant(self):
        with self.assertRaises(p.InvalidMembership):
            p.validate_user_memberships(
                [p.Membership(TENANT_1, p.AUDITOR), p.Membership(TENANT_1, p.SUPERVISOR)]
            )

    def test_staff_may_hold_different_roles_in_different_tenants(self):
        p.validate_user_memberships(
            [p.Membership(TENANT_1, p.AUDITOR), p.Membership(TENANT_2, p.SUPERVISOR)]
        )


if __name__ == "__main__":
    unittest.main()


# --- refund confidentiality (TAX-142, ADR-049) -------------------------------------

REFUND_AUDITOR = principal("ra@example.test", p.Membership(TENANT_1, p.AUDITOR, None, True))
REFUND_SPECIALIST = principal("rs@example.test", p.Membership(TENANT_1, p.SPECIALIST, None, True))
REFUND_SUPERVISOR = principal("rv@example.test", p.Membership(TENANT_1, p.SUPERVISOR, None, True))


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class RefundConfidentialityTests(unittest.TestCase):
    def test_only_refund_roles_read_a_refund_case(self):
        allowed = {
            ("service", None): True,
            ("refund supervisor", None): True,
            ("refund supervisor", "someone@example.test"): True,
            ("refund auditor", "ra@example.test"): True,
            ("refund auditor", "RA@example.test"): True,
            ("refund specialist", "rs@example.test"): True,
            ("refund auditor", None): False,
            ("refund auditor", "rs@example.test"): False,
            ("refund specialist", "ra@example.test"): False,
            ("auditor", "auditor@example.test"): False,
            ("specialist", "specialist@example.test"): False,
            ("supervisor", None): False,
        }
        who = {
            "service": SERVICE_T1,
            "refund supervisor": REFUND_SUPERVISOR,
            "refund auditor": REFUND_AUDITOR,
            "refund specialist": REFUND_SPECIALIST,
            "auditor": AUDITOR_T1,
            "specialist": SPECIALIST_T1,
            "supervisor": SUPERVISOR_T1,
        }
        for (name, assignee), expected in allowed.items():
            with self.subTest(who=name, assignee=assignee):
                self.assertEqual(p.may_read_refund_case(scope(who[name]), assignee), expected)

    def test_refund_team_is_carried_into_the_scope_per_tenant(self):
        both = principal(
            "x@example.test",
            p.Membership(TENANT_1, p.AUDITOR, None, True),
            p.Membership(TENANT_2, p.AUDITOR),
        )
        self.assertTrue(scope(both, TENANT_1).refund_team)
        self.assertFalse(scope(both, TENANT_2).refund_team)
        self.assertFalse(p.may_read_refund_case(scope(both, TENANT_2), "x@example.test"))

    def test_only_staff_join_a_refund_team_and_can_be_assigned(self):
        for role in (p.TAXPAYER, p.SERVICE):
            with self.subTest(role=role):
                with self.assertRaises(p.InvalidMembership):
                    p.validate_membership(
                        p.Membership(TENANT_1, role, TAXPAYER_A if role == p.TAXPAYER else None,
                                     True)
                    )  # fmt: skip
        for role in sorted(p.STAFF_ROLES):
            p.validate_membership(p.Membership(TENANT_1, role, None, True))
            self.assertTrue(p.may_be_assigned_refund_case(p.Membership(TENANT_1, role, None, True)))
            self.assertFalse(p.may_be_assigned_refund_case(p.Membership(TENANT_1, role)))
        self.assertFalse(p.may_be_assigned_refund_case(None))
        self.assertFalse(p.may_be_assigned_refund_case(p.Membership(TENANT_1, p.SERVICE)))

    def test_restrictions_and_query_condition(self):
        self.assertEqual(p.refund_restrictions(REFUND_SUPERVISOR), (frozenset(), frozenset()))
        self.assertEqual(p.refund_restrictions(SERVICE_T1), (frozenset(), frozenset()))
        self.assertEqual(p.refund_restrictions(TAXPAYER_A_USER), (frozenset(), frozenset()))
        self.assertEqual(
            p.refund_restrictions(REFUND_AUDITOR), (frozenset({TENANT_1}), frozenset({TENANT_1}))
        )
        self.assertEqual(p.refund_restrictions(SUPERVISOR_T1), (frozenset({TENANT_1}), frozenset()))
        self.assertEqual(
            p.refund_query_condition(REFUND_SUPERVISOR, "t.case_id", by="case", quote=_quote), ""
        )
        plain = p.refund_query_condition(SUPERVISOR_T1, "t.case_id", by="case", quote=_quote)
        self.assertIn("t.case_id is null or t.case_id not in", plain)
        self.assertIn("'refund_claim'", plain)
        self.assertNotIn("Vati Case Assignment", plain)
        mine = p.refund_query_condition(REFUND_AUDITOR, "t.case_id", by="case", quote=_quote)
        self.assertIn("`tabVati Case Assignment`", mine)
        self.assertIn("'ra@example.test'", mine)
        sub = p.refund_query_condition(REFUND_AUDITOR, "t.name", by="submission", quote=_quote)
        self.assertIn("s.`submission_type` = 'refund_claim'", sub)
        self.assertIn("s.`case_id` is null or s.`case_id` not in", sub)
        tricky = principal("o'hara@example.test", p.Membership(TENANT_1, p.AUDITOR, None, True))
        self.assertIn("'o''hara@example.test'",
                      p.refund_query_condition(tricky, "t.case_id", by="case", quote=_quote))  # fmt: skip
