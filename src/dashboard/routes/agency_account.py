import logging
from functools import lru_cache
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.auth.authorization import require_roles
from src.operational.persistence import create_database_engine


logger = logging.getLogger("floodwatch.agency.account")
router = APIRouter(prefix="/api/agency", tags=["Agency Account"], include_in_schema=False)

# Agency-only authorization, including existing MFA checks.
require_agency_officer = require_roles("agency")

@lru_cache(maxsize=1)
def get_engine():
    return create_database_engine()

def extract_position(justification):
    if not justification:
        return None

    prefix = "Position / Job Title:"

    for line in justification.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip() or None

    return None

@router.get("/account")
def get_agency_account(response: Response, user=Depends(require_agency_officer)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    try:
        with get_engine().connect() as conn:
            account = conn.execute(
                text("""
                    SELECT
                        u.id,
                        u.display_name,
                        u.email,
                        u.role,
                        u.email_verified,
                        u.account_status,
                        u.created_at,

                        application.organization_name,
                        application.organization_email,
                        application.justification,
                        application.status
                            AS application_status,
                        application.created_at
                            AS applied_at,
                        application.reviewed_at
                            AS approved_at

                    FROM floodwatch_users u

                    LEFT JOIN LATERAL (
                        SELECT
                            r.organization_name,
                            r.organization_email,
                            r.justification,
                            r.status,
                            r.created_at,
                            r.reviewed_at

                        FROM floodwatch_agency_requests r

                        WHERE r.user_id = u.id
                          AND r.status = 'approved'

                        ORDER BY r.reviewed_at DESC NULLS LAST,
                                 r.created_at DESC

                        LIMIT 1
                    ) application ON TRUE

                    WHERE u.id = CAST(:user_id AS UUID)
                      AND u.role = 'agency'
                      AND u.account_status = 'active'

                    LIMIT 1
                """),
                {
                    "user_id": str(user["id"]),
                },
            ).mappings().first()

    except SQLAlchemyError as exc:
        logger.exception("Failed to retrieve agency officer account.")
        raise HTTPException(status_code=503, detail="Account information is temporarily unavailable.") from exc

    if account is None:
        raise HTTPException(status_code=404, detail="Agency account not found.")

    return {
        "account": {
            "id": str(account["id"]),
            "display_name": account["display_name"],
            "email": account["email"],
            "role": account["role"],
            "email_verified": account["email_verified"],
            "account_status": account["account_status"],
            "created_at": account["created_at"],
        },
        "organization": {
            "name": account["organization_name"],
            "contact_email": account["organization_email"],
            "position_title": extract_position(account["justification"]),
            "application_status": (account["application_status"]),
            "applied_at": account["applied_at"],
            "approved_at": account["approved_at"],
        },
        "security": {
            "mfa_required": True,
            "role_changes_managed_by_admin": True,
        },
    }
