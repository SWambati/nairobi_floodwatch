# Secure local-account registration and password verification.
from functools import lru_cache
from uuid import uuid4
from email_validator import (EmailNotValidError, validate_email)
from pwdlib import PasswordHash
from sqlalchemy import text

password_hasher = PasswordHash.recommended()

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_PASSWORD_BYTES = 1024

# EMAIL NORMALIZATION
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

# PASSWORD VALIDATION
def validate_password(password, email=None):
    #Requirements:
    #8 to 128 characters
    #At least one uppercase letter
    #At least one number
    #At least one special character
    #No control characters

    if not isinstance(password, str):
        raise ValueError("Invalid password.")

    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters.")

    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password exceeds the permitted size.")

    if password.isspace():
        raise ValueError("Password cannot consist entirely of whitespace.")

    if any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise ValueError("Password contains invalid control characters.")

    has_uppercase = any("A" <= char <= "Z" for char in password)
    has_number = any("0" <= char <= "9" for char in password)
    has_special = any(not char.isalnum() and not char.isspace() for char in password)

    if not has_uppercase:
        raise ValueError("Password must contain at least one uppercase letter.")

    if not has_number:
        raise ValueError("Password must contain at least one number.")

    if not has_special:
        raise ValueError("Password must contain at least one special character.")

    if email and password.casefold() == email.casefold():
        raise ValueError("Password must not match the email address.")

    return password

# PASSWORD HASHING
def hash_password(password):
    validate_password(password)

    return password_hasher.hash(password)

@lru_cache(maxsize=1)
def _dummy_password_hash():
    #Reduce timing differences for nonexistent users.
    return password_hasher.hash("FloodWatch non-existent-account verification value")

# EMAIL UNIQUENESS
def ensure_local_email_uniqueness(engine):
    #Ensure local password accounts have unique emails.
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_floodwatch_local_password_email_ci
            ON floodwatch_users (
                LOWER(BTRIM(email))
            )
            WHERE password_hash IS NOT NULL
        """))

# RESIDENT REGISTRATION
def register_local_user(conn, *, email, password, display_name):
    #Register a new Resident account.
    #New Residents are immediately active.
    #Email verification remains FALSE until a valid verification OTP is redeemed.
    #Unverified Residents may receive restricted access according to the authorization policy.
    #Agency and Admin roles cannot be self-assigned.

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

    # Store the password hash, never the plaintext password.
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
            'active'
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
            CAST(:details AS jsonb)
        )
    """), {
        "audit_id": str(uuid4()),
        "user_id": user_id,
        "details": (
            '{"registration_method":"password",'
            '"email_verification":"pending",'
            '"initial_role":"resident"}'
        ),
    })

    return {
        "id": user_id,
        "email": email,
        "display_name": display_name,
        "role": "resident",
        "account_status": "active",
        "email_verified": False,
    }

# EMAIL/PASSWORD VERIFICATION
def verify_password_first_factor(conn, *, email, password):
    #Authenticate the password against its stored hash.
    #Residents active accounts can authenticate even when their email is not yet verified.
    #Agency/Admin must have an active account and verified email.
    #This function does NOT create sessions, complete MFA, grant privileged roles, getermine verification-grace authorization
    
    if not isinstance(password, str):
        return None

    if (not password or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES):
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

    if row is None:
        return None

    # Inactive and suspended accounts cannot authenticate.
    if row["account_status"] != "active":
        return None

    role = row["role"]
    email_verified = bool(row["email_verified"])

    # Privileged accounts always require verified email.
    if role in {"admin", "agency"} and not email_verified:
        return None

    # Reject unknown roles.
    if role not in {"resident", "agency", "admin"}:
        return None

    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "role": role,
        "email_verified": email_verified,
        "first_factor_verified": True,
    }
