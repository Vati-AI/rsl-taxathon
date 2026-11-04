# docling

Document extraction API. Owner: Keletso; LangGraph implementation is owned by Gupta.

- Upstream: https://github.com/docling-project/docling-serve (MIT)
- Official documentation: https://github.com/docling-project/docling-serve/blob/main/docs/usage.md
- Configuration: `DOCLING_BASE_URL`
- Status: authenticated, bounded development service running offline with local model artifacts and smoke-tested (TAX-028). Vati extraction adapter in `extraction_adapter.py` (TAX-038), tested against a contract-faithful fake; **not yet run against the live service** (see below).

## Run it (TAX-028)

```sh
python3 scripts/docling_dev.py up      # ensures DOCLING_API_KEY in ignored .env, starts, waits for /health
python3 scripts/docling_dev.py smoke   # synthetic scanned invoice: auth, OCR, table, page provenance, size limit
python3 scripts/docling_dev.py status
python3 scripts/docling_dev.py stop
```

- API: http://127.0.0.1:55001 (loopback only). Every API request needs header `X-Api-Key: $DOCLING_API_KEY` from the ignored root `.env`; `/health` is open.
- Configuration lives in the root `docker-compose.docling.yml` (project `rsl-taxathon-docling`). CI validates that file.
- Image: `ghcr.io/docling-project/docling-serve-cpu:v1.38.2@sha256:7a480a1604fa3b42efeddc7db9527690d9f9bc903226136013e2bfe6b1a4dfb0`. Version 1.38.2 equals the locked source `9c058dbb58f61021e3a16e37edb8a7527b2cb2d4` (2026-10-27). The CPU image is a 2.1 GB download versus 5.4 GB for the CUDA image; developer laptops have no GPU.

## Safety settings

| Setting | Value | Why |
|---|---|---|
| `DOCLING_SERVE_API_KEY` | generated | No unauthenticated conversion |
| `DOCLING_SERVE_MAX_FILE_SIZE` | 26214400 (25 MiB) | Same bound as `RegisterDocumentRequest.size_bytes` in the Vati contract |
| `DOCLING_SERVE_MAX_NUM_PAGES` | 50 | Bounded work per document |
| `DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT` | 300 s | Finite deadline (API_STANDARD) |
| `DOCLING_SERVE_ENABLE_REMOTE_SERVICES`, `ALLOW_EXTERNAL_PLUGINS`, `ALLOW_CUSTOM_*_CONFIG` | false | Documents never leave the container; no user-supplied model configs |
| `HF_HUB_OFFLINE` | 1 | Startup fails instead of downloading if a model were missing |
| `DOCLING_SERVE_ENABLE_UI` | false | API only |
| `DOCLING_SERVE_ENG_LOC_NUM_WORKERS`, `mem_limit` | 1, 4 GB | Shared laptop |

Model artifacts are the image's own default set (871 MB at `/opt/app-root/src/.cache/docling/models`: layout heron, docling-models/TableFormer, figure classifier, EasyOCR, RapidOCR). Every conversion logged "artifacts_path is set to a valid directory. No model weights will be downloaded at runtime."

## Recorded smoke result (2026-11-03)

The smoke test renders a **two-page image-only (scanned) synthetic invoice** inside the container with Pillow, so OCR is mandatory: page 1 has the header text with invoice number `SYNINV0042`, page 2 has a ruled 4×3 table. It converts the invoice with `POST /v1/convert/file` (`to_formats=json`, `do_ocr`, `force_ocr`, `do_table_structure`).

| Check | Observed |
|---|---|
| No `X-Api-Key` / wrong key | 401 / 401 |
| Conversion | HTTP 200, `status: success`, about 10–12 s on CPU, 2 pages |
| OCR and page provenance | Invoice number found in text items whose `prov.page_no` is **1** (where it was drawn) |
| Table and page provenance | One table, `prov.page_no` **2**, bounding boxes present, 4 rows × 3 columns. Cells: Item, Qty, Amount LSL / Consulting services, 1, 3000.00 / Freight, 2, 450.00 / Total, 3450.00 (exact) |
| Size bound | 25 MiB + 1 byte file: **HTTP 200** with `status: "failure"`, error category `policy` ("exceeds the max_file_size limit"), no content returned |
| Repeatability | Two runs, same results (input SHA-256 differs per run only because the PDF embeds a creation timestamp) |
| Memory | 1.47 GiB of 4 GiB after conversions |

## Notes for the adapter (TAX-038)

- **Check the body `status`, not just the HTTP code.** Docling reports policy rejections (size, pages) as HTTP 200 with `status: "failure"` and `errors[].category: "policy"`. Treat anything other than `success` as failed or partial extraction, never as an empty document.
- Use only `/v1/convert/file` (multipart). Do not expose `/v1/convert/source`: it fetches URLs, and Vati must not fetch arbitrary URLs.
- Keep the original file's SHA-256 on the Vati side (Docling does not return it). Map Docling `prov[].page_no` plus `bbox` to the contract's `EvidenceLocator.page`.
- Long documents should use Docling's async endpoints (`/v1/convert/file/async`, then poll), within the 300 s document deadline.

## Extraction adapter (TAX-038)

`integration/docling/extraction_adapter.py` converts one stored evidence document and records an append-only extraction revision through `vati_tax.evidence.store.record_extraction` (ADR-011), running as the service identity.

- Routes from the locked source (`external/docling`, `docling_serve/app.py`, 9c058db): `POST /v1/convert/file/async` (multipart, sent once per attempt; not idempotent), `GET /v1/status/poll/{task_id}?wait=N` (404 = task lost), `GET /v1/result/{task_id}` (404 until done; `{"failure": ...}` for a failed task). `/v1/convert/source` is never used.
- Input checks before any request: canonical tenant/evidence UUIDs, one of the five contract media types, 1 byte to 25 MiB, and the bytes' SHA-256 equal to the evidence hash.
- Status mapping (body `status`, not HTTP code): `success` -> `success`; `partial_success` -> `partial` (`partial_extraction`); `failure` -> `failure` (`docling_policy_rejected` for the `policy` category, else `docling_failure`); `skipped` -> `failure` (`docling_skipped`); task failure / empty content -> `failure`. A failure carries an error code and no items.
- Items: each located text element (`label`, `page`, `bbox`, `element_ref` such as `#/texts/1`) and each table cell (`label: table_cell`, `record: tables/{t}/r{row}/c{col}`, cell bbox or the table's). Elements without a page are dropped (not citable); invalid bboxes are dropped while keeping the page. Output beyond the store limits (5,000 items, 2,000,000 characters) is recorded as `partial` with `extraction_truncated`.
- Retry safety: a `JobStore` keyed by tenant, evidence, SHA-256, extractor version and options keeps the Docling task so a restarted worker resumes polling instead of converting again; a lost task clears it so the next attempt resubmits. The revision is recorded with a deterministic idempotency key, so a repeated recording replays the same revision. `InMemoryJobStore` is for development; a deployment needs a durable store (TAX-033 outbox).
- Limits follow docs/API_STANDARD.md: 30 s request timeout, at most 3 attempts with backoff for safe GETs (honouring `Retry-After`), 300 s document deadline (a deadline keeps the job for resumption). 401/403 are not retried. Errors use the contract `ErrorResponse` with `service: docling` and never include the key, document text or Docling bodies.
- Configuration: `AdapterConfig.build(base_url=DOCLING_BASE_URL, api_key=DOCLING_API_KEY)` refuses plain HTTP off loopback, a missing key and out-of-range limits at startup.

**Live verification still needed.** `tests/integration/test_docling_extraction.py` runs against a fake that follows the locked routes and the recorded TAX-028 smoke output. The adapter has not been run against the live container (the implementing machine had Docker stopped and under 5 GB free disk). With the service up (`python3 scripts/docling_dev.py up`), convert the TAX-028 synthetic invoice through `DoclingExtractionAdapter.extract` with `HttpTransport` and confirm OCR text on page 1 and the 4x3 table cells on page 2 before marking the integration connected.
