"""Replay privacy deletion markers against a quarantined restored database.

Run only after restoring a backup and before any API/worker serves traffic:
RESTORE_QUARANTINE=1 python -m apps.api.restore_tombstones
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from sqlalchemy import select

from .models import User, UserDataKey
from .storage import SessionLocal, master_key_for_version, master_keys
from .tombstones import TOMBSTONE_DIR, TOMBSTONE_FILE, cleanup_expired_tombstones


def replay() -> int:
    if os.getenv("RESTORE_QUARANTINE") != "1":
        raise RuntimeError("Refusing tombstone replay unless RESTORE_QUARANTINE=1.")
    master_keys()
    cleanup_expired_tombstones()
    ledger = TOMBSTONE_DIR / TOMBSTONE_FILE
    if not ledger.exists():
        return 0
    now = int(time.time())
    hashes = {entry["account_ref_hash"] for entry in
              (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line)
              if int(entry["expires_at"]) > now}
    deleted = 0
    with SessionLocal() as session:
        versions = set(session.scalars(select(UserDataKey.master_key_version).distinct()))
        for version in versions:
            master_key_for_version(version)
        users = list(session.scalars(select(User)))
        for user in users:
            if hashlib.sha256(user.id.bytes).hexdigest() in hashes:
                session.delete(user)
                deleted += 1
        session.commit()
    return deleted


if __name__ == "__main__":
    # Aggregate count only. No account identifier or payload is printed.
    print(f"Purged deleted accounts: {replay()}")
