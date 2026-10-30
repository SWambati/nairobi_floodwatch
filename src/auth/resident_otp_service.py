# Single-use email OTP verification for Resident accounts.
import hashlib
import hmac
import math
import secrets
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import text

from src.auth.config import get_auth_settings

OTP_LIFETIME = timedelta(minutes=10)
OTP_COOLDOWN = timedelta(seconds=60)
OTP_HOURLY_WINDOW = timedelta(hours=1)
MAX_SENDS_PER_HOUR = 3
MAX_VERIFICATION_ATTEMPTS = 5


class OTPRequestLimited(Exception):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = max(1, math.ceil(retry_after))


def _code_hash(user_id, code):
    #Keyed hashing prevents offline enumeration of six-digit OTPs if database records are exposed.
    secret = get_auth_settings().oauth_session_secret

    if not secret:
        raise RuntimeError("OTP signing secret is unavailable.")

    payload = f"resident-email-otp:v1:{user_id}:{code}"

    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

def ensure_resident_otp_schema(engine):
    # Create persistent, single-use Resident OTP storage.

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS
                floodwatch_resident_email_otps (
                    user_id UUID PRIMARY KEY
                        REFERENCES floodwatch_users(id)
                        ON DELETE CASCADE,

                    code_hash TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,

                    attempts INTEGER NOT NULL DEFAULT 0,

                    last_sent_at TIMESTAMPTZ NOT NULL,
                    window_started_at TIMESTAMPTZ NOT NULL,
                    window_count INTEGER NOT NULL DEFAULT 1,

                    used_at TIMESTAMPTZ
                )
        """))

def issue_resident_email_otp(conn, user_id):
    #Issue a new OTP for the authenticated Resident.
    #Must be called inside a database transaction.
    #Locks the account row to serialize concurrent issuance and verification operations.
    #Returns the OTP only to trusted server-side code.
    
    user = conn.execute(text("""
        SELECT id, email, email_verified
        FROM floodwatch_users
        WHERE id = CAST(:user_id AS UUID)
          AND role = 'resident'
          AND account_status = 'active'
        FOR UPDATE
    """), {
        "user_id": str(user_id),
    }).mappings().first()

    if user is None:
        raise ValueError("Resident account is unavailable.")

    if user["email_verified"]:
        return None

    existing = conn.execute(text("""
        SELECT
            last_sent_at,
            window_started_at,
            window_count
        FROM floodwatch_resident_email_otps
        WHERE user_id = CAST(:user_id AS UUID)
        FOR UPDATE
    """), {
        "user_id": str(user_id),
    }).mappings().first()

    now = conn.execute(text("SELECT NOW()")).scalar_one()
    window_started = now
    window_count = 0

    if existing:
        cooldown_remaining = (OTP_COOLDOWN - (now - existing["last_sent_at"])).total_seconds()

        if cooldown_remaining > 0:
            raise OTPRequestLimited("Please wait before requesting another code.", cooldown_remaining)

        window_age = now - existing["window_started_at"]

        if window_age < OTP_HOURLY_WINDOW:
            window_started = existing["window_started_at"]
            window_count = existing["window_count"]

            if window_count >= MAX_SENDS_PER_HOUR:
                remaining = (OTP_HOURLY_WINDOW - window_age).total_seconds()

                raise OTPRequestLimited("Too many verification requests. Try again later.", remaining)

    code = f"{secrets.randbelow(1_000_000):06d}"
    digest = _code_hash(user["id"], code)
    expires_at = now + OTP_LIFETIME

    conn.execute(text("""
        INSERT INTO floodwatch_resident_email_otps (
            user_id,
            code_hash,
            expires_at,
            attempts,
            last_sent_at,
            window_started_at,
            window_count,
            used_at
        )
        VALUES (
            CAST(:user_id AS UUID),
            :code_hash,
            :expires_at,
            0,
            :sent_at,
            :window_started_at,
            :window_count,
            NULL
        )
        ON CONFLICT (user_id)
        DO UPDATE SET
            code_hash = EXCLUDED.code_hash,
            expires_at = EXCLUDED.expires_at,
            attempts = 0,
            last_sent_at = EXCLUDED.last_sent_at,
            window_started_at = EXCLUDED.window_started_at,
            window_count = EXCLUDED.window_count,
            used_at = NULL
    """), {
        "user_id": str(user["id"]),
        "code_hash": digest,
        "expires_at": expires_at,
        "sent_at": now,
        "window_started_at": window_started,
        "window_count": window_count + 1,
    })

    return {
        "email": user["email"],
        "code": code,
        "expires_at": expires_at,
    }

def verify_resident_email_otp(conn, user_id, code):
    #Validate and consume a six-digit OTP.
    #Five failed attempts invalidate the current code.
    #A successful verification updates the Resident's email_verified field in the same transaction.

    if (not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit()):
        return False

    user = conn.execute(text("""
        SELECT id, email_verified
        FROM floodwatch_users
        WHERE id = CAST(:user_id AS UUID)
          AND role = 'resident'
          AND account_status = 'active'
        FOR UPDATE
    """), {
        "user_id": str(user_id),
    }).mappings().first()

    if user is None or user["email_verified"]:
        return False

    record = conn.execute(text("""
        SELECT
            code_hash,
            expires_at,
            attempts,
            used_at
        FROM floodwatch_resident_email_otps
        WHERE user_id = CAST(:user_id AS UUID)
        FOR UPDATE
    """), {
        "user_id": str(user_id),
    }).mappings().first()

    if record is None:
        return False

    now = conn.execute(text("SELECT NOW()")).scalar_one()

    if (record["used_at"] is not None or record["expires_at"] <= now or record["attempts"] >= MAX_VERIFICATION_ATTEMPTS):
        return False

    submitted_hash = _code_hash(user["id"], code)
    valid = hmac.compare_digest(submitted_hash, record["code_hash"])

    if not valid:
        conn.execute(text("""
            UPDATE floodwatch_resident_email_otps
            SET attempts = attempts + 1,
                used_at = CASE
                    WHEN attempts + 1 >= :maximum
                    THEN NOW()
                    ELSE used_at
                END
            WHERE user_id = CAST(:user_id AS UUID)
        """), {
            "maximum": MAX_VERIFICATION_ATTEMPTS,
            "user_id": str(user_id),
        })

        return False

    # Activate verified-email privileges.
    conn.execute(text("""
        UPDATE floodwatch_users
        SET email_verified = TRUE,
            updated_at = NOW()
        WHERE id = CAST(:user_id AS UUID)
          AND role = 'resident'
          AND account_status = 'active'
          AND email_verified = FALSE
    """), {
        "user_id": str(user_id),
    })

    # Consume OTP permanently.
    conn.execute(text("""
        UPDATE floodwatch_resident_email_otps
        SET used_at = NOW()
        WHERE user_id = CAST(:user_id AS UUID)
    """), {
        "user_id": str(user_id),
    })

    # Maintain the authentication audit trail.
    conn.execute(text("""
        INSERT INTO floodwatch_auth_audit (
            id,
            actor_user_id,
            target_user_id,
            action,
            details
        )
        VALUES (
            CAST(:audit_id AS UUID),
            CAST(:user_id AS UUID),
            CAST(:user_id AS UUID),
            'resident_email_otp_verified',
            '{}'::jsonb
        )
    """), {
        "audit_id": str(uuid4()),
        "user_id": str(user_id),
    })

    return True
