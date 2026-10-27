# Single-use email verification and password recovery tokens.
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import text

from src.auth.password_service import (hash_password, normalize_email, validate_password)

VERIFY_LIFETIME = timedelta(hours=24)
RESET_LIFETIME = timedelta(minutes=15)

def ensure_email_token_schema(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS floodwatch_email_tokens (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES floodwatch_users(id)
                    ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                purpose TEXT NOT NULL CHECK (
                    purpose IN ('verify_email', 'reset_password')
                ),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                used_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_floodwatch_email_tokens_user
            ON floodwatch_email_tokens(user_id, purpose)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_floodwatch_email_tokens_expiry
            ON floodwatch_email_tokens(expires_at)
        """))

def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _audit(conn, user_id, action):
    conn.execute(text("""
        INSERT INTO floodwatch_auth_audit (
            id, target_user_id, action, details
        ) VALUES (
            CAST(:id AS UUID),
            CAST(:user_id AS UUID),
            :action,
            '{}'::jsonb
        )
    """), {
        "id": str(uuid4()),
        "user_id": str(user_id),
        "action": action,
    })

def _issue(conn, user_id, purpose, lifetime):
    # The caller must already hold a lock on the user row.
    conn.execute(text("""
        UPDATE floodwatch_email_tokens
        SET revoked_at = NOW()
        WHERE user_id = CAST(:user_id AS UUID)
          AND purpose = :purpose
          AND used_at IS NULL
          AND revoked_at IS NULL
    """), {
        "user_id": str(user_id),
        "purpose": purpose,
    })

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + lifetime

    conn.execute(text("""
        INSERT INTO floodwatch_email_tokens (
            id, user_id, token_hash, purpose, expires_at
        ) VALUES (
            CAST(:id AS UUID),
            CAST(:user_id AS UUID),
            :token_hash,
            :purpose,
            :expires_at
        )
    """), {
        "id": str(uuid4()),
        "user_id": str(user_id),
        "token_hash": _digest(token),
        "purpose": purpose,
        "expires_at": expires_at,
    })

    return token

def issue_email_verification(conn, user_id):
    # Trusted internal service; token must be delivered by email.
    user = conn.execute(text("""
        SELECT id
        FROM floodwatch_users
        WHERE id = CAST(:id AS UUID)
          AND password_hash IS NOT NULL
          AND google_sub IS NULL
          AND email_verified = FALSE
          AND account_status = 'pending_verification'
        FOR UPDATE
    """), {
        "id": str(user_id),
    }).mappings().first()

    if user is None:
        return None

    return _issue(conn, user["id"], "verify_email", VERIFY_LIFETIME)

def issue_password_reset(conn, email):
    # Internal only: public API responses must never expose tokens.
    try:
        address = normalize_email(email)
    except ValueError:
        return None

    user = conn.execute(text("""
        SELECT id
        FROM floodwatch_users
        WHERE LOWER(BTRIM(email)) = :email
          AND password_hash IS NOT NULL
          AND email_verified = TRUE
          AND account_status = 'active'
        FOR UPDATE
    """), {
        "email": address,
    }).mappings().first()

    if user is None:
        return None

    return _issue(conn, user["id"], "reset_password", RESET_LIFETIME)

def _consume_candidate(conn, token, purpose):
    if not isinstance(token, str) or not 32 <= len(token) <= 256:
        return None

    digest = _digest(token)

    record = conn.execute(text("""
        SELECT user_id
        FROM floodwatch_email_tokens
        WHERE token_hash = :digest
          AND purpose = :purpose
    """), {
        "digest": digest,
        "purpose": purpose,
    }).mappings().first()

    if record is None:
        return None

    # Lock users before tokens to keep concurrent operations ordered.
    user = conn.execute(text("""
        SELECT id, email, role, account_status,
               email_verified, password_hash, google_sub
        FROM floodwatch_users
        WHERE id = CAST(:user_id AS UUID)
        FOR UPDATE
    """), {
        "user_id": str(record["user_id"]),
    }).mappings().first()

    if user is None:
        return None

    available = conn.execute(text("""
        SELECT id
        FROM floodwatch_email_tokens
        WHERE token_hash = :digest
          AND purpose = :purpose
          AND used_at IS NULL
          AND revoked_at IS NULL
          AND expires_at > NOW()
        FOR UPDATE
    """), {
        "digest": digest,
        "purpose": purpose,
    }).scalar_one_or_none()

    if available is None:
        return None

    return user, digest

def _mark_used(conn, digest, purpose):
    conn.execute(text("""
        UPDATE floodwatch_email_tokens
        SET used_at = NOW()
        WHERE token_hash = :digest
          AND purpose = :purpose
    """), {
        "digest": digest,
        "purpose": purpose,
    })

def redeem_email_verification(conn, token):
    candidate = _consume_candidate(conn, token, "verify_email")

    if candidate is None:
        return False

    user, digest = candidate

    if (user["account_status"] != "pending_verification" or user["email_verified"] or user["password_hash"] is None or user["google_sub"] is not None):
        return False

    conn.execute(text("""
        UPDATE floodwatch_users
        SET email_verified = TRUE,
            account_status = 'active',
            updated_at = NOW()
        WHERE id = CAST(:id AS UUID)
    """), {
        "id": str(user["id"]),
    })

    _mark_used(conn, digest, "verify_email")
    _audit(conn, user["id"], "local_email_verified")

    return True

def redeem_password_reset(conn, token, new_password):
    candidate = _consume_candidate(conn, token, "reset_password")

    if candidate is None:
        return False

    user, digest = candidate

    if (user["account_status"] != "active" or not user["email_verified"] or user["password_hash"] is None):
        return False

    validate_password(new_password, user["email"])
    new_hash = hash_password(new_password)

    conn.execute(text("""
        UPDATE floodwatch_users
        SET password_hash = :password_hash,
            password_changed_at = NOW(),
            updated_at = NOW()
        WHERE id = CAST(:id AS UUID)
    """), {
        "id": str(user["id"]),
        "password_hash": new_hash,
    })

    _mark_used(conn, digest, "reset_password")

    conn.execute(text("""
        UPDATE floodwatch_email_tokens
        SET revoked_at = NOW()
        WHERE user_id = CAST(:user_id AS UUID)
          AND purpose = 'reset_password'
          AND used_at IS NULL
          AND revoked_at IS NULL
    """), {
        "user_id": str(user["id"]),
    })

    conn.execute(text("""
        UPDATE floodwatch_user_sessions
        SET revoked_at = NOW()
        WHERE user_id = CAST(:user_id AS UUID)
          AND revoked_at IS NULL
    """), {
        "user_id": str(user["id"]),
    })

    _audit(conn, user["id"], "local_password_reset")

    return True
