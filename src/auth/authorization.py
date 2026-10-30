# Role-based authorization, MFA and Resident verification policy.
import os
from functools import lru_cache
from fastapi import (Depends, HTTPException, Request, status)

from src.auth.google_oauth import (get_signed_in_user, require_signed_in_user)
from src.auth.mfa_session import evaluate_request_mfa
from src.auth.resident_verification import (get_resident_verification_status)
from src.operational.persistence import create_database_engine

# AUTHORIZATION ROLES
VALID_ROLES = frozenset({"resident", "agency", "admin"})
RESIDENT_ROLES = frozenset({"resident"})
AGENCY_ROLES = frozenset({"agency", "admin"})
ADMIN_ROLES = frozenset({"admin"})

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

# ROLE VALIDATION
def _validate_roles(roles):
    allowed = frozenset(roles)

    if not allowed or not allowed.issubset(VALID_ROLES):
        raise ValueError("Invalid authorization role configuration.")

    return allowed

def _authorize_role(user, allowed_roles):
    #Enforce authenticated, active accounts and database roles.
    #Agency and Admin accounts must also have a verified email address.

    if not user or user.get("account_status") != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    role = user.get("role")

    if role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("You do not have permission to access this resource."))

    # Defense in depth for privileged accounts.
    if role in AGENCY_ROLES and not user.get("email_verified"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("Verified email ownership is required for privileged accounts."))

    return user

# MFA ENFORCEMENT
def _mfa_enforced():
    #Production always enforces MFA.
    #Development enforcement is controlled using FLOODWATCH_MFA_ENFORCEMENT.
    environment = os.getenv("FLOODWATCH_ENV", "development").strip().lower()

    if environment != "development":
        return True

    configured = os.getenv("FLOODWATCH_MFA_ENFORCEMENT", "false").strip().lower()

    return configured in {"1", "true", "yes", "on"}

def _authorize_mfa(request, user):
    if not _mfa_enforced():
        return user

    try:
        with _engine().connect() as conn:
            result = evaluate_request_mfa(conn, request, user)

    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=("MFA verification is temporarily unavailable.")) from exc

    if result.allowed:
        return user

    if result.reason in {"invalid_session", "inactive_account"}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=("A valid authenticated session is required."))

    if result.setup_required:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("MFA setup is required before accessing this resource."))

    if result.reason == "challenge_required":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("Google Authenticator verification is required before accessing this resource."))

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA authorization was not completed.")

def _authorize(request, user, allowed_roles):
    _authorize_role(user, allowed_roles)
    return _authorize_mfa(request, user)

# EXISTING ROLE-BASED DEPENDENCIES
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
            raise HTTPException(status_code=303, headers={"Location": "/static/auth/login.html"})

        return _authorize(request, user, allowed)

    return dependency

# RESIDENT VERIFICATION ENFORCEMENT SWITCH
def _resident_deadline_enforced():
    #Determine whether the 48-hour deadline is enforced.
    #Leave this disabled until email verification delivery and recovery have been tested.
    #A missing configuration never enables lockout.
    configured = os.getenv("FLOODWATCH_RESIDENT_VERIFICATION_ENFORCEMENT", "false").strip().lower()

    return configured in {"1", "true", "yes", "on"}

# DATABASE-BACKED RESIDENT VERIFICATION STATUS
def _resident_verification_status(user):
    #Read the current Resident verification state.
    #This runs against PostgreSQL for every protected request, so the 48-hour deadline cannot be bypassed by using an older authenticated session.
    #Missing verification records fail closed.

    try:
        with _engine().connect() as conn:
            result = get_resident_verification_status(conn, user["id"], enforce_deadline=_resident_deadline_enforced())

    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=("Resident verification status is temporarily unavailable.")) from exc

    if result is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("Resident verification information is unavailable for this account."))

    if result["status"] == "account_inactive":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This account is not active.")

    return result

# RESIDENT ACCOUNT ACCESS
def require_resident_account(request: Request, user=Depends(require_signed_in_user)):
    #Allow an active Resident to access account functions.
    #This remains available after the 48-hour deadline, allowing the user to verify their email and recover their access.
    #Do not use this dependency to protect restricted dashboard features or CSV exports.

    _authorize(request, user, RESIDENT_ROLES)
    _resident_verification_status(user)

    return user

# RESIDENT DASHBOARD ACCESS
def require_resident_dashboard_access(request: Request, user=Depends(require_signed_in_user)):
    #Enforce Resident access to authenticated dashboard features.
    #Verified Residents: Full dashboard access.
    #Unverified Residents within 48 hours: Basic dashboard access.
    #Unverified Residents after 48 hours: Restricted when deadline enforcement is enabled.
    #Public flood advisories should remain outside this dependency.

    _authorize(request, user, RESIDENT_ROLES)
    verification = _resident_verification_status(user)

    if not verification["can_access_dashboard"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("Your account verification period has expired. Verify your email address to restore Resident dashboard access."))

    return user

# VERIFIED RESIDENT FEATURES
def require_verified_resident(request: Request, user=Depends(require_signed_in_user)):
    #Require verified Resident email ownership.
    #Intended for:
        #- Official CSV download endpoints
        #- Verified-account-only features
        #- Future trusted email notification settings
    #Never rely on frontend button visibility alone.

    _authorize(request, user, RESIDENT_ROLES)
    verification = _resident_verification_status(user)

    if not verification["email_verified"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("Please verify your email address to use this feature."))

    if not verification["can_download_csv"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=("CSV downloads require a verified Resident account."))

    return user

# EXISTING AGENCY AND ADMIN DEPENDENCIES
require_agency_access = require_roles("agency", "admin")
require_admin_access = require_roles("admin")
require_agency_page = require_page_roles("agency", "admin")
require_admin_page = require_page_roles("admin")
