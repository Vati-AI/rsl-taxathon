"""Behaviour tests for canonical ID and service-link rules (no Frappe needed)."""

import unittest
from typing import Any

from vati_tax.ids.canonical import (
    CaseNotFound,
    CaseRegistration,
    IdempotencyConflict,
    InvalidMapping,
    Link,
    MappingConflict,
    StoredCase,
    TenantBoundaryError,
    canonical_uuid,
    decide_case,
    decide_link,
)

TENANT_A = "00000000-0000-4000-8000-00000000000a"
TENANT_B = "00000000-0000-4000-8000-00000000000b"
CASE_1 = "11111111-1111-4111-8111-111111111111"
CASE_2 = "22222222-2222-4222-8222-222222222222"
EVENT_1 = "e0000000-0000-4000-8000-000000000001"
EVENT_2 = "e0000000-0000-4000-8000-000000000002"
KEY = "synthetic-registration-0001"


def link(
    case=CASE_1,
    service="jube",
    external_type="case",
    external_id="J-1",
    event=None,
    tenant=TENANT_A,
):
    return Link.build(tenant, case, service, external_type, external_id, event)


class IdentifierValidationTests(unittest.TestCase):
    def test_uuid_is_canonicalised(self):
        self.assertEqual(canonical_uuid(CASE_1.upper(), "case_id"), CASE_1)

    def test_malformed_identifiers_are_rejected(self):
        for bad in (None, "", "case-1", 7, "11111111-1111-4111-8111-11111111111"):
            with self.assertRaises(InvalidMapping) as ctx:
                canonical_uuid(bad, "case_id")
            self.assertEqual(ctx.exception.code, "invalid_request")
            self.assertEqual(ctx.exception.field_errors[0]["field"], "case_id")

    def test_unknown_service_or_type_is_rejected(self):
        for service, external_type in (
            ("salesforce", "case"),
            ("jube", "collection"),
            ("openaleph", "case"),
            ("frappe", "tabSubmission; DROP"),
        ):
            with self.assertRaises(InvalidMapping):
                link(service=service, external_type=external_type)

    def test_external_id_must_be_bounded_and_printable(self):
        for bad in ("", " J-1", "J-1\n", "x" * 256, None):
            with self.assertRaises(InvalidMapping):
                link(external_id=bad)
        self.assertEqual(link(external_id="x" * 255).external_id, "x" * 255)

    def test_registration_validates_display_and_idempotency_key(self):
        with self.assertRaises(InvalidMapping):
            CaseRegistration.build(TENANT_A, "short")
        with self.assertRaises(InvalidMapping):
            CaseRegistration.build(TENANT_A, KEY, display_id="case-1")
        self.assertEqual(
            CaseRegistration.build(TENANT_A, KEY, display_id="VAT-000123").display_id, "VAT-000123"
        )


class KeyTests(unittest.TestCase):
    def test_link_key_is_tenant_scoped(self):
        self.assertNotEqual(link().link_key, link(tenant=TENANT_B).link_key)

    def test_service_wide_namespace_outside_frappe(self):
        # OpenAleph documents are entities: one ID cannot be mapped twice under two types.
        entity = link(service="openaleph", external_type="entity", external_id="syn-ent-1")
        document = link(service="openaleph", external_type="document", external_id="syn-ent-1")
        self.assertEqual(entity.link_key, document.link_key)

    def test_frappe_names_are_namespaced_by_doctype(self):
        submission = link(service="frappe", external_type="Tax Submission", external_id="SUB-1")
        request = link(service="frappe", external_type="Evidence Request", external_id="SUB-1")
        self.assertNotEqual(submission.link_key, request.link_key)

    def test_only_one_per_case_types_have_a_slot(self):
        self.assertIsNotNone(link().slot_key)
        self.assertIsNotNone(link(service="openaleph", external_type="collection").slot_key)
        self.assertIsNone(link(service="openaleph", external_type="entity").slot_key)
        self.assertIsNone(link(service="frappe", external_type="Tax Submission").slot_key)
        self.assertNotEqual(link().slot_key, link(case=CASE_2).slot_key)

    def test_event_key_only_when_event_is_known(self):
        self.assertIsNone(link().event_key)
        self.assertNotEqual(
            link(event=EVENT_1).event_key, link(event=EVENT_1, tenant=TENANT_B).event_key
        )


class DecideLinkTests(unittest.TestCase):
    def test_new_link_is_created(self):
        self.assertEqual(decide_link(link(event=EVENT_1), TENANT_A, None, None, None), "create")

    def test_replayed_event_is_a_no_op(self):
        stored = link(event=EVENT_1)
        self.assertEqual(
            decide_link(link(event=EVENT_1), TENANT_A, stored, stored, stored), "replay"
        )

    def test_same_mapping_from_a_new_event_is_a_replay(self):
        stored = link(event=EVENT_1)
        self.assertEqual(decide_link(link(event=EVENT_2), TENANT_A, stored, stored, None), "replay")

    def test_reused_event_with_changed_mapping_is_an_idempotency_conflict(self):
        stored = link(event=EVENT_1)
        with self.assertRaises(IdempotencyConflict) as ctx:
            decide_link(link(external_id="J-2", event=EVENT_1), TENANT_A, None, stored, stored)
        self.assertEqual(ctx.exception.http_status, 422)

    def test_external_id_mapped_to_another_case_is_rejected(self):
        stored = link(case=CASE_2)
        with self.assertRaises(MappingConflict) as ctx:
            decide_link(link(), TENANT_A, stored, None, None)
        self.assertEqual(
            ctx.exception.field_errors, [{"field": "external_id", "code": "already_mapped"}]
        )

    def test_same_id_under_another_type_in_the_namespace_is_rejected(self):
        stored = link(service="openaleph", external_type="entity", external_id="syn-1")
        request = link(service="openaleph", external_type="document", external_id="syn-1")
        with self.assertRaises(MappingConflict):
            decide_link(request, TENANT_A, stored, None, None)

    def test_second_record_in_a_one_per_case_slot_is_rejected(self):
        with self.assertRaises(MappingConflict) as ctx:
            decide_link(link(external_id="J-2"), TENANT_A, None, link(), None)
        self.assertEqual(ctx.exception.field_errors[0]["code"], "slot_taken")

    def test_missing_case_is_not_found(self):
        with self.assertRaises(CaseNotFound) as ctx:
            decide_link(link(), None, None, None, None)
        self.assertNotIsInstance(ctx.exception, TenantBoundaryError)

    def test_cross_tenant_case_is_rejected_like_a_missing_case(self):
        with self.assertRaises(TenantBoundaryError) as ctx:
            decide_link(link(tenant=TENANT_B), TENANT_A, None, None, None)
        self.assertEqual(ctx.exception.code, "not_found")
        self.assertEqual(ctx.exception.http_status, 404)
        self.assertNotIn(TENANT_A, str(ctx.exception))

    def test_tenant_check_precedes_replay(self):
        stored = link(event=EVENT_1)
        with self.assertRaises(TenantBoundaryError):
            decide_link(link(event=EVENT_1, tenant=TENANT_B), TENANT_A, stored, stored, stored)

    def test_error_payload_matches_contract_shape(self):
        error = MappingConflict("taken", "external_id", "already_mapped").as_error("c-1")["error"]
        self.assertEqual(
            set(error),
            {"code", "message", "retryable", "correlation_id", "service", "field_errors"},
        )
        self.assertFalse(error["retryable"])


def stored_case(**changes):
    values: dict[str, Any] = dict(
        tenant_id=TENANT_A,
        case_id=CASE_1,
        display_id="CASE-000001",
        taxpayer_id=None,
        submission_id=None,
    )
    values.update(changes)
    return StoredCase(**values)


class DecideCaseTests(unittest.TestCase):
    def test_new_registration_is_created(self):
        self.assertEqual(
            decide_case(CaseRegistration.build(TENANT_A, KEY), None, None, None), ("create", None)
        )

    def test_identical_registration_replays_with_generated_ids(self):
        stored = stored_case()
        action, case = decide_case(CaseRegistration.build(TENANT_A, KEY), stored, None, None)
        self.assertEqual((action, case), ("replay", stored))

    def test_key_reused_for_a_different_registration_conflicts(self):
        request = CaseRegistration.build(TENANT_A, KEY, taxpayer_id=CASE_2)
        with self.assertRaises(IdempotencyConflict):
            decide_case(request, stored_case(), None, None)

    def test_case_id_of_another_tenant_is_rejected_without_disclosure(self):
        request = CaseRegistration.build(TENANT_B, KEY, case_id=CASE_1)
        with self.assertRaises(TenantBoundaryError) as ctx:
            decide_case(request, None, stored_case(), None)
        self.assertEqual(ctx.exception.code, "not_found")

    def test_case_id_registered_under_another_key_conflicts(self):
        request = CaseRegistration.build(TENANT_A, KEY, case_id=CASE_1)
        with self.assertRaises(MappingConflict):
            decide_case(request, None, stored_case(), None)

    def test_display_id_is_unique_per_tenant(self):
        request = CaseRegistration.build(TENANT_A, KEY, display_id="CASE-000001")
        with self.assertRaises(MappingConflict):
            decide_case(request, None, None, stored_case(case_id=CASE_2))


class RslServiceLinkTests(unittest.TestCase):
    """TAX-133: RSL systems join the register under the same tenant and conflict rules."""

    def test_every_rsl_service_has_a_known_record_type(self):
        for service, external_type in (
            ("online_refunds", "claim"),
            ("audit_system", "audit_case"),
            ("psrm", "objection"),
            ("etax", "vat_return"),
            ("idms", "invoice"),
            ("asycuda", "entry"),
        ):
            built = link(service=service, external_type=external_type, external_id="RSL-1")
            self.assertEqual(built.service, service)

    def test_unknown_rsl_types_and_a_tin_link_are_rejected(self):
        for service, external_type in (
            ("online_refunds", "case"),
            ("audit_system", "claim"),
            ("psrm", "taxpayer"),
            ("etax", "tin"),
            ("rsl", "claim"),
        ):
            with self.assertRaises(InvalidMapping):
                link(service=service, external_type=external_type)

    def test_a_case_has_one_rsl_claim_and_one_rsl_audit_case(self):
        for service, external_type in (("online_refunds", "claim"), ("audit_system", "audit_case")):
            first = link(service=service, external_type=external_type, external_id="REF-1")
            second = link(service=service, external_type=external_type, external_id="REF-2")
            with self.assertRaises(MappingConflict) as ctx:
                decide_link(second, TENANT_A, None, first, None)
            self.assertEqual(ctx.exception.field_errors[0]["code"], "slot_taken")

    def test_one_rsl_reference_cannot_belong_to_two_cases(self):
        mine = link(service="audit_system", external_type="audit_case", external_id="AUD-1")
        other = link(
            case=CASE_2, service="audit_system", external_type="audit_case", external_id="AUD-1"
        )
        self.assertEqual(mine.link_key, other.link_key)
        with self.assertRaises(MappingConflict) as ctx:
            decide_link(other, TENANT_A, mine, None, None)
        self.assertEqual(ctx.exception.field_errors[0]["code"], "already_mapped")
        self.assertEqual(decide_link(mine, TENANT_A, mine, mine, None), "replay")

    def test_many_returns_may_back_one_case(self):
        first = link(service="etax", external_type="vat_return", external_id="VR-1")
        second = link(service="etax", external_type="vat_return", external_id="VR-2")
        self.assertIsNone(second.slot_key)
        self.assertEqual(decide_link(second, TENANT_A, None, None, None), "create")
        self.assertNotEqual(first.link_key, second.link_key)

    def test_the_same_rsl_reference_in_another_tenant_is_separate_and_unprobeable(self):
        here = link(service="online_refunds", external_type="claim", external_id="CLM-1")
        there = link(
            tenant=TENANT_B, service="online_refunds", external_type="claim", external_id="CLM-1"
        )
        self.assertNotEqual(here.link_key, there.link_key)
        with self.assertRaises(TenantBoundaryError) as ctx:
            decide_link(there, TENANT_A, None, None, None)
        self.assertEqual(ctx.exception.code, "not_found")


if __name__ == "__main__":
    unittest.main()
