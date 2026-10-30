import json
import logging
from functools import lru_cache
from typing import Literal
from uuid import UUID, uuid4
from fastapi import (APIRouter, Depends, HTTPException, Query, Request)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.auth.authorization import require_admin_access
from src.auth.config import get_auth_settings
from src.operational.persistence import create_database_engine

logger = logging.getLogger("floodwatch.admin.approvals")

#API ROUTER
router = APIRouter(
    prefix="/api/admin/agency-requests",
    tags=["Admin Agency Approvals"],
    dependencies=[Depends(require_admin_access)],
    include_in_schema=False)

@lru_cache(maxsize=1)
def get_engine():
    return create_database_engine()

def json_response(data, status_code=200):
    return JSONResponse(content=jsonable_encoder(data), status_code=status_code, headers={"Cache-Control": "no-store"})

def database_error(exc):
    logger.exception("Agency approval database operation failed")

    raise HTTPException(status_code=503, detail="Agency approval service is temporarily unavailable.") from exc


def require_same_origin_json(request: Request):
    #Protect state-changing requests from cross-origin submissions and unsupported content types.
   
    settings = get_auth_settings()
    origin = request.headers.get("origin")

    if origin != settings.base_url.rstrip("/"):
        raise HTTPException(status_code=403, detail="Invalid request origin.")

    content_type = (request.headers.get("content-type", "").split(";", 1)[0].strip().lower())

    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="JSON content type is required.")

#REQUEST MODEL
class AgencyDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    review_notes: str = Field(default="", max_length=2000)
    affiliation_confirmed: bool = False

#LIST APPLICATIONS
@router.get("")
def list_agency_requests(status: Literal["all", "pending", "approved", "rejected", "withdrawn"] = "pending", limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    #Retrieve agency access requests with applicant information and pagination.
    condition = ("" if status == "all" else "WHERE r.status = :status")

    params = {
        "status": status,
        "limit": limit,
        "offset": offset
    }

    try:
        with get_engine().connect() as conn:
            requests = conn.execute(
                text(f"""
                    SELECT
                        r.id,
                        r.user_id,
                        r.organization_name,
                        r.organization_email,
                        r.justification,
                        r.status,
                        r.created_at,
                        r.reviewed_at,
                        r.review_notes,

                        u.display_name AS applicant_name,
                        u.email AS applicant_email,
                        u.email_verified,
                        u.account_status,
                        u.role AS applicant_role,

                        reviewer.display_name AS reviewer_name

                    FROM floodwatch_agency_requests r

                    JOIN floodwatch_users u
                        ON u.id = r.user_id

                    LEFT JOIN floodwatch_users reviewer
                        ON reviewer.id = r.reviewed_by

                    {condition}

                    ORDER BY r.created_at DESC, r.id DESC

                    LIMIT :limit OFFSET :offset
                """),
                params,
            ).mappings().all()

            total = conn.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM floodwatch_agency_requests r
                    {condition}
                """),
                params,
            ).scalar_one()

        return json_response({"requests": [dict(row) for row in requests], "total": total,})

    except SQLAlchemyError as exc:
        database_error(exc)

#VIEW APPLICATION DETAILS
@router.get("/{request_id}")
def get_agency_request(request_id: UUID):
    #Retrieve a single agency application and its review information.
    try:
        with get_engine().connect() as conn:
            application = conn.execute(
                text("""
                    SELECT
                        r.id,
                        r.user_id,
                        r.organization_name,
                        r.organization_email,
                        r.justification,
                        r.status,
                        r.created_at,
                        r.updated_at,
                        r.reviewed_at,
                        r.review_notes,
                        r.reviewed_by,

                        u.display_name AS applicant_name,
                        u.email AS applicant_email,
                        u.email_verified,
                        u.account_status,
                        u.role AS applicant_role,

                        reviewer.display_name AS reviewer_name

                    FROM floodwatch_agency_requests r

                    JOIN floodwatch_users u
                        ON u.id = r.user_id

                    LEFT JOIN floodwatch_users reviewer
                        ON reviewer.id = r.reviewed_by

                    WHERE r.id = CAST(:request_id AS UUID)
                """),
                {"request_id": str(request_id)},
            ).mappings().first()

    except SQLAlchemyError as exc:
        database_error(exc)

    if application is None:
        raise HTTPException(status_code=404, detail="Agency application not found.")

    return json_response(dict(application))

#APPROVE OR REJECT APPLICATION
@router.post("/{request_id}/decision")
def decide_agency_request(request_id: UUID, payload: AgencyDecisionRequest, request: Request, administrator=Depends(require_admin_access)):
    #Review an agency application.
    #All database updates occur within one transaction.
    #Approval:
    #- Requires verified, active applicant
    #- Changes role from resident to agency
    #- Revokes existing applicant sessions

    #Rejection:
    #- Records rejection reason
    #- Does not grant agency permissions

    #Both:
    #- Record reviewer and timestamp
    #- Create an authentication audit event
    #- Reject repeated decisions

    require_same_origin_json(request)
    notes = payload.review_notes.strip()

    if payload.decision == "rejected" and len(notes) < 5:
        raise HTTPException(status_code=422, detail="Provide a rejection reason of at least five characters.")

    if (payload.decision == "approved" and not payload.affiliation_confirmed):
        raise HTTPException(status_code=422, detail="Confirm institutional affiliation verification before approval.")

    try:
        with get_engine().begin() as conn:
            # Lock the request to prevent concurrent reviews.
            application = conn.execute(
                text("""
                    SELECT id, user_id, status
                    FROM floodwatch_agency_requests
                    WHERE id = CAST(:request_id AS UUID)
                    FOR UPDATE
                """),
                {"request_id": str(request_id)},
            ).mappings().first()

            if application is None:
                raise HTTPException(status_code=404, detail="Application not found.")

            if application["status"] != "pending":
                raise HTTPException(status_code=409, detail="This application is no longer pending.")

            applicant_id = str(application["user_id"])

            # Lock the applicant account as well.
            applicant = conn.execute(
                text("""
                    SELECT
                        id,
                        role,
                        account_status,
                        email_verified

                    FROM floodwatch_users

                    WHERE id = CAST(:user_id AS UUID)
                    FOR UPDATE
                """),
                {"user_id": applicant_id},
            ).mappings().first()

            if applicant is None:
                raise HTTPException(status_code=409, detail="Applicant account no longer exists.")

            if applicant["role"] != "resident":
                raise HTTPException(status_code=409, detail="Applicant is no longer eligible for agency onboarding.")

            # APPROVAL
            if payload.decision == "approved":
                if (applicant["account_status"] != "active" or not applicant["email_verified"]):
                    raise HTTPException(status_code=409, detail=("Approval requires an active account with a verified email address."))

                conn.execute(
                    text("""
                        UPDATE floodwatch_users
                        SET
                            role = 'agency',
                            role_updated_at = NOW(),
                            updated_at = NOW()
                        WHERE id = CAST(:user_id AS UUID)
                    """),
                    {"user_id": applicant_id},
                )

                # Force the newly approved officer to sign in again with the updated role.
                conn.execute(
                    text("""
                        UPDATE floodwatch_user_sessions
                        SET revoked_at = NOW()
                        WHERE user_id = CAST(:user_id AS UUID)
                          AND revoked_at IS NULL
                    """),
                    {"user_id": applicant_id},
                )

            # SAVE REVIEW DECISION
            conn.execute(
                text("""
                    UPDATE floodwatch_agency_requests

                    SET
                        status = :decision,
                        reviewed_by = CAST(:reviewer_id AS UUID),
                        reviewed_at = NOW(),
                        review_notes = :notes,
                        updated_at = NOW()

                    WHERE id = CAST(:request_id AS UUID)
                """),
                {
                    "decision": payload.decision,
                    "reviewer_id": str(administrator["id"]),
                    "notes": notes or None,
                    "request_id": str(request_id),
                },
            )

            # AUDIT LOG
            conn.execute(
                text("""
                    INSERT INTO floodwatch_auth_audit (
                        id,
                        actor_user_id,
                        target_user_id,
                        action,
                        previous_role,
                        new_role,
                        details,
                        occurred_at
                    )

                    VALUES (
                        CAST(:id AS UUID),
                        CAST(:actor_id AS UUID),
                        CAST(:target_id AS UUID),
                        :action,
                        :previous_role,
                        :new_role,
                        CAST(:details AS JSONB),
                        NOW()
                    )
                """),
                {
                    "id": str(uuid4()),
                    "actor_id": str(administrator["id"]),
                    "target_id": applicant_id,
                    "action": f"agency_request_{payload.decision}",
                    "previous_role": "resident",
                    "new_role": (
                        "agency"
                        if payload.decision == "approved"
                        else None
                    ),
                    "details": json.dumps({
                        "request_id": str(request_id),
                        "decision": payload.decision,
                        "affiliation_confirmed": (payload.affiliation_confirmed)
                    }),
                },
            )

    except SQLAlchemyError as exc:
        database_error(exc)

    return json_response({
        "request_id": str(request_id),
        "status": payload.decision,
        "message": "Agency application decision recorded.",
    })
