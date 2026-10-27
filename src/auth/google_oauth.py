# Google OpenID Connect authentication for Nairobi FloodWatch.
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
from starlette.concurrency import run_in_threadpool

from src.auth.account_service import upsert_verified_google_user
from src.auth.config import get_auth_settings
from src.operational.persistence import create_database_engine


router = APIRouter(include_in_schema=False)
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
        client_kwargs={
            "scope": "openid email profile",
            "code_challenge_method": "S256",
        },
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
    if not token or len(token) > 256:
        return None

    with _engine().connect() as conn:
        row = conn.execute(text("""
            SELECT
                u.id,
                u.email,
                u.display_name,
                u.role,
                u.account_status
            FROM floodwatch_user_sessions s
            JOIN floodwatch_users u
                ON u.id = s.user_id
            WHERE s.token_hash = :token_hash
              AND s.expires_at > NOW()
              AND s.revoked_at IS NULL
              AND u.account_status = 'active'
              AND u.email_verified = TRUE
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
    }

def _post_login_destination(session_token: str) -> str:
    """Choose the correct landing page using the database account."""

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
        raise RuntimeError(
            "Cannot determine the destination for an invalid session."
        )

    if (
        account["account_status"] != "active"
        or not account["email_verified"]
    ):
        raise RuntimeError(
            "Only active, verified accounts may complete sign-in."
        )

    role = account["role"]
    mfa_enabled = account["mfa_status"] == "active"

    if role in {"admin", "agency"} or mfa_enabled:
        return "/static/auth/mfa.html"

    if role == "resident":
        return "/resident"

    raise RuntimeError("Unsupported FloodWatch account role.")

def get_signed_in_user(request: Request):
    # Read a valid, active user session.
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

@router.get("/auth/google/login")
async def google_login(request: Request):
    settings = get_auth_settings().require_google_configuration()
    request.session.clear()

    return await _google_client().authorize_redirect(request, settings.google_redirect_uri)

@router.get("/auth/google/callback")
async def google_callback(request: Request):
    settings = get_auth_settings().require_google_configuration()

    try:
        token = await _google_client().authorize_access_token(request)

    except (OAuthError, JoseError, ValueError) as exc:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Google authentication could not be verified.") from exc

    except httpx.HTTPError as exc:
        request.session.clear()
        raise HTTPException(status_code=502, detail="Unable to communicate with Google authentication.") from exc

    request.session.clear()

    if not token.get("id_token") or not token.get("userinfo"):
        raise HTTPException(status_code=401, detail="Google did not return a verified identity token.")

    try:
        identity = _validate_google_identity(token["userinfo"], settings.google_client_id)
        session_token = await run_in_threadpool(_create_user_session, identity, settings.user_session_max_age)

    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail="This Google account cannot sign in to FloodWatch.") from exc

    response = RedirectResponse(url=_post_login_destination(session_token), status_code=303)
    response.set_cookie( key=SESSION_COOKIE, value=session_token, max_age=settings.user_session_max_age, path="/", secure=settings.secure_cookies, httponly=True, samesite=settings.cookie_samesite)
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
