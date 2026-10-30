# Local authentication: login, registration, email verification, password recovery, and persistent request throttling.
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.auth.config import get_auth_settings
from src.auth.password_service import (normalize_email, register_local_user, validate_password, verify_password_first_factor,)
from src.auth.email_token_service import (issue_email_verification, redeem_email_verification, issue_password_reset, redeem_password_reset)
from src.auth.email_delivery import (EmailSettings, EmailConfigurationError, EmailDeliveryError, send_verification_email, send_password_reset_email, send_password_change_notice)
from src.auth.google_oauth import (SESSION_COOKIE, _post_login_destination)
from src.operational.persistence import create_database_engine
from src.auth.resident_verification import ensure_resident_verification_schema, start_resident_grace_period
from src.auth.resident_otp_service import (issue_resident_email_otp)
from src.auth.email_delivery import (send_resident_verification_otp)
logger = logging.getLogger(__name__)
REQUEST_ACCEPTED = ("Request received. If the account is eligible, check your email for further instructions.")
REGISTRATION_PENDING = (
    "Your Resident account has been created successfully! "
    "You can now access the Nairobi FloodWatch Resident Dashboard. "
    "Please use your registered email and password to sign in on future visits. Email verification will be available soon."
)

REGISTRATION_SUCCESS = (
    "Your Nairobi FloodWatch Resident account has been created successfully! You can now access your Resident Dashboard. "
    "An email verification code has been requested for your registered email address. Open My Account to complete verification, or request a new code if it does not arrive. "
    "Use your registered email and password to sign in on future visits."
)

# REQUEST SECURITY
def require_same_origin_json(request: Request):
    settings = get_auth_settings()

    if request.headers.get("origin") != settings.base_url:
        raise HTTPException(status_code=403, detail="Invalid request origin.")

    content_type = (request.headers.get("content-type", "").split(";")[0].strip().lower())

    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Application/json is required.")

router = APIRouter(prefix="/auth/local", dependencies=[Depends(require_same_origin_json)], include_in_schema=False)

# REQUEST MODELS
class RegistrationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=150)

class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)

class PasswordResetRequest(TokenRequest):
    new_password: str = Field(min_length=8, max_length=128)

# DATABASE CONNECTION
@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

def ensure_local_api_schema(engine):
    # Create persistent rate-limit storage.

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS floodwatch_auth_rate_limits (
                bucket_hash TEXT PRIMARY KEY,
                window_started_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW(),
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_floodwatch_auth_rate_limits_window
            ON floodwatch_auth_rate_limits(window_started_at)
        """))

    ensure_resident_verification_schema(engine)

# SHARED HELPERS
def _reply(message, status_code=200):
    return JSONResponse(status_code=status_code, content={"message": message}, headers={"Cache-Control": "no-store"})

def _valid_email(email):
    try:
        return normalize_email(email)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid email address.") from exc

def _email_delivery_available():
    #Return whether transactional email is configured.
    try:
        EmailSettings.from_environment()
        return True

    except EmailConfigurationError:
        return False

def _require_email_delivery():
    if not _email_delivery_available():
        raise HTTPException(status_code=503, detail="Email service is not configured.")

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

def _throttle(request, action, email=None, ip_limit=12, email_limit=3):
    #Persistent IP and account-based request throttling.
    settings = get_auth_settings()
    secret = settings.oauth_session_secret.encode("utf-8")

    if not secret:
        raise RuntimeError("OAuth session secret is required.")

    client_ip = (request.client.host if request.client else "unknown")
    targets = [(f"{action}:ip", client_ip, ip_limit)]

    if email:
        targets.append((f"{action}:email", email, email_limit))

    allowed = True

    with _engine().begin() as conn:
        for scope, value, maximum in targets:
            bucket = hmac.new(secret, f"{scope}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()

            if not _increment_bucket(conn, bucket, maximum):
                allowed = False

    if not allowed:
        raise HTTPException(status_code=429, detail=("Too many requests. Please try again later."), headers={"Retry-After": "3600"})

def _attempt_delivery(delivery_function, *args):
    try:
        delivery_function(*args)

    except (EmailDeliveryError, EmailConfigurationError):
        logger.warning("FloodWatch transactional email delivery failed.")

# AUTOMATIC REGISTRATION OTP DELIVERY
def _send_registration_otp(email, code):
    #Send a Resident registration OTP after the HTTP registration response has been prepared.
    #Email delivery failures must not reverse a successfully created Resident account.
    
    try:
        send_resident_verification_otp(email, code)

    except (EmailConfigurationError, EmailDeliveryError) as exc:
        logger.warning("Automatic Resident verification email could not be delivered: %s", type(exc).__name__,)

# SHARED PASSWORD SESSION CREATION
def _create_password_session(conn, user_id, lifetime):
    #Create a database-backed authentication session.
    #Called after successful password login or new Resident registration.
    #MFA is deliberately not marked as verified.
   
    session_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=lifetime))

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
        "user_id": str(user_id),
        "expires_at": expires_at,
    })

    return session_token

def _authenticated_response(*, session_token, role, status_code=200, message=None):
    #Build the authenticated JSON response.
    #Google OAuth's existing post-login logic determines the correct destination using the database role.
 
    settings = get_auth_settings()
    destination = _post_login_destination(session_token)
    content = {"authenticated": True, "role": role, "next": destination}

    if message:
        content["message"] = message

    response = JSONResponse(status_code=status_code, content=content, headers={"Cache-Control": "no-store", "Pragma": "no-cache",})
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_token,
        max_age=settings.user_session_max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite=settings.cookie_samesite,
        path="/",
    )

    return response

# LOCAL EMAIL/PASSWORD LOGIN
@router.post("/login")
def login(request: Request, data: LoginRequest):
    #Authenticate an existing local account.
    #Residents active accounts can sign in without email verification during the grace period.
    #Agency/Admin must be active and email verified. Existing MFA routing remains mandatory.
    #The 48-hour Resident verification deadline is enforced separately by authorization middleware.

    try:
        email = normalize_email(data.email)

    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    _throttle(request, "login", email, ip_limit=30, email_limit=10)
    settings = get_auth_settings()

    with _engine().begin() as conn:
        user = verify_password_first_factor(conn, email=email, password=data.password)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        # Read the authoritative role and status.
        account = conn.execute(text("""
            SELECT
                id,
                role,
                email_verified
            FROM floodwatch_users
            WHERE id = CAST(:user_id AS UUID)
              AND account_status = 'active'
              AND password_hash IS NOT NULL
              AND (
                    role = 'resident'
                    OR (
                        role IN ('agency', 'admin')
                        AND email_verified = TRUE
                    )
              )
            LIMIT 1
        """), {
            "user_id": str(user["id"]),
        }).mappings().one_or_none()

        if account is None:
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        session_token = _create_password_session(conn, account["id"], settings.user_session_max_age)
        role = account["role"]

    # The transaction has committed before the destination and cookie are created.
    return _authenticated_response(session_token=session_token, role=role)

# RESIDENT REGISTRATION
@router.post("/register", status_code=202)
def register(request: Request, data: RegistrationRequest, background_tasks: BackgroundTasks):
    #Create an immediately active Resident account.
    #- Password is securely hashed.
    #- Role is always Resident.
    #- Email remains unverified initially.
    #- A session is created immediately.
    #- The 48-hour verification clock starts.
    #- An OTP is generated when email is configured.
    #- OTP delivery runs after the HTTP response.
    #Email delivery is not required for basic Resident registration and dashboard access.
    email = _valid_email(data.email)

    # Validate password
    try:
        validate_password(data.password, email)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    
    # Validate display name
    display_name = data.display_name.strip()

    if (not display_name or len(display_name) > 150 or any(ord(char) < 32 for char in display_name)):
        raise HTTPException(status_code=422, detail="Invalid display name.")
    
    # Registration rate limiting
    _throttle(request, "register", email, ip_limit=8, email_limit=3)
    settings = get_auth_settings()
    email_available = _email_delivery_available()
    session_token = None
    otp_delivery = None

    # Create account and session atomically
    try:
        with _engine().begin() as conn:
            user = register_local_user(conn, email=email, password=data.password, display_name=display_name)

            # Record the 48-hour verification window.
            # The timestamp cannot be reset by logging in.
            start_resident_grace_period(conn, user["id"])

            # Create a first-factor authenticated session.
            session_token = _create_password_session(conn, user["id"], settings.user_session_max_age)

            # Issue a six-digit OTP only if email delivery has been configured.
            if email_available:
                otp_delivery = issue_resident_email_otp(conn, user["id"])

    except ValueError:
        # Avoid exposing whether an email already exists.
        return _reply(REQUEST_ACCEPTED, 202)

    except IntegrityError as exc:
        sqlstate = (getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None))

        if sqlstate != "23505":
            raise

        return _reply(REQUEST_ACCEPTED, 202)

    # Schedule OTP delivery after successful registration
    if otp_delivery is not None:
        background_tasks.add_task(_send_registration_otp, otp_delivery["email"], otp_delivery["code"])

    # Return the authenticated Resident session
    message = (REGISTRATION_SUCCESS if email_available else REGISTRATION_PENDING)

    return _authenticated_response(session_token=session_token, role="resident", status_code=202, message=message)

# RESEND EMAIL VERIFICATION
@router.post("/resend-verification", status_code=202)
def resend_verification(request: Request, data: EmailRequest):
    #Resend verification instructions.
    #Until the OTP module is implemented, this uses the existing email-token service.
    _require_email_delivery()
    email = _valid_email(data.email)
    _throttle(request, "resend-verification", email, ip_limit=10, email_limit=3)
    token = None

    with _engine().begin() as conn:
        user_id = conn.execute(text("""
            SELECT id
            FROM floodwatch_users
            WHERE LOWER(BTRIM(email)) = :email
              AND google_sub IS NULL
              AND password_hash IS NOT NULL
              AND email_verified = FALSE
              AND account_status = 'active'
              AND role = 'resident'
            LIMIT 1
        """), {
            "email": email,
        }).scalar_one_or_none()

        if user_id:
            token = issue_email_verification(conn, user_id)

    if token:
        _attempt_delivery(send_verification_email, email, token)

    return _reply(REQUEST_ACCEPTED, 202)

# VERIFY EMAIL
@router.post("/verify-email")
def verify_email(request: Request, data: TokenRequest):
    #Redeem a valid email verification token.
    _throttle(request, "verify-email", ip_limit=20)

    with _engine().begin() as conn:
        verified = redeem_email_verification(conn, data.token)

    if not verified:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link.")

    return _reply("Email verified successfully. Your account now has full verification status.")

# REQUEST PASSWORD RESET
@router.post("/request-password-reset", status_code=202)
def request_password_reset(request: Request, data: EmailRequest):
    #Request password reset instructions.
    #Email delivery is required for password recovery.
    _require_email_delivery()
    email = _valid_email(data.email)
    _throttle(request, "request-password-reset", email, ip_limit=10, email_limit=3)

    with _engine().begin() as conn:
        token = issue_password_reset(conn, email)

    if token:
        _attempt_delivery(send_password_reset_email, email, token)

    return _reply(REQUEST_ACCEPTED, 202)

# COMPLETE PASSWORD RESET
@router.post("/reset-password")
def reset_password(request: Request, data: PasswordResetRequest):
    #Redeem a valid password-reset token.
    #New passwords follow the 8-character policy.
    try:
        validate_password(data.new_password)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _throttle(request, "reset-password", ip_limit=12)
    token_hash = hashlib.sha256(data.token.encode("utf-8")).hexdigest()
    recipient = None

    with _engine().begin() as conn:
        updated = redeem_password_reset(conn, data.token, data.new_password)

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
        raise HTTPException(status_code=400, detail="Invalid or expired password reset link.")

    if recipient:
        _attempt_delivery(send_password_change_notice, recipient)

    return _reply("Password changed successfully. Please sign in again.")
