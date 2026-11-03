"""Dependency-free, explicit fixture environment and workspace checks."""

import argparse
import json
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


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
        # Explicitly allow only starter UI files; never expose .env or repo source.
        routes = {
            "/": "apps/auditor-web/index.html",
            "/auditor/": "apps/auditor-web/index.html",
            "/taxpayer/": "apps/taxpayer-web/index.html",
            "/starter.css": "apps/shared/starter.css",
            "/starter.js": "apps/shared/starter.js",
        }
        target = routes.get(path)
        if not target:
            self.send_error(404)
            return
        data = (ROOT / target).read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/html"
            if target.endswith(".html")
            else "text/css"
            if target.endswith(".css")
            else "application/javascript",
        )
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
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
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
