"""Authenticated, bounded Docling Serve for local synthetic development (TAX-028).

The smoke test renders a two-page *scanned* (image-only) synthetic invoice inside the Docling
container, converts it, and checks OCR text, table extraction and page provenance. It never
prints the API key, never deletes volumes and sends no document outside this machine.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.docling.yml")]
BASE = "http://127.0.0.1:55001"
MAX_FILE_SIZE = 26214400
PAGE1_MARKER = "SYNINV0042"
TABLE_MARKER = "Consulting"

# Runs inside the container (Pillow is part of the Docling image); prints a base64 PDF.
RENDER = r"""
import base64, io
from PIL import Image, ImageDraw, ImageFont
font = ImageFont.load_default(size=34)
small = ImageFont.load_default(size=28)
def page():
    img = Image.new("RGB", (1240, 1754), "white")
    return img, ImageDraw.Draw(img)
p1, d1 = page()
lines = ["SYNTHETIC SCANNED INVOICE - NOT REAL TAXPAYER DATA", "Invoice number SYNINV0042",
         "Supplier: Synthetic Highland Imports Ltd", "Customer: Synthetic Border Retail (Pty) Ltd",
         "Date: 2026-09-30   Currency: LSL"]
for i, text in enumerate(lines):
    d1.text((100, 150 + i * 70), text, fill="black", font=font)
p2, d2 = page()
d2.text((100, 120), "Invoice lines (continued)", fill="black", font=font)
rows = [["Item", "Qty", "Amount LSL"], ["Consulting services", "1", "3000.00"],
        ["Freight", "2", "450.00"], ["Total", "", "3450.00"]]
x, y, widths, h = 100, 220, [560, 160, 300], 80
for r, row in enumerate(rows):
    cx = x
    for c, cell in enumerate(row):
        d2.rectangle([cx, y + r * h, cx + widths[c], y + (r + 1) * h], outline="black", width=3)
        d2.text((cx + 15, y + r * h + 22), cell, fill="black", font=small)
        cx += widths[c]
buf = io.BytesIO()
p1.save(buf, "PDF", resolution=150.0, save_all=True, append_images=[p2])
print(base64.b64encode(buf.getvalue()).decode())
"""


def api_key() -> str:
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("DOCLING_API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("DOCLING_API_KEY missing; run python3 scripts/dev_env.py")


def run(args: list[str], capture: bool = False) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=capture)
    if result.returncode:
        raise SystemExit(f"Command failed ({result.returncode}); data preserved")
    return result.stdout if capture else ""


def request(
    method: str, path: str, key: str | None, body: bytes | None = None, ctype: str = ""
) -> tuple[int, Any]:
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    if key is not None:
        req.add_header("X-Api-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=330) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, None


def multipart(filename: str, content: bytes) -> tuple[bytes, str]:
    """Build a /v1/convert/file request: JSON output with OCR forced (scanned input)."""
    boundary = uuid.uuid4().hex
    fields = [("to_formats", "json"), ("do_ocr", "true"), ("force_ocr", "true")]
    fields += [("do_table_structure", "true"), ("include_images", "false")]
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields
    ]
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
            f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'
        ).encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def wait_ready(deadline_s: int) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            if request("GET", "/health", None)[0] == 200:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(5)
    raise SystemExit(f"Docling not healthy within {deadline_s}s; inspect: docker compose logs")


def texts_by_page(doc: dict[str, Any]) -> dict[int, str]:
    pages: dict[int, str] = {}
    for item in doc.get("texts", []):
        for prov in item.get("prov", []):
            pages[prov["page_no"]] = pages.get(prov["page_no"], "") + " " + item.get("text", "")
    return pages


def smoke() -> dict[str, Any]:
    wait_ready(300)
    key = api_key()
    pdf = base64.b64decode(run([*COMPOSE, "exec", "-T", "docling", "python", "-c", RENDER], True))
    body, ctype = multipart("synthetic-scanned-invoice.pdf", pdf)
    result: dict[str, Any] = {
        "input_sha256": hashlib.sha256(pdf).hexdigest(),
        "input_bytes": len(pdf),
        "no_key_status": request("POST", "/v1/convert/file", None, body, ctype)[0],
        "wrong_key_status": request(
            "POST", "/v1/convert/file", "wrong-" + secrets.token_hex(8), body, ctype
        )[0],
    }
    started = time.monotonic()
    status, data = request("POST", "/v1/convert/file", key, body, ctype)
    result["convert_status"] = status
    result["convert_seconds"] = round(time.monotonic() - started, 1)
    document = (data or {}).get("document", {}) if isinstance(data, dict) else {}
    doc = document.get("json_content") or {}
    result["docling_status"] = data.get("status") if isinstance(data, dict) else None
    result["num_pages"] = len(doc.get("pages", {}))
    pages = texts_by_page(doc)
    flat = {p: t.replace(" ", "").replace("-", "") for p, t in pages.items()}
    result["page1_marker_pages"] = sorted(p for p, t in flat.items() if PAGE1_MARKER in t)
    tables = doc.get("tables", [])
    result["tables"] = [
        {
            "pages": sorted({p["page_no"] for p in t.get("prov", [])}),
            "has_bbox": all("bbox" in p for p in t.get("prov", [])),
            "rows": t.get("data", {}).get("num_rows"),
            "cols": t.get("data", {}).get("num_cols"),
            "cells": [c.get("text") for c in t.get("data", {}).get("table_cells", [])][:12],
        }
        for t in tables
    ]
    result["table_marker_found"] = any(
        TABLE_MARKER in " ".join(filter(None, t["cells"])) for t in result["tables"]
    )
    oversized, octype = multipart("oversized.pdf", b"%PDF-1.4\n" + b"0" * (MAX_FILE_SIZE + 1))
    # Docling reports policy rejections as HTTP 200 with status "failure"; check the body.
    status, rejected = request("POST", "/v1/convert/file", key, oversized, octype)
    rejected = rejected if isinstance(rejected, dict) else {}
    result["oversized_http_status"] = status
    result["oversized_docling_status"] = rejected.get("status")
    result["oversized_error_categories"] = sorted(
        {e.get("category") for e in rejected.get("errors", [])}
    )
    result["oversized_content_returned"] = bool(
        (rejected.get("document") or {}).get("json_content")
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["up", "smoke", "status", "stop"])
    args = parser.parse_args()
    if args.command == "up":
        run(["python3", "scripts/dev_env.py"])
        run([*COMPOSE, "up", "-d"])
        wait_ready(600)
        print(f"Docling running at {BASE} (loopback only, X-Api-Key required)")
    elif args.command == "smoke":
        print(json.dumps(smoke(), indent=2))
    elif args.command == "status":
        run([*COMPOSE, "ps"])
    elif args.command == "stop":
        run([*COMPOSE, "stop"])


if __name__ == "__main__":
    main()
