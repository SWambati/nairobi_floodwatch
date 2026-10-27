#Authenticated MFA enrollment endpoints for Nairobi FloodWatch.
import base64
import hashlib
import io
import os
from functools import lru_cache
import qrcode
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.auth.config import get_auth_settings
from src.auth.google_oauth import (SESSION_COOKIE, require_signed_in_user)
from src.auth.mfa_service import (begin_totp_enrollment, confirm_totp_enrollment, get_mfa_status)
from src.operational.persistence import create_database_engine

router = APIRouter(prefix="/auth/mfa", include_in_schema=False)
PRIVILEGED_ROLES = {"agency", "admin"}

class ConfirmRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

def _response(data, status_code=200):
    return JSONResponse(
        content=data,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )

def _require_same_origin_json(request: Request):
    settings = get_auth_settings()

    if request.headers.get("origin") != settings.base_url:
        raise HTTPException(status_code=403, detail="Invalid request origin.")

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="JSON request body required.")

def _require_encryption_key():
    key = os.getenv("FLOODWATCH_MFA_ENCRYPTION_KEY", "").strip()

    try:
        Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail="Authenticator enrollment is not configured.") from exc

def _session_hash(request):
    token = request.cookies.get(SESSION_COOKIE)

    if not token or len(token) > 256:
        raise HTTPException(status_code=401, detail="A valid login session is required.")

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_recent_login(request, user):
    #Require a valid application session created within 10 minutes.
    #This is an initial enrollment safeguard. Full step-up reauthentication will be implemented with MFA enforcement.
    digest = _session_hash(request)

    with _engine().connect() as conn:
        valid = conn.execute(text("""
            SELECT 1
            FROM floodwatch_user_sessions
            WHERE token_hash = :digest
              AND user_id = CAST(:user_id AS UUID)
              AND revoked_at IS NULL
              AND expires_at > NOW()
              AND created_at >= NOW() - INTERVAL '10 minutes'
        """), {
            "digest": digest,
            "user_id": user["id"],
        }).scalar_one_or_none()

    if not valid:
        raise HTTPException(status_code=403, detail=("Recent authentication is required. Please sign out and sign in again."))

    return digest

def _qr_data_url(uri):
    #Render the sensitive enrollment URI as an in-memory QR image.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    return f"data:image/png;base64,{encoded}"

@router.get("/status")
def mfa_status(user=Depends(require_signed_in_user)):
    with _engine().begin() as conn:
        status = get_mfa_status(conn, user["id"])

    return _response({
        "enabled": status["enabled"],
        "pending": status["pending"],
        "required": user["role"] in PRIVILEGED_ROLES,
        "setup_required": (
            user["role"] in PRIVILEGED_ROLES
            and not status["enabled"]
        ),
    })

@router.post("/enroll", dependencies=[Depends(_require_same_origin_json)])
def enroll_mfa(request: Request, user=Depends(require_signed_in_user)):
    _require_encryption_key()
    _require_recent_login(request, user)

    try:
        with _engine().begin() as conn:
            enrollment = begin_totp_enrollment(conn, user["id"])
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail="MFA enrollment is not available for this account.") from exc

    # Both the QR image and provisioning URI contain the TOTP secret.
    return _response({
        "status": enrollment["status"],
        "expires_at": enrollment["expires_at"],
        "qr_code_data_url": _qr_data_url(enrollment["otpauth_uri"]),
        "otpauth_uri": enrollment["otpauth_uri"],
        "message": ("Scan the QR code with Google Authenticator, then confirm using a six-digit code."),
    })

@router.post("/confirm", dependencies=[Depends(_require_same_origin_json)])
def confirm_mfa(request: Request, data: ConfirmRequest, user=Depends(require_signed_in_user)):
    _require_encryption_key()
    current_session_hash = _require_recent_login(request, user)

    try:
        with _engine().begin() as conn:
            result = confirm_totp_enrollment(conn, user["id"], data.code)

            if result is not None:
                # Treat successful enrollment confirmation as
                # MFA verification of the current session.
                conn.execute(text("""
                    UPDATE floodwatch_user_sessions
                    SET mfa_verified_at = NOW()
                    WHERE token_hash = :digest
                      AND user_id = CAST(:user_id AS UUID)
                      AND revoked_at IS NULL
                      AND expires_at > NOW()
                """), {
                    "digest": current_session_hash,
                    "user_id": user["id"],
                })

                # Revoke other sessions so older sessions cannot
                # survive MFA enrollment without verification.
                conn.execute(text("""
                    UPDATE floodwatch_user_sessions
                    SET revoked_at = NOW()
                    WHERE user_id = CAST(:user_id AS UUID)
                      AND token_hash <> :digest
                      AND revoked_at IS NULL
                """), {
                    "user_id": user["id"],
                    "digest": current_session_hash,
                })

    except PermissionError as exc:
        if "Too many MFA attempts" in str(exc):
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.", headers={"Retry-After": "900"}) from exc

        raise HTTPException(status_code=403, detail="MFA confirmation is unavailable.") from exc

    # Handle failures after the transaction has committed, so
    # failed-attempt counters are not accidentally rolled back.
    if result is None:
        raise HTTPException(status_code=400, detail="Invalid, expired, or unavailable MFA code.")

    return _response({
        "enabled": True,
        "recovery_codes": result["recovery_codes"],
        "message": (
            "Google Authenticator is now enabled. "
            "Save your recovery codes securely. "
            "They will not be displayed again."),
    })
