import logging
from functools import lru_cache
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.auth.local_routes import (REQUEST_ACCEPTED, _attempt_delivery, _reply, _require_email_delivery, _throttle, _valid_email, require_same_origin_json)
from src.auth.password_service import (register_local_user, validate_password)
from src.auth.email_token_service import (issue_email_verification)
from src.auth.email_delivery import (send_verification_email)
from src.operational.persistence import (create_database_engine)

#LOGGING AND ROUTER CONFIGURATION
logger = logging.getLogger("floodwatch.auth.agency_registration")
router = APIRouter(prefix="/auth/agency", tags=["Agency Registration"], dependencies=[Depends(require_same_origin_json)], include_in_schema=False)

#DATABASE CONNECTION
@lru_cache(maxsize=1)
def get_engine():
    return create_database_engine()

#REGISTRATION REQUEST MODEL
class AgencyRegistrationRequest(BaseModel):
    #Required information for a new agency application.
    display_name: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    organization_name: str = Field(min_length=2, max_length=160)
    organization_email: str | None = Field(default=None, max_length=254)
    position_title: str = Field(min_length=2, max_length=120)
    justification: str = Field(min_length=20, max_length=1000)

#INPUT NORMALIZATION
def clean_single_line(value: str) -> str:
    return " ".join(value.split()).strip()

def validate_agency_details(data: AgencyRegistrationRequest):
    name = clean_single_line(data.display_name)
    organization = clean_single_line(data.organization_name)
    position = clean_single_line(data.position_title)
    justification = data.justification.strip()

    if (len(name) < 2 or len(organization) < 2 or len(position) < 2 or len(justification) < 20):
        raise HTTPException(status_code=422, detail=("Please provide complete personal and institutional registration details."))

    if any(ord(char) < 32 and char not in "\n\r\t" for char in justification):
        raise HTTPException(status_code=422, detail="Invalid characters in the justification.")

    organization_email = None

    if data.organization_email:
        organization_email = _valid_email(data.organization_email)

    return {
        "display_name": name,
        "organization_name": organization,
        "organization_email": organization_email,
        "position_title": position,
        "justification": justification,
    }

#AGENCY REGISTRATION ENDPOINT
@router.post("/register", status_code=202)
def register_agency_officer(request: Request, data: AgencyRegistrationRequest):
    #ENSURE EMAIL SERVICE IS CONFIGURED
    _require_email_delivery()

    #VALIDATE EMAIL AND PASSWORD
    email = _valid_email(data.email)

    try:
        validate_password(data.password, email)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    #VALIDATE AGENCY INFORMATION
    details = validate_agency_details(data)

    #REGISTRATION RATE LIMITING
    _throttle(request, "agency-register", email, ip_limit=8, email_limit=3)
    verification_token = None

    #DATABASE TRANSACTION
    try:
        with get_engine().begin() as conn:
            user = register_local_user(conn, email=email, password=data.password, display_name=details["display_name"])
            user_id = str(user["id"])

            eligible_user_id = conn.execute(
                text("""
                    UPDATE floodwatch_users
                    SET
                        account_status = 'pending_verification',
                        updated_at = NOW()

                    WHERE id = CAST(:user_id AS UUID)
                      AND role = 'resident'
                      AND email_verified = FALSE
                      AND password_hash IS NOT NULL
                      AND google_sub IS NULL
                      AND account_status IN (
                          'active',
                          'pending_verification'
                      )

                    RETURNING id
                """),
                {
                    "user_id": user_id,
                },
            ).scalar_one_or_none()

            if eligible_user_id is None:
                logger.error("New agency applicant failed verification-state initialization.")
                raise HTTPException(status_code=503, detail=("Agency registration could not be initialized. Please try again later."))

            request_description = (
                f"Position / Job Title: "
                f"{details['position_title']}\n\n"
                f"Reason for Request:\n"
                f"{details['justification']}")

            conn.execute(
                text("""
                    INSERT INTO floodwatch_agency_requests (
                        id,
                        user_id,
                        organization_name,
                        organization_email,
                        justification,
                        status,
                        created_at,
                        updated_at
                    )

                    VALUES (
                        CAST(:request_id AS UUID),
                        CAST(:user_id AS UUID),
                        :organization_name,
                        :organization_email,
                        :justification,
                        'pending',
                        NOW(),
                        NOW()
                    )
                """),
                {
                    "request_id": str(uuid4()),
                    "user_id": user_id,
                    "organization_name": (details["organization_name"]),
                    "organization_email": (details["organization_email"]),
                    "justification": request_description,
                },
            )
        
            #GENERATE EMAIL VERIFICATION TOKEN
            verification_token = issue_email_verification(conn, eligible_user_id)

            if not verification_token:
                logger.error("Agency verification token generation failed.")
                raise HTTPException(status_code=503, detail=("Agency email verification could not be initialized. Please try again later."))

    #DUPLICATE REGISTRATION HANDLING
    except ValueError:
        return _reply(REQUEST_ACCEPTED, 202)

    except IntegrityError as exc:
        sqlstate = (getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None))

        # PostgreSQL unique-constraint violation.
        if sqlstate == "23505":
            return _reply(REQUEST_ACCEPTED, 202)
        logger.exception("Agency registration database constraint failed.")

        raise

    #SEND EMAIL AFTER DATABASE COMMIT
    _attempt_delivery(send_verification_email, email, verification_token)

    #RETURN REGISTRATION RESPONSE
    return _reply(
        (
            "Agency registration request received. "
            "Please check your email for verification instructions. Agency Dashboard access requires email verification and administrator approval."),
        202,
    )
