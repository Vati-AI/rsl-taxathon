"""Docling extraction adapter (TAX-038).

Converts one stored evidence document with the pinned Docling Serve (TAX-028) and
records the result as an append-only extraction revision through the TAX-032
evidence store (ADR-011). Routes and response shapes come from the locked source
(``external/docling`` at 9c058db, docling-serve 1.38.2: ``docling_serve/app.py``)
and its ``docs/usage.md``:

- ``POST /v1/convert/file/async`` (multipart ``files``) returns a task with
  ``task_status`` ``pending|started|success|failure``. Submitting is not idempotent,
  so it is sent once per attempt and never blindly retried.
- ``GET /v1/status/poll/{task_id}?wait=N`` reports progress; 404 means the task is
  unknown (for example after a Docling restart).
- ``GET /v1/result/{task_id}`` returns the conversion response (body ``status``
  ``success|partial_success|skipped|failure``) or ``{"failure": ...}``; 404 until done.

Every request carries ``X-Api-Key``. ``/v1/convert/source`` is never used: Vati does
not let Docling fetch URLs. The adapter checks the body ``status`` rather than the
HTTP code (Docling reports policy rejections as HTTP 200 ``failure``), keeps the
original SHA-256 on the Vati side, and maps ``prov[].page_no`` and ``bbox`` to the
contract ``EvidenceLocator``. A failure is recorded as a failed revision with an
error code and no items; it is never an empty document.

Retry safety: a ``JobStore`` remembers the Docling task for each (tenant, evidence,
SHA-256, options) so a restarted worker resumes polling instead of converting again,
and the extraction is recorded with a deterministic idempotency key so a repeated
recording replays the same revision. Document text is untrusted data: it is never
logged and never interpreted here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

LOG = logging.getLogger("vati.integration.docling")

EXTRACTOR = "docling"
DEFAULT_EXTRACTOR_VERSION = "docling-serve-1.38.2"
MAX_FILE_SIZE = 26_214_400  # same bound as RegisterDocumentRequest.size_bytes and TAX-028
MAX_DEADLINE_S = 300.0  # DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT (TAX-028)
MAX_RETRY_AFTER_S = 30.0
MAX_ITEMS = 5_000  # vati_tax.evidence.rules limits (ADR-011)
MAX_ITEM_TEXT = 20_000
MAX_TOTAL_TEXT = 2_000_000
MEDIA_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
# Conversion options sent with every request; part of the job identity.
CONVERT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("to_formats", "json"),
    ("do_ocr", "true"),
    ("do_table_structure", "true"),
    ("include_images", "false"),
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LABEL = re.compile(r"[a-z][a-z0-9_]{0,31}")
_CODE = re.compile(r"[a-z][a-z0-9_.]{0,63}")
_TASK_ID = re.compile(r"[A-Za-z0-9._\-]{1,128}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ConfigError(ValueError):
    """Adapter configuration is missing or malformed (fails at startup, not mid-request)."""


class DoclingAdapterError(Exception):
    """Structured failure; ``code`` is an ``ErrorCode`` from integration/api/openapi.json."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        retryable: bool = False,
        reason: str | None = None,
        field: str | None = None,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.reason = reason
        self.field = field
        self.upstream_status = upstream_status

    def as_error(self, correlation_id: str) -> dict[str, dict[str, Any]]:
        """``ErrorResponse`` body; never includes Docling bodies, document text or the key."""
        error: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "correlation_id": correlation_id,
            "service": "docling",
        }
        if self.field:
            error["field_errors"] = [{"field": self.field, "code": self.reason or self.code}]
        return {"error": error}


def _invalid(message: str, field: str, reason: str) -> DoclingAdapterError:
    return DoclingAdapterError(
        "invalid_domain_input", message, http_status=422, reason=reason, field=field
    )


def _unavailable(
    message: str, reason: str, *, retryable: bool, upstream_status: int | None = None
) -> DoclingAdapterError:
    return DoclingAdapterError(
        "upstream_unavailable",
        message,
        http_status=503,
        retryable=retryable,
        reason=reason,
        upstream_status=upstream_status,
    )


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class AdapterConfig:
    base_url: str
    api_key: str = field(repr=False)
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION
    timeout_s: float = 30.0
    max_attempts: int = 3
    poll_wait_s: float = 5.0
    deadline_s: float = MAX_DEADLINE_S

    @classmethod
    def build(
        cls,
        *,
        base_url: str,
        api_key: str | None,
        extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
        timeout_s: float = 30.0,
        max_attempts: int = 3,
        poll_wait_s: float = 5.0,
        deadline_s: float = MAX_DEADLINE_S,
    ) -> AdapterConfig:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigError("DOCLING_BASE_URL must be an http(s) URL")
        # host.docker.internal: the developer's own machine as seen from the Frappe
        # developer container (TAX-085); plain HTTP never leaves that machine.
        local = ("127.0.0.1", "localhost", "::1", "host.docker.internal")
        if parsed.scheme == "http" and parsed.hostname not in local:
            raise ConfigError("plain HTTP to Docling is only allowed on loopback")
        if not api_key or len(api_key) < 16:
            raise ConfigError("DOCLING_API_KEY is missing")
        if not _VERSION.fullmatch(extractor_version):
            raise ConfigError("extractor_version must be a short version label")
        if not 1 <= max_attempts <= 3:
            raise ConfigError("max_attempts must be 1-3 (docs/API_STANDARD.md)")
        if not 0 < timeout_s <= 60 or not 0 <= poll_wait_s < timeout_s:
            raise ConfigError("timeout_s must be 0-60 s and poll_wait_s below it")
        if not 0 < deadline_s <= MAX_DEADLINE_S:
            raise ConfigError(
                f"deadline_s must be within the {MAX_DEADLINE_S:.0f} s document limit"
            )
        return cls(
            base_url.rstrip("/"),
            api_key,
            extractor_version,
            timeout_s,
            max_attempts,
            poll_wait_s,
            deadline_s,
        )


# --------------------------------------------------------------------------- transport


@dataclass(frozen=True)
class Response:
    status: int
    body: Any
    retry_after: float | None = None


class TransportError(Exception):
    def __init__(self, timed_out: bool) -> None:
        super().__init__("Docling request timed out" if timed_out else "Docling is unreachable")
        self.timed_out = timed_out


# (method, path, body bytes or None, content type or None) -> Response
Transport = Callable[[str, str, bytes | None, str | None], Response]


class HttpTransport:
    """urllib client sending ``X-Api-Key``; never logs the key or response bodies."""

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 30.0) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout_s = timeout_s

    def __call__(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> Response:
        headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self._base_url + path, data=body, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw, status, resp_headers = response.read(), response.status, response.headers
        except urllib.error.HTTPError as error:
            raw, status, resp_headers = error.read(), error.code, error.headers
        except (TimeoutError, socket.timeout):
            raise TransportError(timed_out=True) from None
        except (urllib.error.URLError, ConnectionError) as error:
            reason = getattr(error, "reason", None)
            raise TransportError(
                timed_out=isinstance(reason, (TimeoutError, socket.timeout))
            ) from None
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        retry_after = resp_headers.get("Retry-After") if resp_headers else None
        return Response(status, parsed, _retry_after(retry_after))


def _retry_after(value: str | None) -> float | None:
    try:
        return min(float(value), MAX_RETRY_AFTER_S) if value else None
    except ValueError:
        return None


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    return min(4.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)


def multipart_body(filename: str, media_type: str, content: bytes) -> tuple[bytes, str]:
    """Build the ``/v1/convert/file/async`` multipart body (options + one ``files`` part)."""
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in CONVERT_OPTIONS
    ]
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
            f'filename="{filename}"\r\nContent-Type: {media_type}\r\n\r\n'
        ).encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# --------------------------------------------------------------------------- persistence ports


class JobStore(Protocol):
    """Remembers the Docling task per job so a restarted worker resumes, not resubmits."""

    def get(self, job_key: str) -> str | None: ...

    def put(self, job_key: str, task_id: str) -> None: ...

    def clear(self, job_key: str) -> None: ...


class InMemoryJobStore:
    """Development/test job store. A deployment uses a durable table (TAX-033 outbox)."""

    def __init__(self) -> None:
        self.tasks: dict[str, str] = {}

    def get(self, job_key: str) -> str | None:
        return self.tasks.get(job_key)

    def put(self, job_key: str, task_id: str) -> None:
        self.tasks[job_key] = task_id

    def clear(self, job_key: str) -> None:
        self.tasks.pop(job_key, None)


class ExtractionSink(Protocol):
    """``vati_tax.evidence.store.record_extraction`` (service identity), or a test double."""

    def __call__(
        self,
        *,
        tenant_id: str,
        evidence_id: str,
        document_sha256: str,
        idempotency_key: str,
        result: dict[str, Any],
    ) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- mapping


def _bbox(raw: object) -> dict[str, Any] | None:
    """Keep a Docling bbox only if it satisfies the evidence-store rules; else drop it."""
    if not isinstance(raw, Mapping):
        return None
    try:
        values = [float(raw[k]) for k in ("l", "t", "r", "b")]
    except (KeyError, TypeError, ValueError):
        return None
    origin = raw.get("coord_origin", "BOTTOMLEFT")
    if origin not in ("TOPLEFT", "BOTTOMLEFT") or any(v < 0 or v != v for v in values):
        return None
    left, top, right, bottom = values
    if (
        left > right
        or (origin == "BOTTOMLEFT" and top < bottom)
        or (origin == "TOPLEFT" and top > bottom)
    ):
        return None
    return {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": origin}


def _page(prov: object, page_count: int) -> tuple[int | None, dict[str, Any] | None]:
    if not isinstance(prov, list) or not prov or not isinstance(prov[0], Mapping):
        return None, None
    page = prov[0].get("page_no")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= max(page_count, 1):
        return None, None
    return page, _bbox(prov[0].get("bbox"))


def _clean(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    text = _CONTROL.sub(" ", text).strip()
    return text[:MAX_ITEM_TEXT] if text else None


def _label(value: object, default: str) -> str:
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else default


def map_document(doc: Mapping[str, Any]) -> tuple[int, list[dict[str, Any]], bool]:
    """Map a DoclingDocument to evidence items; returns (page_count, items, truncated).

    Text items cite their page and bbox. Each table cell becomes its own item with a
    ``record`` locator (``tables/{t}/r{row}/c{col}``) so amounts can be cited exactly.
    """
    pages = doc.get("pages")
    page_count = len(pages) if isinstance(pages, Mapping) else 0
    items: list[dict[str, Any]] = []
    total = 0
    truncated = False

    def add(item: dict[str, Any]) -> bool:
        nonlocal total, truncated
        size = len(item.get("text") or "")
        if len(items) >= MAX_ITEMS or total + size > MAX_TOTAL_TEXT:
            truncated = True
            return False
        total += size
        items.append(item)
        return True

    texts = doc.get("texts")
    for index, raw in enumerate(texts if isinstance(texts, list) else []):
        if not isinstance(raw, Mapping):
            continue
        page, bbox = _page(raw.get("prov"), page_count)
        text = _clean(raw.get("text"))
        if page is None or text is None:
            continue  # no citable location or no content
        ref = raw.get("self_ref")
        item = {
            "element_ref": ref if isinstance(ref, str) and len(ref) <= 128 else f"#/texts/{index}",
            "label": _label(raw.get("label"), "text"),
            "page": page,
            "bbox": bbox,
            "text": text,
        }
        if not add(item):
            break

    tables = doc.get("tables")
    for t_index, table in enumerate(tables if isinstance(tables, list) else []):
        if truncated or not isinstance(table, Mapping):
            continue
        page, table_bbox = _page(table.get("prov"), page_count)
        if page is None:
            continue
        ref = table.get("self_ref")
        table_ref = ref if isinstance(ref, str) and len(ref) <= 100 else f"#/tables/{t_index}"
        data = table.get("data") if isinstance(table.get("data"), Mapping) else {}
        cells = data.get("table_cells") if isinstance(data, Mapping) else None
        for c_index, cell in enumerate(cells if isinstance(cells, list) else []):
            if not isinstance(cell, Mapping):
                continue
            text = _clean(cell.get("text"))
            if text is None:
                continue
            row = cell.get("start_row_offset_idx")
            col = cell.get("start_col_offset_idx")
            row = row if isinstance(row, int) and not isinstance(row, bool) else c_index
            col = col if isinstance(col, int) and not isinstance(col, bool) else 0
            item = {
                "element_ref": f"{table_ref}/cells/{c_index}",
                "label": "table_cell",
                "page": page,
                "bbox": _bbox(cell.get("bbox")) or table_bbox,
                "record": f"tables/{t_index}/r{row}/c{col}",
                "text": text,
            }
            if not add(item):
                break
    return page_count, items, truncated


def map_conversion(body: object, extractor_version: str) -> dict[str, Any]:
    """Map a ``/v1/result`` body to a ``record_extraction`` payload (ADR-011)."""
    base: dict[str, Any] = {"extractor": EXTRACTOR, "extractor_version": extractor_version}
    if not isinstance(body, Mapping):
        return {
            **base,
            "status": "failure",
            "error_code": "docling_invalid_response",
            "error_category": "upstream",
            "items": [],
        }
    if "failure" in body and "document" not in body:
        return {
            **base,
            "status": "failure",
            "error_code": "docling_task_failure",
            "error_category": "conversion",
            "items": [],
        }
    status = body.get("status")
    raw_errors = body.get("errors")
    categories: set[str] = set()
    for error in raw_errors if isinstance(raw_errors, list) else []:
        value = error.get("category") if isinstance(error, Mapping) else None
        if isinstance(value, str) and _LABEL.fullmatch(value):
            categories.add(value)
    # Prefer "policy" so a size/page rejection is reported as such.
    category = "policy" if "policy" in categories else min(categories, default=None)
    if status in ("failure", "skipped") or status not in ("success", "partial_success"):
        code = {
            "skipped": "docling_skipped",
            "failure": "docling_policy_rejected" if category == "policy" else "docling_failure",
        }.get(str(status), "docling_unknown_status")
        return {
            **base,
            "status": "failure",
            "error_code": code,
            "error_category": category or "conversion",
            "items": [],
        }
    document = body.get("document") if isinstance(body.get("document"), Mapping) else {}
    doc = document.get("json_content") if isinstance(document, Mapping) else None
    if not isinstance(doc, Mapping) or not doc:
        return {
            **base,
            "status": "failure",
            "error_code": "docling_missing_content",
            "error_category": "upstream",
            "items": [],
        }
    page_count, items, truncated = map_document(doc)
    if status == "partial_success" or truncated:
        code = "extraction_truncated" if truncated else "partial_extraction"
        return {
            **base,
            "status": "partial",
            "error_code": code,
            "error_category": category or ("limits" if truncated else "conversion"),
            "page_count": page_count,
            "items": items,
        }
    return {
        **base,
        "status": "success",
        "error_code": None,
        "page_count": page_count,
        "items": items,
    }


# --------------------------------------------------------------------------- adapter


@dataclass(frozen=True)
class ExtractionOutcome:
    status: str  # success | partial | failure
    task_id: str | None
    resumed: bool
    recorded: dict[str, Any]
    error_code: str | None


def job_key(tenant_id: str, evidence_id: str, document_sha256: str, extractor_version: str) -> str:
    options = json.dumps(CONVERT_OPTIONS, separators=(",", ":"))
    raw = "\x1f".join((tenant_id, evidence_id, document_sha256, extractor_version, options))
    return hashlib.sha256(raw.encode()).hexdigest()


class DoclingExtractionAdapter:
    def __init__(
        self,
        config: AdapterConfig,
        *,
        jobs: JobStore,
        sink: ExtractionSink,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._jobs = jobs
        self._sink = sink
        self._send = transport or HttpTransport(config.base_url, config.api_key, config.timeout_s)
        self._sleep = sleep
        self._clock = clock

    # -- public -------------------------------------------------------------

    def extract(
        self,
        *,
        tenant_id: str,
        evidence_id: str,
        document_sha256: str,
        media_type: str,
        content: bytes,
        correlation_id: str,
    ) -> ExtractionOutcome:
        """Convert the evidence bytes and record one extraction revision.

        Raises ``DoclingAdapterError`` for invalid input or an unavailable/slow Docling;
        a Docling-reported conversion failure is *recorded* as a failed revision.
        """
        for name, value in (("tenant_id", tenant_id), ("evidence_id", evidence_id)):
            if not isinstance(value, str) or not _UUID.fullmatch(value):
                raise _invalid(f"{name} must be a canonical UUID.", name, "invalid_uuid")
        if not isinstance(document_sha256, str) or not _SHA256.fullmatch(document_sha256):
            raise _invalid(
                "document_sha256 must be a SHA-256 hex digest.", "document_sha256", "invalid_sha256"
            )
        if media_type not in MEDIA_TYPES:
            raise _invalid(
                "This media type cannot be extracted.", "media_type", "unsupported_media_type"
            )
        if not 1 <= len(content) <= MAX_FILE_SIZE:
            raise _invalid(
                "Document size is outside 1 byte to 25 MiB.", "size_bytes", "size_out_of_range"
            )
        if hashlib.sha256(content).hexdigest() != document_sha256:
            raise _invalid(
                "The bytes do not match the evidence SHA-256.", "document_sha256", "hash_mismatch"
            )
        key = job_key(tenant_id, evidence_id, document_sha256, self.config.extractor_version)
        LOG.info(
            "docling.extract.start",
            extra={"correlation_id": correlation_id, "evidence_id": evidence_id},
        )
        task_id = self._jobs.get(key)
        resumed = task_id is not None
        if task_id is None:
            task_id = self._submit(evidence_id, media_type, content)
            self._jobs.put(key, task_id)
        body = self._await_result(key, task_id)
        result = map_conversion(body, self.config.extractor_version)
        recorded = self._sink(
            tenant_id=tenant_id,
            evidence_id=evidence_id,
            document_sha256=document_sha256,
            idempotency_key=f"docling-{key[:48]}",
            result=result,
        )
        self._jobs.clear(key)
        LOG.info(
            "docling.extract.recorded",
            extra={
                "correlation_id": correlation_id,
                "evidence_id": evidence_id,
                "status": result["status"],
                "items": len(result.get("items", [])),
            },
        )
        return ExtractionOutcome(
            result["status"], task_id, resumed, recorded, result.get("error_code")
        )

    # -- steps ----------------------------------------------------------------

    def _submit(self, evidence_id: str, media_type: str, content: bytes) -> str:
        body, ctype = multipart_body(f"{evidence_id}{MEDIA_TYPES[media_type]}", media_type, content)
        try:
            # Not idempotent: one send; a lost response leaves no job, so a retry resubmits.
            response = self._send("POST", "/v1/convert/file/async", body, ctype)
        except TransportError as error:
            raise _unavailable(
                "Docling did not accept the document.",
                "timeout" if error.timed_out else "unreachable",
                retryable=True,
            ) from None
        self._check_auth(response)
        if response.status in (429, 503) or response.status >= 500:
            raise _unavailable(
                "Docling is busy or unavailable.",
                "upstream_status",
                retryable=True,
                upstream_status=response.status,
            )
        task = response.body if isinstance(response.body, Mapping) else {}
        task_id = task.get("task_id")
        if (
            response.status != 200
            or not isinstance(task_id, str)
            or not _TASK_ID.fullmatch(task_id)
        ):
            raise _unavailable(
                "Docling returned an unexpected submission response.",
                "invalid_response",
                retryable=False,
                upstream_status=response.status,
            )
        return task_id

    def _await_result(self, key: str, task_id: str) -> object:
        deadline = self._clock() + self.config.deadline_s
        path = f"/v1/status/poll/{urllib.parse.quote(task_id, safe='')}"
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                # Keep the job so the next attempt resumes this task, not a new conversion.
                raise _unavailable(
                    "Docling did not finish within the document deadline.",
                    "deadline_exceeded",
                    retryable=True,
                )
            wait = min(self.config.poll_wait_s, max(remaining, 0.0))
            response = self._get(f"{path}?wait={wait:g}")
            if response.status == 404:
                self._jobs.clear(key)  # Docling lost the task: the next attempt resubmits
                raise _unavailable(
                    "Docling no longer knows this task.", "task_lost", retryable=True
                )
            status = (
                response.body.get("task_status") if isinstance(response.body, Mapping) else None
            )
            if response.status != 200 or status not in ("pending", "started", "success", "failure"):
                raise _unavailable(
                    "Docling returned an unexpected task status.",
                    "invalid_response",
                    retryable=False,
                    upstream_status=response.status,
                )
            if status in ("success", "failure"):
                break
            # Short pause even with server-side ``wait`` so an immediate reply cannot spin.
            self._sleep(min(1.0, max(deadline - self._clock(), 0.0)))
        result = self._get(f"/v1/result/{urllib.parse.quote(task_id, safe='')}")
        if result.status == 404:
            self._jobs.clear(key)
            raise _unavailable("Docling no longer has this result.", "task_lost", retryable=True)
        if result.status != 200:
            raise _unavailable(
                "Docling returned an unexpected result response.",
                "invalid_response",
                retryable=False,
                upstream_status=result.status,
            )
        return result.body

    def _get(self, path: str) -> Response:
        attempts = self.config.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                response = self._send("GET", path, None, None)
            except TransportError as error:
                if attempt == attempts:
                    raise _unavailable(
                        "Docling did not respond.",
                        "timeout" if error.timed_out else "unreachable",
                        retryable=True,
                    ) from None
                self._sleep(_backoff(attempt, None))
                continue
            self._check_auth(response)
            if response.status in (429, 502, 503, 504) and attempt < attempts:
                self._sleep(_backoff(attempt, response.retry_after))
                continue
            if response.status in (429, 502, 503, 504):
                raise _unavailable(
                    "Docling is busy or unavailable.",
                    "upstream_status",
                    retryable=True,
                    upstream_status=response.status,
                )
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _check_auth(response: Response) -> None:
        if response.status in (401, 403):
            raise _unavailable(
                "Docling rejected the adapter credentials.",
                "authentication_failed",
                retryable=False,
                upstream_status=response.status,
            )
