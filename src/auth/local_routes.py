
# Local login, registration, email verification, and password recovery APIs.

import hashlib
import hmac
import logging
import secrets

from datetime import datetime, timedelta, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.auth.config import get_auth_settings
from src.auth.password_service import (
    normalize_email,
    register_local_user,
    validate_password,
    verify_password_first_factor,
)
from src.auth.email_token_service import (
    issue_email_verification,
    redeem_email_verification,
    issue_password_reset,
    redeem_password_reset,
)
from src.auth.email_delivery import (
    EmailSettings,
    EmailConfigurationError,
    EmailDeliveryError,
    send_verification_email,
    send_password_reset_email,
    send_password_change_notice,
)
from src.auth.google_oauth import (
    SESSION_COOKIE,
    _post_login_destination,
)
from src.operational.persistence import create_database_engine


logger = logging.getLogger(__name__)

REQUEST_ACCEPTED = (
    "Request received. If the account is eligible and email "
    "delivery is available, check your inbox for instructions."
)


# ---------------------------------------------------------
# Request validation
# ---------------------------------------------------------

def require_same_origin_json(request: Request):
    settings = get_auth_settings()

    if request.headers.get("origin") != settings.base_url:
        raise HTTPException(
            status_code=403,
            detail="Invalid request origin.",
        )

    content_type = (
        request.headers.get("content-type", "")
        .split(";")[0]
        .strip()
        .lower()
    )

    if content_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail="Application/json is required.",
        )


router = APIRouter(
    prefix="/auth/local",
    dependencies=[Depends(require_same_origin_json)],
    include_in_schema=False,
)


# ---------------------------------------------------------
# Request models
# ---------------------------------------------------------

class RegistrationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=15, max_length=128)
    display_name: str = Field(min_length=1, max_length=150)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class PasswordResetRequest(TokenRequest):
    new_password: str = Field(min_length=15, max_length=128)


# ---------------------------------------------------------
# Database connection
# ---------------------------------------------------------

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()


def ensure_local_api_schema(engine):
    # Persistent, concurrency-safe request throttling.

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS floodwatch_auth_rate_limits (
                bucket_hash TEXT PRIMARY KEY,
                window_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_floodwatch_auth_rate_limits_window
            ON floodwatch_auth_rate_limits(window_started_at)
        """))


# ---------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------

def _reply(message, status_code=200):
    return JSONResponse(
        status_code=status_code,
        content={"message": message},
        headers={"Cache-Control": "no-store"},
    )


def _valid_email(email):
    try:
        return normalize_email(email)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid email address.",
        ) from exc


def _require_email_delivery():
    try:
        EmailSettings.from_environment()
    except EmailConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Email service is not configured.",
        ) from exc


def _increment_bucket(conn, bucket_hash, max_attempts):
    attempts = conn.execute(text("""
        INSERT INTO floodwatch_auth_rate_limits (
            bucket_hash,
            window_started_at,
            attempts
        )
        VALUES (
            :bucket,
            NOW(),
            1
        )
        ON CONFLICT (bucket_hash)
        DO UPDATE SET
            attempts = CASE
                WHEN floodwatch_auth_rate_limits.window_started_at
                     <= NOW() - INTERVAL '1 hour'
                THEN 1
                ELSE floodwatch_auth_rate_limits.attempts + 1
            END,
            window_started_at = CASE
                WHEN floodwatch_auth_rate_limits.window_started_at
                     <= NOW() - INTERVAL '1 hour'
                THEN NOW()
                ELSE floodwatch_auth_rate_limits.window_started_at
            END
        RETURNING attempts
    """), {
        "bucket": bucket_hash,
    }).scalar_one()

    return attempts <= max_attempts


def _throttle(
    request,
    action,
    email=None,
    ip_limit=12,
    email_limit=3,
):
    settings = get_auth_settings()

    secret = settings.oauth_session_secret.encode("utf-8")

    if not secret:
        raise RuntimeError(
            "OAuth session secret is required."
        )

    client_ip = (
        request.client.host
        if request.client
        else "unknown"
    )

    targets = [
        (f"{action}:ip", client_ip, ip_limit)
    ]

    if email:
        targets.append(
            (f"{action}:email", email, email_limit)
        )

    allowed = True

    with _engine().begin() as conn:
        for scope, value, maximum in targets:

            bucket = hmac.new(
                secret,
                f"{scope}:{value}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if not _increment_bucket(
                conn,
                bucket,
                maximum,
            ):
                allowed = False

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many requests. "
                "Please try again later."
            ),
            headers={"Retry-After": "3600"},
        )


def _attempt_delivery(delivery_function, *args):
    try:
        delivery_function(*args)
    except (
        EmailDeliveryError,
        EmailConfigurationError,
    ):
        logger.warning(
            "FloodWatch transactional email delivery failed."
        )


# ---------------------------------------------------------
# Local email/password login
# ---------------------------------------------------------

@router.post("/login")
def login(request: Request, data: LoginRequest):
    """
    Authenticate a verified local FloodWatch account.

    Successful password verification creates a database-backed
    session, but does not automatically satisfy MFA.
    """

    try:
        email = normalize_email(data.email)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )

    # Persistent login throttling.
    _throttle(
        request,
        "login",
        email,
        ip_limit=30,
        email_limit=10,
    )

    settings = get_auth_settings()

    # Match Google OAuth's existing session lifetime.
    lifetime = settings.user_session_max_age

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=lifetime)
    )

    with _engine().begin() as conn:

        # Verify the password against its stored hash.
        user = verify_password_first_factor(
            conn,
            email=email,
            password=data.password,
        )

        if not user:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password.",
            )

        # Confirm that the account remains eligible.
        account = conn.execute(text("""
            SELECT
                id,
                role
            FROM floodwatch_users
            WHERE id = CAST(:user_id AS UUID)
              AND account_status = 'active'
              AND email_verified = TRUE
              AND password_hash IS NOT NULL
            LIMIT 1
        """), {
            "user_id": str(user["id"]),
        }).mappings().one_or_none()

        if account is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password.",
            )

        # Generate a secure session token.
        session_token = secrets.token_urlsafe(48)

        # Store only the hash in PostgreSQL.
        token_hash = hashlib.sha256(
            session_token.encode("utf-8")
        ).hexdigest()

        # Create a session using the same table as Google OAuth.
        # MFA remains incomplete until explicitly verified.
        conn.execute(text("""
            INSERT INTO floodwatch_user_sessions (
                token_hash,
                user_id,
                expires_at,
                auth_method,
                mfa_verified_at
            )
            VALUES (
                :token_hash,
                CAST(:user_id AS UUID),
                :expires_at,
                'password',
                NULL
            )
        """), {
            "token_hash": token_hash,
            "user_id": str(account["id"]),
            "expires_at": expires_at,
        })

        role = account["role"]

    # Use the existing Google OAuth post-login routing.
    # The new session has already been committed.
    destination = _post_login_destination(
        session_token
    )

    response = JSONResponse(
        status_code=200,
        content={
            "authenticated": True,
            "role": role,
            "next": destination,
        },
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )

    # Issue the same secure session cookie as Google OAuth.
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_token,
        max_age=lifetime,
        httponly=True,
        secure=settings.secure_cookies,
        samesite=settings.cookie_samesite,
        path="/",
    )

    return response


# ---------------------------------------------------------
# Local account registration
# ---------------------------------------------------------

@router.post("/register", status_code=202)
def register(
    request: Request,
    data: RegistrationRequest,
):
    _require_email_delivery()

    email = _valid_email(data.email)

    try:
        validate_password(
            data.password,
            email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    display_name = data.display_name.strip()

    if (
        not display_name
        or any(ord(char) < 32 for char in display_name)
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid display name.",
        )

    _throttle(
        request,
        "register",
        email,
        ip_limit=8,
        email_limit=3,
    )

    try:
        with _engine().begin() as conn:

            user = register_local_user(
                conn,
                email=email,
                password=data.password,
                display_name=display_name,
            )

            token = issue_email_verification(
                conn,
                user["id"],
            )

    except ValueError:
        # Do not disclose whether the account already exists.
        return _reply(REQUEST_ACCEPTED, 202)

    except IntegrityError as exc:
        sqlstate = (
            getattr(exc.orig, "sqlstate", None)
            or getattr(exc.orig, "pgcode", None)
        )

        if sqlstate != "23505":
            raise

        return _reply(REQUEST_ACCEPTED, 202)

    if token:
        _attempt_delivery(
            send_verification_email,
            email,
            token,
        )

    return _reply(REQUEST_ACCEPTED, 202)


# ---------------------------------------------------------
# Resend email verification
# ---------------------------------------------------------

@router.post("/resend-verification", status_code=202)
def resend_verification(
    request: Request,
    data: EmailRequest,
):
    _require_email_delivery()

    email = _valid_email(data.email)

    _throttle(
        request,
        "resend-verification",
        email,
        ip_limit=10,
        email_limit=3,
    )

    token = None

    with _engine().begin() as conn:
        user_id = conn.execute(text("""
            SELECT id
            FROM floodwatch_users
            WHERE LOWER(BTRIM(email)) = :email
              AND google_sub IS NULL
              AND password_hash IS NOT NULL
              AND email_verified = FALSE
              AND account_status = 'pending_verification'
            LIMIT 1
        """), {
            "email": email,
        }).scalar_one_or_none()

        if user_id:
            token = issue_email_verification(
                conn,
                user_id,
            )

    if token:
        _attempt_delivery(
            send_verification_email,
            email,
            token,
        )

    return _reply(REQUEST_ACCEPTED, 202)


# ---------------------------------------------------------
# Verify email address
# ---------------------------------------------------------

@router.post("/verify-email")
def verify_email(
    request: Request,
    data: TokenRequest,
):
    _throttle(
        request,
        "verify-email",
        ip_limit=20,
    )

    with _engine().begin() as conn:
        verified = redeem_email_verification(
            conn,
            data.token,
        )

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired verification link.",
        )

    return _reply(
        "Email verified successfully. You may now sign in."
    )


# ---------------------------------------------------------
# Request password reset
# ---------------------------------------------------------

@router.post("/request-password-reset", status_code=202)
def request_password_reset(
    request: Request,
    data: EmailRequest,
):
    _require_email_delivery()

    email = _valid_email(data.email)

    _throttle(
        request,
        "request-password-reset",
        email,
        ip_limit=10,
        email_limit=3,
    )

    with _engine().begin() as conn:
        token = issue_password_reset(
            conn,
            email,
        )

    if token:
        _attempt_delivery(
            send_password_reset_email,
            email,
            token,
        )

    return _reply(REQUEST_ACCEPTED, 202)


# ---------------------------------------------------------
# Complete password reset
# ---------------------------------------------------------

@router.post("/reset-password")
def reset_password(
    request: Request,
    data: PasswordResetRequest,
):
    try:
        validate_password(
            data.new_password
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    _throttle(
        request,
        "reset-password",
        ip_limit=12,
    )

    token_hash = hashlib.sha256(
        data.token.encode("utf-8")
    ).hexdigest()

    recipient = None

    with _engine().begin() as conn:
        updated = redeem_password_reset(
            conn,
            data.token,
            data.new_password,
        )

        if updated:
            recipient = conn.execute(text("""
                SELECT u.email
                FROM floodwatch_email_tokens t
                JOIN floodwatch_users u
                    ON u.id = t.user_id
                WHERE t.token_hash = :token_hash
                  AND t.purpose = 'reset_password'
            """), {
                "token_hash": token_hash,
            }).scalar_one_or_none()

    if not updated:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired password reset link.",
        )

    if recipient:
        _attempt_delivery(
            send_password_change_notice,
            recipient,
        )

    return _reply(
        "Password changed successfully. Please sign in again."
    )
