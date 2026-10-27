#Server-side MFA authorization decisions for FloodWatch sessions.
import hashlib
import re
from dataclasses import dataclass
from sqlalchemy import text

from src.auth.google_oauth import SESSION_COOKIE

PRIVILEGED_ROLES = frozenset({"agency", "admin"})
VALID_ROLES = frozenset({"resident", "agency", "admin"})

@dataclass(frozen=True)
class MfaSessionState:
    allowed: bool
    reason: str
    mfa_required: bool
    setup_required: bool

def _state(allowed=False, reason="invalid_session", mfa_required=False, setup_required=False):
    return MfaSessionState(allowed=allowed, reason=reason, mfa_required=mfa_required, setup_required=setup_required)

def evaluate_session_mfa(conn, *, user_id, session_token):
    #Evaluate one database-backed session.
    # A valid first-factor session is not automatically MFA-verified.
    # This service does not create or elevate sessions.
    if (not isinstance(session_token, str) or not re.fullmatch( r"[A-Za-z0-9_-]{32,256}", session_token)):
        return _state()

    digest = hashlib.sha256(session_token.encode("ascii")).hexdigest()

    row = conn.execute(text("""
        SELECT
            u.role,
            u.account_status,
            u.email_verified,

            s.created_at AS session_created_at,
            s.mfa_verified_at,
            s.auth_method,

            f.status AS factor_status,
            f.activated_at AS factor_activated_at,

            NOW() AS database_now

        FROM floodwatch_user_sessions s

        JOIN floodwatch_users u
          ON u.id = s.user_id

        LEFT JOIN floodwatch_mfa_factors f
          ON f.user_id = u.id

        WHERE s.token_hash = :digest
          AND s.user_id = CAST(:user_id AS UUID)
          AND s.revoked_at IS NULL
          AND s.expires_at > NOW()

        LIMIT 1
    """), {
        "digest": digest,
        "user_id": str(user_id),
    }).mappings().first()

    if row is None:
        return _state()

    if (row["account_status"] != "active" or not row["email_verified"] or row["role"] not in VALID_ROLES):
        return _state(reason="inactive_account")

    role_requires_mfa = (row["role"] in PRIVILEGED_ROLES)
    factor_active = (row["factor_status"] == "active" and row["factor_activated_at"] is not None)
    requires_mfa = role_requires_mfa or factor_active

    if not requires_mfa:
        return _state(allowed=True, reason="authenticated")

    if not factor_active:
        return _state(reason="enrollment_required", mfa_required=True, setup_required=True)

    verified_at = row["mfa_verified_at"]
    created_at = row["session_created_at"]
    activated_at = row["factor_activated_at"]

    if (verified_at is None or created_at is None or verified_at < created_at or verified_at < activated_at or verified_at > row["database_now"]):
        return _state(reason="challenge_required", mfa_required=True)

    return _state(allowed=True, reason="mfa_verified", mfa_required=True)

def evaluate_request_mfa(conn, request, user):
    #Evaluate the session cookie for a server-resolved user.
    token = request.cookies.get(SESSION_COOKIE)

    return evaluate_session_mfa(conn, user_id=user["id"], session_token=token)
