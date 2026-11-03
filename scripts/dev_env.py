"""Add missing local secrets without overwriting existing values."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
p = root / ".env"
content = p.read_text() if p.exists() else (root / ".env.example").read_text()
lines = content.splitlines()
for key in ["POSTGRES_PASSWORD", "FRAPPE_DB_PASSWORD", "DOCLING_API_KEY"]:
    matches = [i for i, line in enumerate(lines) if line.startswith(key + "=")]
    if matches:
        if not lines[matches[-1]].split("=", 1)[1].strip():
            lines[matches[-1]] = key + "=" + secrets.token_urlsafe(32)
    else:
        lines.append(key + "=" + secrets.token_urlsafe(32))
p.write_text("\n".join(lines) + "\n")
os.chmod(p, 0o600)
print("Local .env prepared; existing non-empty secrets preserved")
