#Google Authenticator TOTP and recovery services for FloodWatch.
import hashlib
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pyotp
from cryptography.fernet import Fernet
from sqlalchemy import text

ISSUER = "Nairobi FloodWatch"
TOTP_PERIOD = 30
ENROLLMENT_MINUTES = 10
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
RECOVERY_CODE_COUNT = 10

def _cipher():
    key = os.getenv("FLOODWATCH_MFA_ENCRYPTION_KEY", "").strip()

    if not key:
        raise RuntimeError("FLOODWATCH_MFA_ENCRYPTION_KEY is not configured.")

    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise RuntimeError("Invalid FloodWatch MFA encryption key.") from exc

def _encrypt(secret):
    return _cipher().encrypt(secret.encode("ascii")).decode("ascii")

def _decrypt(ciphertext):
    return _cipher().decrypt(ciphertext.encode("ascii")).decode("ascii")

def _user(conn, user_id):
    #Lock and validate an active, verified account.

    row = conn.execute(text("""
        SELECT id, email, role
        FROM floodwatch_users
        WHERE id = CAST(:id AS UUID)
          AND account_status = 'active'
          AND email_verified = TRUE
        FOR UPDATE
    """), {
        "id": str(user_id),
    }).mappings().first()

    if row is None:
        raise PermissionError("An active, verified account is required.")

    return row

def _factor(conn, user_id):
    return conn.execute(text("""
        SELECT *
        FROM floodwatch_mfa_factors
        WHERE user_id = CAST(:id AS UUID)
        FOR UPDATE
    """), {
        "id": str(user_id),
    }).mappings().first()

def _audit(conn, user_id, action):
    conn.execute(text("""
        INSERT INTO floodwatch_auth_audit (
            id, actor_user_id, target_user_id,
            action, details
        )
        VALUES (
            CAST(:id AS UUID),
            CAST(:user_id AS UUID),
            CAST(:user_id AS UUID),
            :action,
            '{}'::jsonb
        )
    """), {
        "id": str(uuid4()),
        "user_id": str(user_id),
        "action": action,
    })

def _check_lock(factor):
    locked_until = factor["locked_until"]

    if (locked_until is not None and locked_until > datetime.now(timezone.utc)):
        raise PermissionError("Too many MFA attempts. Try again later.")

def _record_failure(conn, user_id, factor):
    now = datetime.now(timezone.utc)
    previous_lock = factor["locked_until"]

    if previous_lock and previous_lock <= now:
        attempts = 1
    else:
        attempts = factor["failed_attempts"] + 1

    locked_until = (now + timedelta(minutes=LOCKOUT_MINUTES) if attempts >= MAX_ATTEMPTS else None)

    conn.execute(text("""
        UPDATE floodwatch_mfa_factors
        SET failed_attempts = :attempts,
            locked_until = :locked_until,
            updated_at = NOW()
        WHERE user_id = CAST(:id AS UUID)
    """), {
        "id": str(user_id),
        "attempts": attempts,
        "locked_until": locked_until,
    })

    if locked_until:
        _audit(conn, user_id, "mfa_temporarily_locked")

def _reset_attempts(conn, user_id):
    conn.execute(text("""
        UPDATE floodwatch_mfa_factors
        SET failed_attempts = 0,
            locked_until = NULL,
            updated_at = NOW()
        WHERE user_id = CAST(:id AS UUID)
    """), {
        "id": str(user_id),
    })

def _matching_step(secret, code, last_accepted_step):
    #Accept a nearby time step only if it has not been used.
    if not isinstance(code, str):
        return None

    if not re.fullmatch(r"[0-9]{6}", code):
        return None

    totp = pyotp.TOTP(secret, digits=6, interval=TOTP_PERIOD)
    current_step = int(time.time() // TOTP_PERIOD)
    matched = None

    for step in range(current_step - 1, current_step + 2):
        expected = totp.at(step * TOTP_PERIOD)

        if (secrets.compare_digest(expected, code) and step > last_accepted_step):
            matched = step if matched is None else max(matched, step)

    return matched

def _new_recovery_codes(conn, user_id):
    codes = []

    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(16).upper()

        formatted = "-".join(raw[i:i + 8] for i in range(0, len(raw), 8))
        digest = hashlib.sha256(raw.encode("ascii")).hexdigest()

        conn.execute(text("""
            INSERT INTO floodwatch_mfa_recovery_codes (
                id, user_id, code_hash
            )
            VALUES (
                CAST(:id AS UUID),
                CAST(:user_id AS UUID),
                :digest
            )
        """), {
            "id": str(uuid4()),
            "user_id": str(user_id),
            "digest": digest,
        })

        codes.append(formatted)

    return codes

def get_mfa_status(conn, user_id):
    #Return enrollment state without exposing secrets.
    _user(conn, user_id)
    factor = _factor(conn, user_id)

    return {
        "enabled": bool(factor and factor["status"] == "active"),
        "pending": bool(factor and factor["status"] == "pending" and factor["pending_expires_at"] and factor["pending_expires_at"] > datetime.now(timezone.utc)),
    }


def begin_totp_enrollment(conn, user_id):
    # Prepare enrollment. The caller must verify user identity and require recent reauthentication before calling this.
    user = _user(conn, user_id)
    factor = _factor(conn, user_id)

    if factor and factor["status"] == "active":
        raise PermissionError("MFA is already enabled for this account.")

    now = datetime.now(timezone.utc)

    if (factor and factor["status"] == "pending" and factor["pending_expires_at"] and factor["pending_expires_at"] > now):
        secret = _decrypt(factor["secret_ciphertext"])
        expires_at = factor["pending_expires_at"]

    else:
        secret = pyotp.random_base32()
        expires_at = now + timedelta(minutes=ENROLLMENT_MINUTES)
        ciphertext = _encrypt(secret)

        if factor:
            conn.execute(text("""
                UPDATE floodwatch_mfa_factors
                SET secret_ciphertext = :ciphertext,
                    status = 'pending',
                    pending_expires_at = :expires_at,
                    activated_at = NULL,
                    last_accepted_step = -1,
                    failed_attempts = 0,
                    locked_until = NULL,
                    updated_at = NOW()
                WHERE user_id = CAST(:id AS UUID)
            """), {
                "id": str(user_id),
                "ciphertext": ciphertext,
                "expires_at": expires_at,
            })

        else:
            conn.execute(text("""
                INSERT INTO floodwatch_mfa_factors (
                    user_id, secret_ciphertext,
                    status, pending_expires_at
                )
                VALUES (
                    CAST(:id AS UUID),
                    :ciphertext,
                    'pending',
                    :expires_at
                )
            """), {
                "id": str(user_id),
                "ciphertext": ciphertext,
                "expires_at": expires_at,
            })

    uri = pyotp.TOTP(secret, digits=6, interval=TOTP_PERIOD).provisioning_uri(name=user["email"], issuer_name=ISSUER)

    return {
        "otpauth_uri": uri,
        "expires_at": expires_at.isoformat(),
        "status": "pending",
    }

def confirm_totp_enrollment(conn, user_id, code):
    #Enable MFA only after a valid authenticator code.
    _user(conn, user_id)
    factor = _factor(conn, user_id)

    if not factor or factor["status"] != "pending":
        return None

    if (not factor["pending_expires_at"] or factor["pending_expires_at"] <= datetime.now(timezone.utc)):
        return None

    _check_lock(factor)
    secret = _decrypt(factor["secret_ciphertext"])
    step = _matching_step(secret, code, factor["last_accepted_step"])

    if step is None:
        _record_failure(conn, user_id, factor)
        return None

    conn.execute(text("""
        UPDATE floodwatch_mfa_factors
        SET status = 'active',
            activated_at = NOW(),
            pending_expires_at = NULL,
            last_accepted_step = :step,
            failed_attempts = 0,
            locked_until = NULL,
            updated_at = NOW()
        WHERE user_id = CAST(:id AS UUID)
    """), {
        "id": str(user_id),
        "step": step,
    })

    codes = _new_recovery_codes(conn, user_id)
    _audit(conn, user_id, "mfa_totp_enabled")

    return {
        "enabled": True,
        "recovery_codes": codes,
    }

def verify_totp_code(conn, user_id, code):
    #Verify a code once, with replay and attempt protection.
    _user(conn, user_id)
    factor = _factor(conn, user_id)

    if not factor or factor["status"] != "active":
        return False

    _check_lock(factor)
    secret = _decrypt(factor["secret_ciphertext"])
    step = _matching_step(secret, code, factor["last_accepted_step"])

    if step is None:
        _record_failure(conn, user_id, factor)
        return False

    conn.execute(text("""
        UPDATE floodwatch_mfa_factors
        SET last_accepted_step = :step,
            failed_attempts = 0,
            locked_until = NULL,
            updated_at = NOW()
        WHERE user_id = CAST(:id AS UUID)
    """), {
        "id": str(user_id),
        "step": step,
    })

    _audit(conn, user_id, "mfa_totp_verified")
    return True

def use_recovery_code(conn, user_id, code):
    #Redeem a recovery code exactly once.
    _user(conn, user_id)
    factor = _factor(conn, user_id)

    if not factor or factor["status"] != "active":
        return False

    _check_lock(factor)

    normalized = (code.replace("-", "").replace(" ", "").upper() if isinstance(code, str) and len(code) <= 64 else "")

    if re.fullmatch(r"[0-9A-F]{32}", normalized):
        digest = hashlib.sha256(normalized.encode("ascii")).hexdigest()

        redeemed = conn.execute(text("""
            UPDATE floodwatch_mfa_recovery_codes
            SET used_at = NOW()
            WHERE user_id = CAST(:user_id AS UUID)
              AND code_hash = :digest
              AND used_at IS NULL
            RETURNING id
        """), {
            "user_id": str(user_id),
            "digest": digest,
        }).scalar_one_or_none()

    else:
        redeemed = None

    if redeemed is None:
        _record_failure(conn, user_id, factor)
        return False

    _reset_attempts(conn, user_id)
    _audit(conn, user_id, "mfa_recovery_code_used")

    return True
