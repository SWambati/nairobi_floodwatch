#Role-based authorization with server-side MFA enforcement.
import os
from functools import lru_cache
from fastapi import Depends, HTTPException, Request, status

from src.auth.google_oauth import (get_signed_in_user, require_signed_in_user)
from src.auth.mfa_session import evaluate_request_mfa
from src.operational.persistence import create_database_engine

VALID_ROLES = frozenset({"resident", "agency", "admin"})
AGENCY_ROLES = frozenset({"agency", "admin"})
ADMIN_ROLES = frozenset({"admin"})

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

def _validate_roles(roles):
    allowed = frozenset(roles)

    if not allowed or not allowed.issubset(VALID_ROLES):
        raise ValueError("Invalid authorization role configuration.")

    return allowed

def _mfa_enforced():
    #Production always enforces MFA.
    #Development can explicitly enable MFA after the
    #enrollment and challenge interfaces are ready.
    environment = os.getenv("FLOODWATCH_ENV", "development").strip().lower()

    if environment != "development":
        return True

    configured = os.getenv("FLOODWATCH_MFA_ENFORCEMENT", "false").strip().lower()

    return configured in {"1", "true", "yes", "on"}

def _authorize_role(user, allowed_roles):
    if not user or user.get("account_status") != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    if user.get("role") not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to access this resource.")

    return user

def _authorize_mfa(request, user):
    if not _mfa_enforced():
        return user

    try:
        with _engine().connect() as conn:
            result = evaluate_request_mfa(conn, request, user)

    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MFA verification is temporarily unavailable.") from exc

    if result.allowed:
        return user

    if result.reason in {"invalid_session", "inactive_account"}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A valid authenticated session is required.")

    if result.setup_required:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("MFA setup is required before accessing this resource."))

    if result.reason == "challenge_required":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("Google Authenticator verification is required before accessing this resource."))

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA authorization was not completed.")

def _authorize(request, user, allowed_roles):
    _authorize_role(user, allowed_roles)
    return _authorize_mfa(request, user)

def require_roles(*roles):
    allowed = _validate_roles(roles)

    def dependency(request: Request, user=Depends(require_signed_in_user)):
        return _authorize(request, user, allowed)

    return dependency

def require_page_roles(*roles):
    allowed = _validate_roles(roles)

    def dependency(request: Request):
        user = get_signed_in_user(request)

        if user is None:
            raise HTTPException(status_code=303, headers={"Location": "/auth/google/login"})

        return _authorize(request, user, allowed)

    return dependency

require_agency_access = require_roles("agency", "admin")
require_admin_access = require_roles("admin")
require_agency_page = require_page_roles("agency", "admin")
require_admin_page = require_page_roles("admin")
