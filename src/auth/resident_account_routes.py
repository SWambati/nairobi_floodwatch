#Resident account status and email OTP verification routes.
import logging
from functools import lru_cache
from fastapi import (APIRouter, Depends, HTTPException, Request)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.auth.authorization import (require_resident_account, _resident_verification_status)
from src.auth.config import get_auth_settings
from src.auth.email_delivery import (EmailConfigurationError, EmailDeliveryError, send_resident_verification_otp, ensure_email_configured)
from src.auth.resident_otp_service import (OTPRequestLimited, issue_resident_email_otp, verify_resident_email_otp)
from src.operational.persistence import create_database_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/resident/account", tags=["Resident Account"], include_in_schema=False)

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

class VerifyOTPRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")

def _json_response(content, status_code=200):
    return JSONResponse(status_code=status_code, content=content, headers={"Cache-Control": "no-store"})

def _require_same_origin(request: Request):
    #Protect authenticated POST operations against cross-origin requests.
    settings = get_auth_settings()

    if request.headers.get("origin") != settings.base_url:
        raise HTTPException(status_code=403, detail="Invalid request origin.")

    content_type = (request.headers.get("content-type", "").split(";")[0].strip().lower())

    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="JSON requests are required.")

# RESIDENT VERIFICATION STATUS
@router.get("/verification")
def resident_verification_status(user=Depends(require_resident_account)):
    verification = _resident_verification_status(user)

    return _json_response({
        "authenticated": True,
        "display_name": user.get("display_name"),
        "email": user["email"],
        "role": "resident",
        "email_verified": verification["email_verified"],
        "verification_status": verification["status"],
        "verification_required": verification["verification_required"],
        "grace_expires_at": verification["grace_expires_at"],
        "seconds_remaining": verification["seconds_remaining"],
        "can_access_dashboard": verification["can_access_dashboard"],
        "can_download_csv": verification["can_download_csv"],
    })

# REQUEST / RESEND EMAIL OTP
@router.post("/send-verification-otp")
def send_verification_otp(request: Request, user=Depends(require_resident_account)):
    _require_same_origin(request)

    # Verification is already complete.
    if user.get("email_verified"):
        return _json_response({
            "message": "Your email address is already verified.",
            "email_verified": True,
        })

    # Never issue OTPs if email delivery is unavailable.
    try:
        ensure_email_configured()

    except EmailConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Email service is not configured.") from exc

    try:
        with _engine().begin() as conn:
            issued = issue_resident_email_otp(conn, user["id"])

    except OTPRequestLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc

    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Resident account is not eligible.") from exc

    if issued is None:
        return _json_response({
            "message": "Your email address is already verified.",
            "email_verified": True,
        })

    try:
        send_resident_verification_otp(issued["email"], issued["code"])

    except (EmailConfigurationError, EmailDeliveryError):
        logger.warning("Resident verification email delivery failed.")

        raise HTTPException(status_code=503, detail=("Verification email could not be delivered. Please try again later."))

    # Never include the OTP in the HTTP response.
    return _json_response(
        {
            "message": ("A six-digit verification code has been sent to your registered email address."),
            "expires_in_seconds": 600,
        },
        status_code=202,
    )

# VERIFY RESIDENT EMAIL OTP
@router.post("/verify-otp")
def verify_resident_otp(request: Request, data: VerifyOTPRequest, user=Depends(require_resident_account)):
    _require_same_origin(request)

    with _engine().begin() as conn:
        verified = verify_resident_email_otp(conn, user["id"], data.code)

    if not verified:
        raise HTTPException(status_code=400, detail=("Invalid, expired or already-used verification code."))

    return _json_response({
        "message": ("Email verification successful. Your Resident account is now verified."),
        "email_verified": True,
        "can_download_csv": True,
    })
