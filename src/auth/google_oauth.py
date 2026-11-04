# Google OpenID Connect authentication for Nairobi FloodWatch.
import logging
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from uuid import uuid4
import httpx
from authlib.integrations.starlette_client import OAuth, OAuthError
from authlib.jose.errors import JoseError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from src.auth.account_service import upsert_verified_google_user, link_verified_google_user
from src.auth.config import get_auth_settings
from src.operational.persistence import create_database_engine


router = APIRouter(include_in_schema=False)
logger = logging.getLogger("floodwatch.auth.google")

SESSION_COOKIE = "floodwatch_user_session"
GOOGLE_DISCOVERY_URL = ("https://accounts.google.com/.well-known/openid-configuration")

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

@lru_cache(maxsize=1)
def _google_client():
    settings = get_auth_settings().require_google_configuration()
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )

    return oauth.google

def ensure_user_session_schema(engine):
    # Create persistent application sessions in PostgreSQL.
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS floodwatch_user_sessions (
                token_hash TEXT PRIMARY KEY,

                user_id UUID NOT NULL
                    REFERENCES floodwatch_users(id)
                    ON DELETE CASCADE,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_floodwatch_user_sessions_user
            ON floodwatch_user_sessions(user_id)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_floodwatch_user_sessions_expiry
            ON floodwatch_user_sessions(expires_at)
        """))

def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _validate_google_identity(claims, client_id):
    # Accept only identity claims from Authlib's verified ID token.

    if not claims:
        raise ValueError("Missing verified Google identity.")

    if claims.get("iss") not in {"https://accounts.google.com", "accounts.google.com"}:
        raise ValueError("Invalid Google token issuer.")

    if claims.get("aud") != client_id:
        raise ValueError("Invalid Google token audience.")

    if claims.get("azp") not in (None, client_id):
        raise ValueError("Invalid authorized presenter.")

    if claims.get("email_verified") is not True:
        raise PermissionError("Google email verification is required.")

    google_sub = claims.get("sub")
    email = claims.get("email")

    if not isinstance(google_sub, str) or not google_sub.strip():
        raise ValueError("Missing Google account identifier.")

    if not isinstance(email, str) or not email.strip():
        raise ValueError("Missing verified email address.")

    return {
        "google_sub": google_sub,
        "email": email,
        "email_verified": True,
        "display_name": claims.get("name") or "",
        "avatar_url": claims.get("picture"),
    }

def _create_user_session(identity, lifetime):
    #Save the account and issue a session in one transaction.
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=lifetime)

    with _engine().begin() as conn:
        user = upsert_verified_google_user(conn, **identity,)
        user_id = str(user["id"])

        conn.execute(text("""
            INSERT INTO floodwatch_user_sessions (
                token_hash, user_id, expires_at, auth_method, mfa_verified_at
            )
            VALUES (
                :token_hash,
                CAST(:user_id AS UUID),
                :expires_at,
                'google',
                NULL
            )
        """), {
            "token_hash": _token_hash(token),
            "user_id":str(user["id"]),
            "expires_at": expires_at,
        })

        conn.execute(text("""
            INSERT INTO floodwatch_auth_audit (
                id, actor_user_id, target_user_id,
                action, details
            )
            VALUES (
                CAST(:id AS UUID),
                CAST(:user_id AS UUID),
                CAST(:user_id AS UUID),
                'google_login_succeeded',
                '{}'::jsonb
            )
        """), {
            "id": str(uuid4()),
            "user_id": user_id,
        })

    return token


def _find_session_user(token):
    #Find the authenticated user associated with a valid session.
    #Resident accounts must be active and email verification is optional.
    #Agency and Admin accounts must be active and must have a verified email address.
    #MFA authorization remains enforced separately.

    if not token or len(token) > 256:
        return None

    with _engine().connect() as conn:
        row = conn.execute(text("""
            SELECT
                u.id,
                u.email,
                u.display_name,
                u.role,
                u.account_status,
                u.email_verified

            FROM floodwatch_user_sessions s

            JOIN floodwatch_users u
                ON u.id = s.user_id

            WHERE s.token_hash = :token_hash

              AND s.expires_at > NOW()

              AND s.revoked_at IS NULL

              AND u.account_status = 'active'

              AND (
                    u.role = 'resident'

                    OR (
                        u.role IN ('agency', 'admin')
                        AND u.email_verified = TRUE
                    )
              )

            LIMIT 1
        """), {
            "token_hash": _token_hash(token),
        }).mappings().first()

    if row is None:
        return None

    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "account_status": row["account_status"],
        "email_verified": bool(row["email_verified"]),
    }


def _post_login_destination(session_token: str) -> str:
    #Determine the correct post-login destination.
    #Residents can access the Resident dashboard without email verification and must complete MFA if already enrolled.
    #Agency and Admin require an active account, require verified email ownership, must pass through MFA.
    #The account's database role determines its access.
    
    session_hash = _token_hash(session_token)

    with _engine().connect() as conn:
        account = conn.execute(text("""
            SELECT
                u.role,
                u.account_status,
                u.email_verified,
                f.status AS mfa_status

            FROM floodwatch_user_sessions s

            JOIN floodwatch_users u
                ON u.id = s.user_id

            LEFT JOIN floodwatch_mfa_factors f
                ON f.user_id = u.id

            WHERE s.token_hash = :session_hash

              AND s.revoked_at IS NULL

              AND s.expires_at > NOW()

            LIMIT 1
        """), {
            "session_hash": session_hash,
        }).mappings().first()

    if account is None:
        raise RuntimeError("Cannot determine the destination for an invalid session.")

    # No inactive or suspended account may sign in.
    if account["account_status"] != "active":
        raise RuntimeError("Only active accounts may complete sign-in.")

    role = account["role"]
    email_verified = bool(account["email_verified"])
    mfa_enabled = (account["mfa_status"] == "active")

    # Resident accounts
    if role == "resident":

        # Respect MFA when the Resident has enabled it.
        if mfa_enabled:
            return "/static/auth/mfa.html"

        # Email verification is not required for basic Resident dashboard access.
        return "/resident"

    # Agency and Admin accounts
    if role in {"agency", "admin"}:

        if not email_verified:
            raise RuntimeError("Privileged accounts require verified email ownership.")

        # Privileged roles must complete MFA.
        return "/static/auth/mfa.html"

    raise RuntimeError("Unsupported FloodWatch account role.")

def get_signed_in_user(request: Request):
    #Read a valid, active user session.
    token = request.cookies.get(SESSION_COOKIE)

    return _find_session_user(token)

def require_signed_in_user(request: Request):
    #Dependency for future authenticated API endpoints.
    user = get_signed_in_user(request)

    if not user:
        raise HTTPException(status_code=401, detail="Authentication is required.")

    return user

def _revoke_user_session(token):
    if not token:
        return

    with _engine().begin() as conn:
        user_id = conn.execute(text("""
            UPDATE floodwatch_user_sessions
            SET revoked_at = NOW()
            WHERE token_hash = :token_hash
              AND revoked_at IS NULL
            RETURNING user_id
        """), {
            "token_hash": _token_hash(token),
        }).scalar_one_or_none()

        if user_id:
            conn.execute(text("""
                INSERT INTO floodwatch_auth_audit (
                    id, actor_user_id, target_user_id,
                    action, details
                )
                VALUES (
                    CAST(:id AS UUID),
                    CAST(:user_id AS UUID),
                    CAST(:user_id AS UUID),
                    'user_logged_out',
                    '{}'::jsonb
                )
            """), {
                "id": str(uuid4()),
                "user_id": str(user_id),
            })

def _verified_link_user(request: Request):
    from src.auth.mfa_session import evaluate_session_mfa

    user = get_signed_in_user(request)
    session_token = request.cookies.get(SESSION_COOKIE)

    if not user or not session_token:
        raise PermissionError("Authentication is required.")

    if user["role"] not in {"agency", "admin"}:
        raise PermissionError("Privileged account required.")

    with _engine().connect() as conn:
        mfa = evaluate_session_mfa(
            conn,
            user_id=user["id"],
            session_token=session_token,
        )

    if not mfa.allowed or not mfa.mfa_required:
        raise PermissionError("MFA verification is required.")

    return user, _token_hash(session_token)


def _link_google_identity(user_id, identity):
    with _engine().begin() as conn:
        return link_verified_google_user(
            conn,
            user_id=user_id,
            google_sub=identity["google_sub"],
            email=identity["email"],
            email_verified=identity["email_verified"],
        )


@router.get("/auth/google/link")
async def google_link(request: Request):
    settings = get_auth_settings().require_google_configuration()

    try:
        user, session_hash = _verified_link_user(request)
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail="Sign in and complete MFA before connecting Google.",
        ) from exc

    request.session.clear()
    request.session["google_link_user_id"] = str(user["id"])
    request.session["google_link_session_hash"] = session_hash

    return await _google_client().authorize_redirect(
        request,
        settings.google_redirect_uri,
        prompt="select_account",
    )

@router.get("/auth/google/login")
async def google_login(request: Request):
    settings = get_auth_settings().require_google_configuration()
    request.session.clear()

    return await _google_client().authorize_redirect(request, settings.google_redirect_uri)


@router.get("/auth/google/callback")
async def google_callback(request: Request):
    settings = get_auth_settings().require_google_configuration()

    
    link_user_id = request.session.get("google_link_user_id")
    link_session_hash = request.session.get("google_link_session_hash")

    
    logger.warning(
        "Google callback context: "
        "link_intent_present=%s, "
        "link_session_hash_present=%s, "
        "user_session_cookie_present=%s",
        bool(link_user_id),
        bool(link_session_hash),
        SESSION_COOKIE in request.cookies,
    )


    if bool(link_user_id) != bool(link_session_hash):
        request.session.clear()
        raise HTTPException(
            status_code=401,
            detail="Invalid Google linking session.",
        )


    # Capture safe diagnostic indicators BEFORE Authlib consumes or clears the stored OAuth state.
    # Never log actual state values, authorization codes, session cookies, access tokens or ID tokens.
    
    callback_state_present = bool(request.query_params.get("state"))
    oauth_cookie_present = ("floodwatch_oauth_flow" in request.cookies)
    saved_oauth_state_present = any(isinstance(key, str) and key.startswith("_state_google_") for key in request.session)

    # GOOGLE TOKEN EXCHANGE AND OIDC VERIFICATION
    try:
        token = await _google_client().authorize_access_token(request)

    except (OAuthError, JoseError, ValueError) as exc:
        error_code = getattr(exc, "error", None)

        if error_code not in {"mismatching_state", "access_denied", "invalid_request", "invalid_grant", "invalid_client"}:
            error_code = "other"

        logger.warning(
            "Google OAuth callback failed: "
            "exception=%s, "
            "error_code=%s, "
            "callback_state_present=%s, "
            "flow_cookie_present=%s, "
            "saved_oauth_state_present=%s",
            type(exc).__name__,
            error_code,
            callback_state_present,
            oauth_cookie_present,
            saved_oauth_state_present,
        )
        request.session.clear()
        raise HTTPException(status_code=401, detail="Google authentication could not be verified.") from exc

    except httpx.HTTPError as exc:
        logger.warning("Google OAuth HTTP error: exception=%s", type(exc).__name__)
        request.session.clear()
        raise HTTPException(status_code=502, detail="Unable to communicate with Google authentication.") from exc

    # Google OAuth state is no longer required.
    request.session.clear()

    # REQUIRE VERIFIED GOOGLE IDENTITY
    if not token.get("id_token") or not token.get("userinfo"):
        raise HTTPException(status_code=401, detail="Google did not return a verified identity token.")
    
    
    # DATABASE IDENTITY, LINKING AND SESSION
    try:
        identity = _validate_google_identity(
            token["userinfo"],
            settings.google_client_id,
        )

        if link_user_id is not None:
            user, current_session_hash = _verified_link_user(request)

            if (
                str(user["id"]) != str(link_user_id)
                or not isinstance(link_session_hash, str)
                or not secrets.compare_digest(
                    current_session_hash,
                    link_session_hash,
                )
            ):
                raise PermissionError(
                    "The original authenticated session is required."
                )

            linked = await run_in_threadpool(
                _link_google_identity,
                str(user["id"]),
                identity,
            )

            destination = (
                "/admin"
                if linked["role"] == "admin"
                else "/agency"
            )

            response = RedirectResponse(
                url=destination,
                status_code=303,
            )
            response.headers["Cache-Control"] = "no-store"
            return response

        session_token = await run_in_threadpool(
            _create_user_session,
            identity,
            settings.user_session_max_age,
        )

    except (ValueError, PermissionError) as exc:   
        logger.warning(
            "Google identity rejected: flow=%s, reason=%s",
            "link" if link_user_id else "normal_login",
            str(exc),
        )

        raise HTTPException(
            status_code=403,
            detail="This Google identity cannot access this account.",
        ) from exc

    except IntegrityError as exc:
        logger.warning(
            "Google account linking encountered an identity conflict."
        )
        raise HTTPException(
            status_code=409,
            detail="Google account linking could not be completed.",
        ) from exc

    
    # ROLE-AWARE REDIRECTION
    # The database determines whether this account is Resident, Agency or Admin.
    # Agency/Admin must still complete MFA.
    destination = _post_login_destination(session_token)
    response = RedirectResponse(url=destination, status_code=303)
    response.set_cookie(key=SESSION_COOKIE, value=session_token, max_age=settings.user_session_max_age, path="/", secure=settings.secure_cookies, httponly=True, samesite=settings.cookie_samesite)
    response.headers["Cache-Control"] = "no-store"

    return response

@router.get("/auth/session")
def auth_session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    user = get_signed_in_user(request)

    return {
        "authenticated": user is not None,
        "user": user,
    }

@router.post("/auth/logout")
async def logout(request: Request):
    settings = get_auth_settings()
    origin = request.headers.get("origin")

    if origin is not None and origin != settings.base_url:
        raise HTTPException(status_code=403, detail="Invalid request origin.")

    session_token = request.cookies.get(SESSION_COOKIE)

    if session_token:
        await run_in_threadpool(_revoke_user_session, session_token)

    request.session.clear()
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE, path="/", secure=settings.secure_cookies, httponly=True, samesite=settings.cookie_samesite)
    response.headers["Cache-Control"] = "no-store"

    return response
