"""TAX-038 Docling extraction adapter against a contract-faithful fake Docling Serve.

The fake follows the locked docling-serve 1.38.2 routes (``/v1/convert/file/async``,
``/v1/status/poll/{task_id}``, ``/v1/result/{task_id}``) and the TAX-028 recorded smoke
result: a two-page scanned synthetic invoice with OCR text on page 1 and a 4x3 table on
page 2, and policy rejections reported as HTTP 200 ``status: failure``. Every recorded
payload is validated with the real ``vati_tax.evidence.rules.parse_extraction``.
"""

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "vati_tax"))
sys.path.insert(0, str(ROOT / "integration" / "docling"))

import extraction_adapter as da  # noqa: E402
from vati_tax.evidence import rules  # noqa: E402

TENANT = "11111111-1111-4111-8111-111111111111"
EVIDENCE = "22222222-2222-4222-8222-222222222222"
API_KEY = "synthetic-docling-key-0123456789"
CONTENT = b"%PDF-1.4\n% synthetic two-page scanned invoice (test bytes only)\n"
SHA = hashlib.sha256(CONTENT).hexdigest()


def box(left, top, right, bottom):
    return {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": "BOTTOMLEFT"}


def cell(row, col, text, left):
    return {
        "bbox": box(left, 700 - row * 40, left + 100, 660 - row * 40),
        "row_span": 1,
        "col_span": 1,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row + 1,
        "start_col_offset_idx": col,
        "end_col_offset_idx": col + 1,
        "text": text,
        "column_header": row == 0,
        "row_header": False,
    }


ROWS = [
    ["Item", "Qty", "Amount LSL"],
    ["Consulting services", "1", "3000.00"],
    ["Freight", "2", "450.00"],
    ["Total", "", "3450.00"],
]
DOCUMENT = {
    "schema_name": "DoclingDocument",
    "pages": {
        "1": {"page_no": 1, "size": {"width": 595, "height": 842}},
        "2": {"page_no": 2, "size": {"width": 595, "height": 842}},
    },
    "texts": [
        {
            "self_ref": "#/texts/0",
            "label": "section_header",
            "text": "SYNTHETIC SCANNED INVOICE - NOT REAL TAXPAYER DATA",
            "prov": [{"page_no": 1, "bbox": box(50, 800, 545, 780), "charspan": [0, 50]}],
        },
        {
            "self_ref": "#/texts/1",
            "label": "text",
            "text": "Invoice number SYNINV0042",
            "prov": [{"page_no": 1, "bbox": box(50, 760, 400, 740), "charspan": [0, 25]}],
        },
        {"self_ref": "#/texts/2", "label": "text", "text": "no location", "prov": []},
        {
            "self_ref": "#/texts/3",
            "label": "text",
            "text": "Bad box keeps page",
            "prov": [{"page_no": 1, "bbox": box(300, 100, 200, 200)}],
        },
    ],
    "tables": [
        {
            "self_ref": "#/tables/0",
            "label": "table",
            "prov": [{"page_no": 2, "bbox": box(50, 720, 550, 540)}],
            "data": {
                "num_rows": 4,
                "num_cols": 3,
                "table_cells": [
                    cell(r, c, t, 50 + c * 150)
                    for r, row in enumerate(ROWS)
                    for c, t in enumerate(row)
                    if t
                ],
            },
        },
    ],
}


def convert_response(status="success", document=DOCUMENT, errors=()):
    return {
        "document": {"json_content": document, "md_content": ""},
        "status": status,
        "processing_time": 10.5,
        "timings": {},
        "errors": list(errors),
    }


class FakeDocling:
    """Scriptable Docling Serve: each poll pops the next status; default finishes at once."""

    def __init__(self, result=None, statuses=("success",)):
        self.result = convert_response() if result is None else result
        self.statuses = list(statuses)
        self.calls: list[tuple[str, str]] = []
        self.submits = 0
        self.tasks: set[str] = set()
        self.fail_next: list[object] = []  # Response or TransportError to inject per call
        self.api_key_seen: set[str] = set()

    def __call__(self, method, path, body=None, content_type=None):
        self.calls.append((method, path.split("?")[0]))
        if self.fail_next:
            injected = self.fail_next.pop(0)
            if isinstance(injected, Exception):
                raise injected
            return injected
        if method == "POST" and path == "/v1/convert/file/async":
            assert content_type.startswith("multipart/form-data")
            assert b'name="files"' in body and CONTENT in body
            assert b"/v1/convert/source" not in body
            self.submits += 1
            task_id = f"task-{self.submits}"
            self.tasks.add(task_id)
            return da.Response(
                200, {"task_id": task_id, "task_status": "pending", "task_position": 1}
            )
        task_id = path.split("?")[0].rsplit("/", 1)[-1]
        if task_id not in self.tasks:
            return da.Response(404, {"detail": "Task not found."})
        if path.startswith("/v1/status/poll/"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return da.Response(200, {"task_id": task_id, "task_status": status, "task_position": 0})
        if path.startswith("/v1/result/"):
            return da.Response(200, copy.deepcopy(self.result))
        return da.Response(404, None)


class RecordingSink:
    """Validates with the real evidence rules and replays by idempotency key (ADR-011)."""

    def __init__(self):
        self.revisions: dict[str, tuple[str, dict]] = {}
        self.payloads: list[dict] = []
        self.calls = 0
        self.fail_times = 0

    def __call__(self, *, tenant_id, evidence_id, document_sha256, idempotency_key, result):
        self.calls += 1
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("synthetic evidence store outage")
        self.payloads.append(copy.deepcopy(result))
        parsed = rules.parse_extraction(result)
        digest = parsed.digest(evidence_id, document_sha256)
        if idempotency_key in self.revisions:
            stored_digest, view = self.revisions[idempotency_key]
            assert stored_digest == digest, "idempotency conflict"
            return {**view, "replayed": True}
        view = {
            "revision": len(self.revisions) + 1,
            "status": parsed.status,
            "items": [
                {"locator": i.locator(), "label": i.label, "field": i.field} for i in parsed.items
            ],
            "replayed": False,
        }
        self.revisions[idempotency_key] = (digest, view)
        return view


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += max(seconds, 0.5)


class DoclingAdapterTests(unittest.TestCase):
    def make(self, fake=None, deadline_s=60.0, jobs=None, sink=None):
        self.fake = fake or FakeDocling()
        self.sink = sink or RecordingSink()
        self.jobs = jobs or da.InMemoryJobStore()
        self.clock = Clock()
        config = da.AdapterConfig.build(
            base_url="http://127.0.0.1:55001",
            api_key=API_KEY,
            deadline_s=deadline_s,
            poll_wait_s=5.0,
        )
        return da.DoclingExtractionAdapter(
            config,
            jobs=self.jobs,
            sink=self.sink,
            transport=self.fake,
            sleep=self.clock.sleep,
            clock=self.clock,
        )

    def extract(self, adapter, **overrides):
        args = {
            "tenant_id": TENANT,
            "evidence_id": EVIDENCE,
            "document_sha256": SHA,
            "media_type": "application/pdf",
            "content": CONTENT,
            "correlation_id": "corr-1",
        }
        return adapter.extract(**{**args, **overrides})

    # --- success, OCR and table provenance -------------------------------------

    def test_scanned_invoice_records_ocr_text_and_table_cells_with_page_references(self) -> None:
        outcome = self.extract(self.make())
        self.assertEqual(outcome.status, "success")
        self.assertIsNone(outcome.error_code)
        self.assertEqual(self.fake.submits, 1)
        self.assertEqual(len(self.sink.revisions), 1)
        self.assertTrue(all("/v1/convert/source" not in path for _, path in self.fake.calls))
        (recorded,) = self.sink.payloads
        self.assertEqual(
            (recorded["extractor"], recorded["extractor_version"]),
            ("docling", "docling-serve-1.38.2"),
        )
        parsed = rules.parse_extraction(recorded)  # real ADR-011 validation
        self.assertEqual(parsed.page_count, 2)
        by_text = {i.text: i for i in parsed.items}
        ocr = by_text["Invoice number SYNINV0042"]
        self.assertEqual(ocr.page, 1)
        self.assertIsNotNone(ocr.bbox)
        amount = by_text["3000.00"]
        self.assertEqual(
            (amount.page, amount.record, amount.label), (2, "tables/0/r1/c2", "table_cell")
        )
        self.assertEqual(by_text["3450.00"].record, "tables/0/r3/c2")
        self.assertNotIn("no location", by_text)  # uncitable text is dropped
        self.assertIsNone(by_text["Bad box keeps page"].bbox)
        self.assertEqual(by_text["Bad box keeps page"].page, 1)
        self.assertEqual(outcome.recorded["status"], "success")
        self.assertEqual(self.jobs.tasks, {})  # job cleared after recording

    # --- timeout and async polling ---------------------------------------------

    def test_deadline_timeout_keeps_job_and_next_attempt_resumes_without_resubmitting(self) -> None:
        fake = FakeDocling(statuses=["pending"] * 200)
        adapter = self.make(fake, deadline_s=10.0)
        with self.assertRaises(da.DoclingAdapterError) as raised:
            self.extract(adapter)
        error = raised.exception
        self.assertEqual(
            (error.code, error.reason, error.retryable),
            ("upstream_unavailable", "deadline_exceeded", True),
        )
        self.assertEqual(self.sink.calls, 0)
        self.assertEqual(len(self.jobs.tasks), 1)
        fake.statuses = ["started", "success"]
        outcome = self.extract(self.make(fake, jobs=self.jobs, sink=self.sink))
        self.assertTrue(outcome.resumed)
        self.assertEqual(fake.submits, 1)
        self.assertEqual(outcome.status, "success")

    def test_transient_poll_failures_are_retried_within_three_attempts(self) -> None:
        fake = FakeDocling()
        adapter = self.make(fake)
        original = fake.__call__
        # Submit succeeds; the first poll times out, the second gets 503, the third succeeds.
        script = [da.TransportError(timed_out=True), da.Response(503, None, retry_after=2.0)]

        def transport(method, path, body=None, ctype=None):
            if method == "GET" and script:
                injected = script.pop(0)
                if isinstance(injected, Exception):
                    raise injected
                return injected
            return original(method, path, body, ctype)

        adapter._send = transport
        outcome = self.extract(adapter)
        self.assertEqual(outcome.status, "success")
        self.assertIn(2.0, self.clock.slept)  # honoured Retry-After
        self.assertEqual(fake.submits, 1)

    def test_persistent_upstream_timeout_is_a_retryable_unavailable_error(self) -> None:
        fake = FakeDocling()
        adapter = self.make(fake)
        original = fake.__call__

        def transport(method, path, body=None, ctype=None):
            if method == "GET":
                raise da.TransportError(timed_out=True)
            return original(method, path, body, ctype)

        adapter._send = transport
        with self.assertRaises(da.DoclingAdapterError) as raised:
            self.extract(adapter)
        self.assertEqual((raised.exception.reason, raised.exception.retryable), ("timeout", True))
        self.assertEqual(self.sink.calls, 0)

    def test_submission_is_not_blindly_retried(self) -> None:
        fake = FakeDocling()
        fake.fail_next = [da.TransportError(timed_out=True)]
        with self.assertRaises(da.DoclingAdapterError) as raised:
            self.extract(self.make(fake))
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(
            [c for c in fake.calls if c[0] == "POST"], [("POST", "/v1/convert/file/async")]
        )
        self.assertEqual(self.jobs.tasks, {})

    def test_lost_task_clears_job_so_the_next_attempt_resubmits(self) -> None:
        fake = FakeDocling(statuses=["pending"])
        adapter = self.make(fake)
        fake.tasks.clear()
        jobs = self.jobs
        jobs.put(da.job_key(TENANT, EVIDENCE, SHA, da.DEFAULT_EXTRACTOR_VERSION), "task-gone")
        with self.assertRaises(da.DoclingAdapterError) as raised:
            self.extract(adapter)
        self.assertEqual(raised.exception.reason, "task_lost")
        self.assertEqual(jobs.tasks, {})
        fake.statuses = ["success"]
        self.assertEqual(self.extract(self.make(fake, jobs=jobs, sink=self.sink)).status, "success")
        self.assertEqual(fake.submits, 1)

    # --- malformed documents and Docling-reported failures ---------------------

    def test_malformed_document_is_recorded_as_failed_revision_without_items(self) -> None:
        cases = {
            "docling_failure": convert_response(
                "failure",
                {},
                [
                    {
                        "component_type": "document_backend",
                        "module_name": "pdf",
                        "error_message": "Malformed PDF",
                        "category": "conversion",
                    }
                ],
            ),
            "docling_policy_rejected": convert_response(
                "failure",
                {},
                [{"error_message": "exceeds the max_file_size limit", "category": "policy"}],
            ),
            "docling_skipped": convert_response("skipped", {}),
            "docling_task_failure": {"failure": {"error_message": "worker crashed"}},
            "docling_missing_content": convert_response("success", {}),
        }
        for code, body in cases.items():
            with self.subTest(code=code):
                outcome = self.extract(self.make(FakeDocling(result=body)))
                self.assertEqual(outcome.status, "failure")
                self.assertEqual(outcome.error_code, code)
                self.assertEqual(outcome.recorded["items"], [])
                (key,) = self.sink.revisions
                self.assertEqual(self.sink.revisions[key][1]["status"], "failure")

    def test_partial_success_is_recorded_as_partial_with_items_and_error_code(self) -> None:
        body = convert_response(
            "partial_success", errors=[{"error_message": "page 2 OCR", "category": "conversion"}]
        )
        outcome = self.extract(self.make(FakeDocling(result=body)))
        self.assertEqual((outcome.status, outcome.error_code), ("partial", "partial_extraction"))
        self.assertTrue(outcome.recorded["items"])

    def test_oversized_extraction_is_truncated_to_store_limits_as_partial(self) -> None:
        doc = copy.deepcopy(DOCUMENT)
        doc["texts"] = [
            {
                "self_ref": f"#/texts/{i}",
                "label": "text",
                "text": f"line {i}",
                "prov": [{"page_no": 1, "bbox": box(1, 20, 10, 10)}],
            }
            for i in range(da.MAX_ITEMS + 10)
        ]
        result = da.map_conversion(convert_response(document=doc), da.DEFAULT_EXTRACTOR_VERSION)
        self.assertEqual(
            (result["status"], result["error_code"]), ("partial", "extraction_truncated")
        )
        self.assertEqual(len(rules.parse_extraction(result).items), da.MAX_ITEMS)

    def test_invalid_input_is_refused_before_any_network_call(self) -> None:
        cases = {
            "hash_mismatch": {"content": CONTENT + b"x"},
            "unsupported_media_type": {"media_type": "text/html"},
            "invalid_uuid": {"tenant_id": "tenant-1"},
            "invalid_sha256": {"document_sha256": "abc"},
        }
        for reason, change in cases.items():
            with self.subTest(reason=reason):
                adapter = self.make()
                with self.assertRaises(da.DoclingAdapterError) as raised:
                    self.extract(adapter, **change)
                self.assertEqual(
                    (raised.exception.code, raised.exception.reason),
                    ("invalid_domain_input", reason),
                )
                self.assertEqual(self.fake.calls, [])

    # --- authentication and error hygiene ---------------------------------------

    def test_rejected_credentials_fail_without_retry_or_recording(self) -> None:
        fake = FakeDocling()
        fake.fail_next = [da.Response(401, {"detail": "Invalid API key"})]
        with self.assertRaises(da.DoclingAdapterError) as raised:
            self.extract(self.make(fake))
        self.assertEqual(
            (raised.exception.reason, raised.exception.retryable), ("authentication_failed", False)
        )
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.sink.calls, 0)

    def test_error_body_never_carries_key_document_text_or_upstream_body(self) -> None:
        fake = FakeDocling()
        fake.fail_next = [da.Response(500, {"detail": "Invoice number SYNINV0042 traceback"})]
        with self.assertRaises(da.DoclingAdapterError) as raised:
            self.extract(self.make(fake))
        body = json.dumps(raised.exception.as_error("corr-1"))
        for secret in (API_KEY, "SYNINV0042", "traceback"):
            self.assertNotIn(secret, body)
        self.assertIn('"service": "docling"', body)
        self.assertNotIn(API_KEY, repr(self.make().config))

    # --- retry-safe persistence --------------------------------------------------

    def test_store_outage_then_retry_records_exactly_one_revision(self) -> None:
        sink = RecordingSink()
        sink.fail_times = 1
        fake = FakeDocling()
        with self.assertRaises(ConnectionError):
            self.extract(self.make(fake, sink=sink))
        self.assertEqual(len(self.jobs.tasks), 1)  # job kept for resume
        outcome = self.extract(self.make(fake, jobs=self.jobs, sink=sink))
        self.assertTrue(outcome.resumed)
        self.assertEqual(fake.submits, 1)
        self.assertEqual(len(sink.revisions), 1)
        # A duplicate delivery of the same job replays the same revision.
        self.jobs.put(da.job_key(TENANT, EVIDENCE, SHA, da.DEFAULT_EXTRACTOR_VERSION), "task-1")
        again = self.extract(self.make(fake, jobs=self.jobs, sink=sink))
        self.assertTrue(again.recorded["replayed"])
        self.assertEqual(len(sink.revisions), 1)

    # --- configuration -----------------------------------------------------------

    def test_configuration_is_validated_at_startup(self) -> None:
        bad = [
            {"base_url": "http://docling.example.org:5001"},
            {"base_url": "ftp://127.0.0.1"},
            {"api_key": None},
            {"api_key": "short"},
            {"max_attempts": 4},
            {"deadline_s": 301},
            {"poll_wait_s": 40, "timeout_s": 30},
        ]
        for change in bad:
            with self.subTest(change=change):
                args = {"base_url": "http://127.0.0.1:55001", "api_key": API_KEY, **change}
                with self.assertRaises(da.ConfigError):
                    da.AdapterConfig.build(**args)
        self.assertEqual(
            da.AdapterConfig.build(base_url="https://docling.internal/", api_key=API_KEY).base_url,
            "https://docling.internal",
        )


if __name__ == "__main__":
    unittest.main()
