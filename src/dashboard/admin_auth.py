# Temporary localhost-only administrator login with database-backed sessions.
import hashlib
import ipaddress
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.operational.persistence import create_database_engine

logger = logging.getLogger("floodwatch.admin_auth")
router = APIRouter(prefix="/admin/auth", tags=["Admin Authentication"], include_in_schema=False)
key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

COOKIE_NAME = "floodwatch_admin_session"
SESSION_HOURS = 8

class LoginRequest(BaseModel):
    admin_key: SecretStr

@lru_cache(maxsize=1)
def auth_engine():
    return create_database_engine()

def configured_key():
    value = os.getenv("FLOODWATCH_ADMIN_API_KEY", "")

    if len(value) < 32:
        raise HTTPException(503, "Administrator authentication is not configured.",)

    return value

def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def check_origin(request):
    origin = request.headers.get("origin")

    if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc):
        raise HTTPException(403, "Cross-origin administrator action denied.",)

    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site administrator action denied.",)

def require_local_login(request):
    if os.getenv("FLOODWATCH_ENABLE_LOCAL_ADMIN_LOGIN") != "1":
        raise HTTPException(403, "Local administrator login is disabled.",)

    try:
        local_client = ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        local_client = False

    if (not local_client or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise HTTPException(403, "Local administrator login requires loopback access.",)

    check_origin(request)

def prepare_sessions(connection):
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS floodwatch_admin_sessions (
            session_hash CHAR(64) PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ
        )
    """))

    connection.execute(text("""
        DELETE FROM floodwatch_admin_sessions
        WHERE expires_at <= NOW()
           OR revoked_at IS NOT NULL
    """))

def valid_session(token):
    if not token or len(token) > 128:
        return False

    try:
        with auth_engine().connect() as connection:
            return connection.execute(text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM floodwatch_admin_sessions
                    WHERE session_hash = :hash
                      AND expires_at > NOW()
                      AND revoked_at IS NULL
                )
            """), {
                "hash": token_hash(token)
            }).scalar_one()

    except SQLAlchemyError as exc:
        logger.exception("Could not verify administrator session")
        raise HTTPException(503, "Administrator session verification unavailable.",) from exc

def require_admin(request: Request, api_key: str | None = Depends(key_header),):
    expected = configured_key()

    # Preserve API-key authentication for backend testing.
    if api_key and secrets.compare_digest(api_key, expected):
        return "admin_api_key"

    # Browser requests authenticate using a server-backed session.
    if valid_session(request.cookies.get(COOKIE_NAME)):
        return "local_admin_session"

    raise HTTPException(401, "Administrator authentication required.",)

@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response,):
    require_local_login(request)

    if not secrets.compare_digest( payload.admin_key.get_secret_value(), configured_key(),):
        logger.warning("Rejected local administrator login")
        raise HTTPException(401, "Invalid administrator credentials.",)

    token = secrets.token_urlsafe(48)

    expires_at = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS))

    try:
        with auth_engine().begin() as connection:
            prepare_sessions(connection)

            connection.execute(text("""
                INSERT INTO floodwatch_admin_sessions
                    (session_hash, expires_at)
                VALUES
                    (:hash, :expires_at)
            """), {
                "hash": token_hash(token),
                "expires_at": expires_at,
            })

    except SQLAlchemyError as exc:
        logger.exception("Could not create administrator session")

        raise HTTPException(503,  "Administrator login is temporarily unavailable.") from exc

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )

    logger.info("Local administrator session created")

    return {
        "authenticated": True,
        "expires_at": expires_at.isoformat(),
    }

@router.get("/session")
def session_status(request: Request):
    return {
        "authenticated": valid_session(
            request.cookies.get(COOKIE_NAME)
        )
    }

@router.post("/logout")
def logout(request: Request, response: Response,):
    check_origin(request)

    token = request.cookies.get(COOKIE_NAME)

    if token:
        try:
            with auth_engine().begin() as connection:
                connection.execute(text("""
                    UPDATE floodwatch_admin_sessions
                    SET revoked_at = NOW()
                    WHERE session_hash = :hash
                """), {
                    "hash": token_hash(token)
                })

        except SQLAlchemyError as exc:
            logger.exception("Could not revoke administrator session")
            raise HTTPException(503, "Administrator logout is temporarily unavailable.",) from exc

    response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")

    return {"authenticated": False}