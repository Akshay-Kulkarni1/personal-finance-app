"""Create/preserve a development .env with a durable local encryption key."""
from pathlib import Path
import os
import re

from cryptography.fernet import Fernet

root = Path(__file__).resolve().parent.parent
example = root / "backend" / ".env.example"
target = root / "backend" / ".env"
if not target.exists():
    target.write_text(example.read_text())
    os.chmod(target, 0o600)
    print("Created backend/.env from the development example")
text = target.read_text()
if re.search(r"^AHEAD_ENV=production\s*$", text, re.MULTILINE):
    raise RuntimeError("Refusing to alter a production environment file")
match = re.search(r"^AHEAD_ENCRYPTION_KEY=(.*)$", text, re.MULTILINE)
if match and match.group(1).strip():
    Fernet(match.group(1).strip().encode())
    print("Preserved existing Ahead encryption key")
else:
    key = Fernet.generate_key().decode()
    if match:
        text = text[:match.start(1)] + key + text[match.end(1):]
    else:
        text += "\nAHEAD_ENCRYPTION_KEY=" + key + "\n"
    target.write_text(text)
    print("Generated local-only encryption key (saved privately in backend/.env)")
os.chmod(target, 0o600)
print("Local sign-in works without Plaid. Plaid Link requires your Sandbox keys.")
