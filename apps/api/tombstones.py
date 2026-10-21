"""Pseudonymous delete markers kept separately from encrypted DB backups."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

TOMBSTONE_DIR = Path(os.getenv("TOMBSTONE_DIR", "/var/lib/nightwise/tombstones"))
TOMBSTONE_FILE = "deleted-accounts.jsonl"


def cleanup_expired_tombstones() -> int:
    path = TOMBSTONE_DIR / TOMBSTONE_FILE
    if not path.exists():
        return 0
    now = int(time.time())
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
               if line and int(json.loads(line).get("expires_at", 0)) > now]
    old_lines = len(path.read_text(encoding="utf-8").splitlines())
    if len(entries) == old_lines:
        return 0
    _write_entries(path, entries)
    return old_lines - len(entries)


def _write_entries(path: Path, entries: list[dict]) -> None:
    temporary = TOMBSTONE_DIR / f".{TOMBSTONE_FILE}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(TOMBSTONE_DIR, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def record_deleted_account(user_id: uuid.UUID) -> None:
    TOMBSTONE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = TOMBSTONE_DIR / TOMBSTONE_FILE
    now = int(time.time())
    entries = []
    if path.exists():
        try:
            entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                       if line and int(json.loads(line).get("expires_at", 0)) > now]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Refuse to destroy an unreadable ledger; deletion is retried after repair.
            raise RuntimeError("Deletion ledger cannot be read safely.") from None
    account_hash = hashlib.sha256(user_id.bytes).hexdigest()
    entries = [entry for entry in entries if entry.get("account_ref_hash") != account_hash]
    entries.append({"account_ref_hash": account_hash, "deleted_at": now,
                    "expires_at": now + 30 * 86400})
    _write_entries(path, entries)
