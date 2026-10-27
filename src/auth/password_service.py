# Secure local-account registration and password verification.
from functools import lru_cache
from uuid import uuid4
from email_validator import EmailNotValidError, validate_email
from pwdlib import PasswordHash
from sqlalchemy import text

password_hasher = PasswordHash.recommended()

def normalize_email(email):
    if not isinstance(email, str) or len(email) > 320:
        raise ValueError("Invalid email address.")

    try:
        result = validate_email(email.strip(), check_deliverability=False, allow_smtputf8=False)
    except EmailNotValidError as exc:
        raise ValueError("Invalid email address.") from exc

    normalized = result.normalized.lower()

    if len(normalized) > 254:
        raise ValueError("Email address is too long.")

    return normalized

def validate_password(password, email=None):
    # Use long passwords without arbitrary character-class rules.
    if not isinstance(password, str):
        raise ValueError("Invalid password.")

    if not 15 <= len(password) <= 128:
        raise ValueError("Password must contain between 15 and 128 characters.")

    if len(password.encode("utf-8")) > 1024:
        raise ValueError("Password exceeds the permitted size.")

    if password.isspace() or any(ord(char) < 32 for char in password):
        raise ValueError("Password contains invalid control characters.")

    if email and password.casefold() == email.casefold():
        raise ValueError("Password must not match the email address.")

    return password

def hash_password(password):
    validate_password(password)
    return password_hasher.hash(password)

@lru_cache(maxsize=1)
def _dummy_password_hash():
    # Reduce timing differences when a user does not exist.
    return password_hasher.hash("FloodWatch non-existent-account verification value")

def ensure_local_email_uniqueness(engine):
    """One-time constraint for all local accounts, verified or pending."""

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_floodwatch_local_password_email_ci
            ON floodwatch_users (LOWER(BTRIM(email)))
            WHERE password_hash IS NOT NULL
        """))

def register_local_user(conn, *, email, password, display_name):
    # Create an unverified resident. Caller controls the transaction.
    email = normalize_email(email)
    validate_password(password, email)

    if not isinstance(display_name, str):
        raise ValueError("Invalid display name.")

    display_name = display_name.strip()

    if not 1 <= len(display_name) <= 150:
        raise ValueError("Display name must contain between 1 and 150 characters.")

    if any(ord(char) < 32 for char in display_name):
        raise ValueError("Display name contains control characters.")

    existing = conn.execute(text("""
        SELECT id
        FROM floodwatch_users
        WHERE LOWER(BTRIM(email)) = :email
        LIMIT 1
    """), {
        "email": email,
    }).first()

    if existing:
        raise ValueError("An account cannot be created with this email.")

    user_id = str(uuid4())
    encoded_password = hash_password(password)

    conn.execute(text("""
        INSERT INTO floodwatch_users (
            id,
            google_sub,
            email,
            email_verified,
            display_name,
            password_hash,
            password_changed_at,
            role,
            account_status
        )
        VALUES (
            CAST(:id AS UUID),
            NULL,
            :email,
            FALSE,
            :display_name,
            :password_hash,
            NOW(),
            'resident',
            'pending_verification'
        )
    """), {
        "id": user_id,
        "email": email,
        "display_name": display_name,
        "password_hash": encoded_password,
    })

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
            NULL,
            CAST(:user_id AS UUID),
            'password_account_created',
            '{}'::jsonb
        )
    """), {
        "audit_id": str(uuid4()),
        "user_id": user_id,
    })

    return {
        "id": user_id,
        "email": email,
        "display_name": display_name,
        "role": "resident",
        "account_status": "pending_verification",
        "email_verified": False,
    }

def verify_password_first_factor(conn, *, email, password):
    # Verify credentials only.
    # This does NOT create a session or satisfy any required MFA.

    if not isinstance(password, str):
        return None

    if not password or len(password.encode("utf-8")) > 1024:
        return None

    try:
        email = normalize_email(email)
    except ValueError:
        password_hasher.verify(password, _dummy_password_hash())
        return None

    row = conn.execute(text("""
        SELECT
            id,
            email,
            display_name,
            password_hash,
            email_verified,
            role,
            account_status
        FROM floodwatch_users
        WHERE LOWER(BTRIM(email)) = :email
          AND password_hash IS NOT NULL
        LIMIT 1
    """), {
        "email": email,
    }).mappings().first()

    candidate_hash = (row["password_hash"] if row else _dummy_password_hash())

    if not password_hasher.verify(password, candidate_hash):
        return None

    if not row:
        return None

    if not row["email_verified"]:
        return None

    if row["account_status"] != "active":
        return None

    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "first_factor_verified": True,
    }
