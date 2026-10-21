"""Encrypted, short-lived analysis staging files; only opaque IDs enter RQ."""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
TEMP_BLOB_TTL_SECONDS = 3600
TEMP_BLOB_DIR = Path(os.getenv("TEMP_BLOB_DIR", "/tmp/nightwise-analysis"))


def _key(version: str | None = None) -> tuple[str, bytes]:
    current = os.getenv("TEMP_BLOB_KEY_VERSION", os.getenv("MASTER_KEY_VERSION", "v1"))
    wanted = version or current
    raw = (os.getenv("TEMP_BLOB_KEY", "") if wanted == current else
           os.getenv(f"TEMP_BLOB_KEY_{wanted.upper()}", ""))
    if not raw:
        raw = (os.getenv("MASTER_KEY", "") if wanted == os.getenv("MASTER_KEY_VERSION", "v1") else
               os.getenv(f"MASTER_KEY_{wanted.upper()}", ""))
    if not raw:
        raise RuntimeError("TEMP_BLOB_KEY or MASTER_KEY is required for staging uploads.")
    key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(key) != 32:
        raise RuntimeError("Temporary blob key must decode to exactly 32 bytes.")
    return wanted, key


def write_blob(payload: bytes, *, max_bytes: int = MAX_UPLOAD_BYTES,
               ttl_seconds: int = TEMP_BLOB_TTL_SECONDS) -> str:
    if len(payload) > max_bytes:
        raise ValueError("Combined upload size must be at most 100 MB.")
    TEMP_BLOB_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    blob_id = uuid.uuid4().hex
    nonce = os.urandom(12)
    key_version, key = _key()
    body = AESGCM(key).encrypt(nonce, payload, blob_id.encode())
    path = TEMP_BLOB_DIR / blob_id
    tmp = TEMP_BLOB_DIR / f".{blob_id}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        created = time.time()
        handle.write(json.dumps({"created": created, "expires_at": created + ttl_seconds,
                                 "nonce": base64.b64encode(nonce).decode(),
                                 "key_version": key_version}).encode() + b"\n")
        handle.write(body)
    os.replace(tmp, path)
    return blob_id


def read_blob(blob_id: str) -> bytes:
    if not blob_id.isalnum() or len(blob_id) != 32:
        raise ValueError("Invalid temporary blob ID.")
    path = TEMP_BLOB_DIR / blob_id
    with path.open("rb") as handle:
        metadata = json.loads(handle.readline())
        expires_at = float(metadata.get("expires_at", float(metadata["created"]) + TEMP_BLOB_TTL_SECONDS))
        if time.time() > expires_at:
            path.unlink(missing_ok=True)
            raise FileNotFoundError("Temporary upload expired.")
        body = handle.read()
    nonce = base64.b64decode(metadata["nonce"])
    _, key = _key(metadata.get("key_version"))
    return AESGCM(key).decrypt(nonce, body, blob_id.encode())


def delete_blob(blob_id: str) -> None:
    if blob_id.isalnum() and len(blob_id) == 32:
        (TEMP_BLOB_DIR / blob_id).unlink(missing_ok=True)


def cleanup_expired_blobs() -> int:
    if not TEMP_BLOB_DIR.exists():
        return 0
    removed = 0
    now = time.time()
    cutoff = now - TEMP_BLOB_TTL_SECONDS
    for path in TEMP_BLOB_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith("."):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
            continue
        try:
            with path.open("rb") as handle:
                metadata = json.loads(handle.readline())
            expiry = float(metadata.get("expires_at", float(metadata["created"]) + TEMP_BLOB_TTL_SECONDS))
            if expiry < now:
                path.unlink(missing_ok=True)
                removed += 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            removed += 1
    return removed
