"""Database sessions and per-user AES-GCM envelope encryption."""
from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, UserDataKey


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nightwise.db")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def _decode_master(raw: str) -> bytes:
    if not raw:
        raise RuntimeError("MASTER_KEY is required before storing encrypted data.")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception as exc:
        raise RuntimeError("MASTER_KEY must be URL-safe base64.") from exc
    if len(key) != 32:
        raise RuntimeError("MASTER_KEY must decode to exactly 32 bytes.")
    return key


def master_key_for_version(version: str | None = None) -> tuple[str, bytes]:
    """Read current or retained versioned key from environment/secret injection."""
    current_version = os.getenv("MASTER_KEY_VERSION", "v1")
    wanted = version or current_version
    raw = os.getenv("MASTER_KEY", "") if wanted == current_version else os.getenv(f"MASTER_KEY_{wanted.upper()}", "")
    if not raw:
        raise RuntimeError(f"Missing master key material for version {wanted}.")
    return wanted, _decode_master(raw)


def master_keys() -> tuple[str, bytes]:
    return master_key_for_version()


@contextmanager
def db_session():
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_user_dek(session: Session, user_id: uuid.UUID) -> None:
    version, master = master_keys()
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    wrapped = AESGCM(master).encrypt(nonce, dek, user_id.bytes)
    session.add(UserDataKey(user_id=user_id, wrapped_dek=wrapped, nonce=nonce,
                            master_key_version=version))


def user_dek(session: Session, user_id: uuid.UUID) -> bytes:
    row = session.scalar(select(UserDataKey).where(UserDataKey.user_id == user_id))
    if row is None:
        raise RuntimeError("User encryption key is missing.")
    _, master = master_key_for_version(row.master_key_version)
    return AESGCM(master).decrypt(row.nonce, row.wrapped_dek, user_id.bytes)


def encrypt_for_user(session: Session, user_id: uuid.UUID, plaintext: bytes) -> tuple[bytes, bytes, str]:
    dek = user_dek(session, user_id)
    nonce = os.urandom(12)
    return AESGCM(dek).encrypt(nonce, plaintext, user_id.bytes), nonce, master_keys()[0]


def decrypt_for_user(session: Session, user_id: uuid.UUID, ciphertext: bytes, nonce: bytes) -> bytes:
    return AESGCM(user_dek(session, user_id)).decrypt(nonce, ciphertext, user_id.bytes)


def init_db() -> None:
    """Development bootstrap only; deployed environments use Alembic migrations."""
    if os.getenv("AUTO_CREATE_SCHEMA") == "1":
        Base.metadata.create_all(engine)


def rotate_wrapped_user_keys(batch_size: int = 100) -> int:
    """Resumably rewrap per-user DEKs under the current master key version."""
    from .models import Alert, Night, PushSubscription, Result, UserDataKey
    from sqlalchemy import select

    target_version, target_key = master_keys()
    rotated = 0
    with SessionLocal() as session:
        rows = list(session.scalars(select(UserDataKey).where(
            UserDataKey.master_key_version != target_version).limit(batch_size)))
        for row in rows:
            _, old_key = master_key_for_version(row.master_key_version)
            dek = AESGCM(old_key).decrypt(row.nonce, row.wrapped_dek, row.user_id.bytes)
            nonce = os.urandom(12)
            row.wrapped_dek = AESGCM(target_key).encrypt(nonce, dek, row.user_id.bytes)
            row.nonce = nonce
            row.master_key_version = target_version
            row.rotated_at = datetime.now(timezone.utc)
            for encrypted_result in session.scalars(select(Result).join(
                    Night, Result.night_id == Night.id).where(Night.user_id == row.user_id)):
                encrypted_result.key_version = target_version
            for encrypted_alert in session.scalars(select(Alert).where(Alert.user_id == row.user_id)):
                if encrypted_alert.encrypted_detail:
                    encrypted_alert.key_version = target_version
            for subscription in session.scalars(select(PushSubscription).where(
                    PushSubscription.user_id == row.user_id)):
                subscription.key_version = target_version
            rotated += 1
        session.commit()
    return rotated


def rotate_compromised_user_dek(user_id: uuid.UUID) -> None:
    """Replace a user's DEK and re-encrypt every stored per-user ciphertext."""
    from .models import Alert, Night, PushSubscription, Result
    from sqlalchemy import select

    with SessionLocal() as session:
        key_row = session.get(UserDataKey, user_id)
        if key_row is None:
            raise RuntimeError("User encryption key is missing.")
        old_key = user_dek(session, user_id)
        new_key = AESGCM.generate_key(bit_length=256)
        for row in session.scalars(select(Result).join(Night, Result.night_id == Night.id)
                                   .where(Night.user_id == user_id)):
            plaintext = AESGCM(old_key).decrypt(row.nonce, row.encrypted_payload, user_id.bytes)
            nonce = os.urandom(12)
            row.encrypted_payload = AESGCM(new_key).encrypt(nonce, plaintext, user_id.bytes)
            row.nonce = nonce
        for row in session.scalars(select(Alert).where(Alert.user_id == user_id)):
            if row.encrypted_detail and row.detail_nonce:
                plaintext = AESGCM(old_key).decrypt(row.detail_nonce, row.encrypted_detail, user_id.bytes)
                nonce = os.urandom(12)
                row.encrypted_detail = AESGCM(new_key).encrypt(nonce, plaintext, user_id.bytes)
                row.detail_nonce = nonce
        for row in session.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)):
            if row.encrypted_subscription and row.nonce:
                plaintext = AESGCM(old_key).decrypt(row.nonce, row.encrypted_subscription, user_id.bytes)
                nonce = os.urandom(12)
                row.encrypted_subscription = AESGCM(new_key).encrypt(nonce, plaintext, user_id.bytes)
                row.nonce = nonce
        version, master = master_keys()
        for row in session.scalars(select(Result).join(Night, Result.night_id == Night.id)
                                   .where(Night.user_id == user_id)):
            row.key_version = version
        for row in session.scalars(select(Alert).where(Alert.user_id == user_id)):
            if row.encrypted_detail:
                row.key_version = version
        for row in session.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)):
            row.key_version = version
        nonce = os.urandom(12)
        key_row.wrapped_dek = AESGCM(master).encrypt(nonce, new_key, user_id.bytes)
        key_row.nonce = nonce
        key_row.master_key_version = version
        key_row.rotated_at = datetime.now(timezone.utc)
        session.commit()
