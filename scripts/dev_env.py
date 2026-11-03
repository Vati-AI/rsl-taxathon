"""Create local secrets without overwriting an existing environment."""
from pathlib import Path
import os
import secrets
root = Path(__file__).resolve().parents[1]
p = root / '.env'
if p.exists():
    print('.env already exists; unchanged')
else:
    content = (root / '.env.example').read_text().replace('POSTGRES_PASSWORD=\n', 'POSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(content)
    print('Created ignored .env with a generated local database password')
