#Session-bound MFA login challenges for Nairobi FloodWatch.
import hashlib
import re
import secrets
from functools import lru_cache
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.auth.config import get_auth_settings
from src.auth.google_oauth import (SESSION_COOKIE, require_signed_in_user)
from src.auth.mfa_service import (use_recovery_code, verify_totp_code)
from src.operational.persistence import create_database_engine

MAX_CHALLENGES = 5
MAX_ATTEMPTS = 5
PRIVILEGED_ROLES = frozenset({"agency", "admin"})

router = APIRouter(prefix="/auth/mfa/challenge", include_in_schema=False)

class CompleteChallengeRequest(BaseModel):
    challenge_token: str = Field(min_length=40, max_length=128)
    code: str | None = None
    recovery_code: str | None = None

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

def ensure_challenge_binding_schema(engine):
    #Bind new challenges to sessions and reject old unbound tokens.

    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE floodwatch_mfa_challenges
            ADD COLUMN IF NOT EXISTS session_token_hash TEXT
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_floodwatch_mfa_challenge_session
            ON floodwatch_mfa_challenges(session_token_hash)
        """))

        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid =
                        'floodwatch_mfa_challenges'::regclass
                      AND conname =
                        'floodwatch_mfa_challenge_session_bound'
                ) THEN
                    ALTER TABLE floodwatch_mfa_challenges
                    ADD CONSTRAINT
                        floodwatch_mfa_challenge_session_bound
                    CHECK (
                        session_token_hash IS NOT NULL
                        AND LENGTH(session_token_hash) = 64
                    ) NOT VALID;
                END IF;
            END $$
        """))


def _session_digest(token):
    if not isinstance(token, str):
        return None

    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        return None

    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _lock_user_and_session(conn, user_id, session_token):
    digest = _session_digest(session_token)

    if digest is None:
        return None

    user = conn.execute(text("""
        SELECT id, role, account_status, email_verified
        FROM floodwatch_users
        WHERE id = CAST(:user_id AS UUID)
        FOR UPDATE
    """), {"user_id": str(user_id)}).mappings().first()

    if (not user or user["account_status"] != "active" or not user["email_verified"]):
        return None

    session = conn.execute(text("""
        SELECT
            auth_method,
            mfa_verified_at,
            created_at
        FROM floodwatch_user_sessions
        WHERE user_id = CAST(:user_id AS UUID)
          AND token_hash = :digest
          AND revoked_at IS NULL
          AND expires_at > NOW()
        FOR UPDATE
    """), {
        "user_id": str(user_id),
        "digest": digest,
    }).mappings().first()

    if session is None:
        return None

    return user, session, digest

def start_login_challenge(conn, *, user_id, session_token):
    locked = _lock_user_and_session(conn, user_id, session_token)

    if locked is None:
        return {"status": "invalid_session"}

    user, session, session_hash = locked

    if session["auth_method"] not in ("google", "password"):
        return {"status": "session_method_missing"}

    factor = conn.execute(text("""
        SELECT
            status,
            activated_at,
            locked_until
        FROM floodwatch_mfa_factors
        WHERE user_id = CAST(:user_id AS UUID)
        FOR UPDATE
    """), {"user_id": str(user_id)}).mappings().first()

    active = bool(factor and factor["status"] == "active")

    required = (user["role"] in PRIVILEGED_ROLES or active)

    if not required:
        return {"status": "not_required"}

    if not active:
        return {"status": "enrollment_required"}

    verified = (session["mfa_verified_at"] is not None and session["mfa_verified_at"] >= session["created_at"] and session["mfa_verified_at"] >= factor["activated_at"])

    if verified:
        return {"status": "already_verified"}

    locked_out = conn.execute(text("""
        SELECT locked_until > NOW()
        FROM floodwatch_mfa_factors
        WHERE user_id = CAST(:user_id AS UUID)
    """), {"user_id": str(user_id)}).scalar_one()

    if locked_out:
        return {"status": "locked"}

    recent = conn.execute(text("""
        SELECT COUNT(*)
        FROM floodwatch_mfa_challenges
        WHERE user_id = CAST(:user_id AS UUID)
          AND created_at > NOW() - INTERVAL '10 minutes'
    """), {"user_id": str(user_id)}).scalar_one()

    if recent >= MAX_CHALLENGES:
        return {"status": "rate_limited"}

    conn.execute(text("""
        UPDATE floodwatch_mfa_challenges
        SET revoked_at = NOW()
        WHERE user_id = CAST(:user_id AS UUID)
          AND session_token_hash = :session_hash
          AND used_at IS NULL
          AND revoked_at IS NULL
    """), {
        "user_id": str(user_id),
        "session_hash": session_hash,
    })

    raw_token = secrets.token_urlsafe(32)

    conn.execute(text("""
        INSERT INTO floodwatch_mfa_challenges (
            token_hash,
            user_id,
            session_token_hash,
            purpose,
            auth_method,
            expires_at
        )
        VALUES (
            :challenge_hash,
            CAST(:user_id AS UUID),
            :session_hash,
            'login',
            :auth_method,
            NOW() + INTERVAL '5 minutes'
        )
    """), {
        "challenge_hash": hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
        "user_id": str(user_id),
        "session_hash": session_hash,
        "auth_method": session["auth_method"],
    })

    return {
        "status": "challenge_required",
        "challenge_token": raw_token,
        "expires_in_seconds": 300,
    }

def complete_login_challenge(conn, *, user_id, session_token, challenge_token, code=None, recovery_code=None):
    if (code is None) == (recovery_code is None):
        return "invalid_input"

    if not isinstance(challenge_token, str):
        return "invalid_challenge"

    if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", challenge_token):
        return "invalid_challenge"

    locked = _lock_user_and_session(conn, user_id, session_token)

    if locked is None:
        return "invalid_session"

    _, session, session_hash = locked

    if session["auth_method"] not in ("google", "password"):
        return "invalid_session"

    challenge_hash = hashlib.sha256(challenge_token.encode("ascii")).hexdigest()

    challenge = conn.execute(text("""
        SELECT token_hash, failed_attempts
        FROM floodwatch_mfa_challenges
        WHERE token_hash = :challenge_hash
          AND user_id = CAST(:user_id AS UUID)
          AND session_token_hash = :session_hash
          AND auth_method = :auth_method
          AND purpose = 'login'
          AND used_at IS NULL
          AND revoked_at IS NULL
          AND expires_at > NOW()
        FOR UPDATE
    """), {
        "challenge_hash": challenge_hash,
        "user_id": str(user_id),
        "session_hash": session_hash,
        "auth_method": session["auth_method"],
    }).mappings().first()

    if challenge is None:
        return "invalid_challenge"

    if challenge["failed_attempts"] >= MAX_ATTEMPTS:
        return "locked"

    try:
        if code is not None:
            valid = verify_totp_code(conn, user_id, code)
        else:
            valid = use_recovery_code(conn, user_id, recovery_code)
    except PermissionError:
        return "locked"

    if not valid:
        conn.execute(text("""
            UPDATE floodwatch_mfa_challenges
            SET failed_attempts = failed_attempts + 1,
                revoked_at = CASE
                    WHEN failed_attempts + 1 >= :maximum
                    THEN NOW()
                    ELSE revoked_at
                END
            WHERE token_hash = :challenge_hash
        """), {
            "challenge_hash": challenge_hash,
            "maximum": MAX_ATTEMPTS,
        })

        return "invalid_code"

    conn.execute(text("""
        UPDATE floodwatch_user_sessions
        SET mfa_verified_at = NOW()
        WHERE token_hash = :session_hash
          AND user_id = CAST(:user_id AS UUID)
          AND revoked_at IS NULL
          AND expires_at > NOW()
    """), {
        "session_hash": session_hash,
        "user_id": str(user_id),
    })

    conn.execute(text("""
        UPDATE floodwatch_mfa_challenges
        SET used_at = NOW()
        WHERE token_hash = :challenge_hash
    """), {
        "challenge_hash": challenge_hash,
    })

    conn.execute(text("""
        UPDATE floodwatch_mfa_challenges
        SET revoked_at = NOW()
        WHERE user_id = CAST(:user_id AS UUID)
          AND session_token_hash = :session_hash
          AND token_hash <> :challenge_hash
          AND used_at IS NULL
          AND revoked_at IS NULL
    """), {
        "user_id": str(user_id),
        "session_hash": session_hash,
        "challenge_hash": challenge_hash,
    })

    return "verified"

def _require_origin_json(request: Request):
    if request.headers.get("origin") != get_auth_settings().base_url:
        raise HTTPException(status_code=403, detail="Invalid request origin.")

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="JSON request body required.")

def _response(data):
    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
        })

@router.post("/start", dependencies=[Depends(_require_origin_json)])
def start_challenge(request: Request, user=Depends(require_signed_in_user)):
    with _engine().begin() as conn:
        result = start_login_challenge(conn, user_id=user["id"], session_token=request.cookies.get(SESSION_COOKIE, ""))

    status = result["status"]

    if status == "challenge_required":
        return _response(result)

    if status == "already_verified":
        return _response({"status": "already_verified"})

    if status == "enrollment_required":
        raise HTTPException(status_code=409, detail="Authenticator setup required.")

    if status in {"locked", "rate_limited"}:
        raise HTTPException(status_code=429, detail="Too many attempts.", headers={"Retry-After": "600"})

    raise HTTPException(status_code=403, detail="MFA challenge unavailable for this session.")

@router.post("/complete", dependencies=[Depends(_require_origin_json)])
def complete_challenge(request: Request, body: CompleteChallengeRequest, user=Depends(require_signed_in_user)):
    with _engine().begin() as conn:
        result = complete_login_challenge(
            conn,
            user_id=user["id"],
            session_token=request.cookies.get(SESSION_COOKIE, ""),
            challenge_token=body.challenge_token,
            code=body.code,
            recovery_code=body.recovery_code)

    if result == "verified":
        return _response({
            "status": "verified",
            "message": "MFA verification completed.",
        })

    if result == "locked":
        raise HTTPException(status_code=429, detail="Too many attempts.", headers={"Retry-After": "900"})

    if result == "invalid_input":
        raise HTTPException(status_code=422, detail="Provide a code or recovery code, not both.")

    raise HTTPException(status_code=400, detail="Invalid or expired MFA challenge or code.")
