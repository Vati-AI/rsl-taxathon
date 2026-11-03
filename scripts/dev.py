"""Dependency-free, explicit fixture environment and workspace checks."""

import argparse
import json
import re
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Built Vue auditor app (`npm run build:auditor`). Optional: without a build the
# starter page is served so the fixture environment stays dependency-free.
AUDITOR_DIST = ROOT / "apps/auditor-web/dist"
STARTER_ROUTES = {
    "/auditor/": "apps/auditor-web/starter.html",
    "/taxpayer/": "apps/taxpayer-web/starter.html",
    "/starter.css": "apps/shared/starter.css",
    "/starter.js": "apps/shared/starter.js",
}
ASSET_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


class FixtureServer(ThreadingHTTPServer):
    # socketserver's default listen backlog is 5. A built app loads many module
    # chunks in parallel, and refused connections surfaced as blank pages.
    request_queue_size = 64
    daemon_threads = True


def static_target(path):
    """Map an allowed URL path to a file; anything else is None (404)."""
    built_index = AUDITOR_DIST / "index.html"
    if path.startswith("/auditor/") and built_index.is_file():
        if path.startswith("/auditor/assets/"):
            name = path.removeprefix("/auditor/assets/")
            assets = AUDITOR_DIST / "assets"
            candidate = assets / name
            # Single flat file name only: no separators, dot-files or traversal.
            if ASSET_NAME.fullmatch(name) and candidate.suffix in CONTENT_TYPES:
                if candidate.is_file() and candidate.resolve().parent == assets.resolve():
                    return candidate
            return None
        # Client-side routes such as /auditor/cases/SYN-001 load the app shell.
        return built_index
    target = STARTER_ROUTES.get(path)
    return ROOT / target if target else None


def scenarios():
    return json.loads((ROOT / "data/scenarios/cases.json").read_text())


def payload(path):
    if path == "/api/health":
        return 200, {"status": "ok", "mode": "fixture", "synthetic": True}
    if path in ["/api/scenarios", "/api/cases"]:
        return 200, {"mode": "fixture", "synthetic": True, "items": scenarios()}
    if path.startswith("/api/cases/"):
        item = next((x for x in scenarios() if x["id"] == path.rsplit("/", 1)[-1]), None)
        return (
            (200, {"mode": "fixture", "synthetic": True, "item": item})
            if item
            else (404, {"error": "not_found", "mode": "fixture", "synthetic": True})
        )
    return 404, {"error": "not_found", "mode": "fixture", "synthetic": True}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            code, data = payload(path)
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/", "/auditor"):
            self.send_response(302)
            self.send_header("Location", "/auditor/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # Explicitly allow only UI files; never expose .env or repo source.
        target = static_target(path)
        if not target:
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[target.suffix])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


def check():
    for command in [
        ["python3", "scripts/validate.py"],
        [
            "python3",
            "-m",
            "compileall",
            "-q",
            "scripts",
            "backend/vati_tax/vati_tax",
            "ai",
            "integration/api",
        ],
        ["python3", "-m", "unittest", "discover", "-s", "tests/tooling", "-v"],
        ["python3", "integration/api/contract.py"],
        ["python3", "-m", "unittest", "discover", "-s", "tests/contracts", "-v"],
        # Frappe-free app logic (portal boundary/fixtures); bench tests cover the rest.
        ["python3", "-m", "unittest", "discover", "-s", "backend/vati_tax", "-p", "test_*.py"],
    ]:
        subprocess.check_call(command, cwd=ROOT)
    print("Workspace checks passed")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["check", "serve"])
    p.add_argument("--port", type=int, default=58080)
    a = p.parse_args()
    if a.command == "check":
        check()
        return
    server = FixtureServer(("127.0.0.1", a.port), Handler)
    print(
        f"FIXTURE MODE — synthetic data only: http://127.0.0.1:{a.port}/auditor/ and /taxpayer/",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
