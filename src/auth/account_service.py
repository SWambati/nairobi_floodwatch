#User accounts and agency approval services for Nairobi FloodWatch.

import json
from uuid import uuid4
from sqlalchemy import text


def _audit(conn, action, actor_id=None, target_id=None,
           previous_role=None, new_role=None, details=None):
    conn.execute(text("""
        INSERT INTO floodwatch_auth_audit (
            id, actor_user_id, target_user_id,
            action, previous_role, new_role, details
        )
        VALUES (
            CAST(:id AS UUID),
            CAST(:actor AS UUID),
            CAST(:target AS UUID),
            :action, :previous, :new,
            CAST(:details AS JSONB)
        )
    """), {
        "id": str(uuid4()),
        "actor": str(actor_id) if actor_id else None,
        "target": str(target_id) if target_id else None,
        "action": action,
        "previous": previous_role,
        "new": new_role,
        "details": json.dumps(details or {}),
    })


def get_user_by_google_sub(conn, google_sub):
    query = text("""
        SELECT id, google_sub, email, email_verified,
               display_name, avatar_url, role, account_status,
               created_at, updated_at, last_login_at
        FROM floodwatch_users
        WHERE google_sub = :sub
    """)

    row = conn.execute(query, {"sub": google_sub}).mappings().first()

    return dict(row) if row else None


def upsert_verified_google_user(conn, *, google_sub, email, email_verified, display_name="", avatar_url=None,):
    # Accept identity claims only after server-side Google token validation.
    google_sub = str(google_sub).strip()
    email = str(email).strip().lower()
    display_name = str(display_name or "").strip()

    if not email_verified:
        raise PermissionError("A verified Google email is required.")

    if not google_sub or not email or len(email) > 254:
        raise ValueError("Invalid Google identity information.")

    existing = conn.execute(text("""
        SELECT id, account_status
        FROM floodwatch_users
        WHERE google_sub = :sub
        FOR UPDATE
    """), {"sub": google_sub}).mappings().first()

    if existing and existing["account_status"] != "active":
        raise PermissionError("This account is suspended.")

    
    email_owner = conn.execute(text("""
        SELECT id, google_sub
        FROM floodwatch_users
        WHERE LOWER(BTRIM(email)) = LOWER(BTRIM(:email))
        FOR UPDATE
    """), {"email": email}).mappings().first()

    if email_owner and email_owner["google_sub"] != google_sub:
        raise PermissionError(
            "An existing account must be authenticated before Google linking."
        )


    if not existing:
        user_id = str(uuid4())

        inserted = conn.execute(text("""
            INSERT INTO floodwatch_users (
                id, google_sub, email, email_verified,
                display_name, avatar_url, role, account_status,
                last_login_at
            )
            VALUES (
                CAST(:id AS UUID), :sub, :email, TRUE,
                :name, :avatar, 'resident', 'active', NOW()
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """), {
            "id": user_id,
            "sub": google_sub,
            "email": email,
            "name": display_name,
            "avatar": avatar_url,
        }).scalar_one_or_none()

        if inserted:
            _audit(conn, action="user_created", target_id=inserted, new_role="resident",)
            return get_user_by_google_sub(conn, google_sub)

    updated = conn.execute(text("""
        UPDATE floodwatch_users
        SET email = :email,
            email_verified = TRUE,
            display_name = :name,
            avatar_url = :avatar,
            last_login_at = NOW(),
            updated_at = NOW()
        WHERE google_sub = :sub
          AND account_status = 'active'
        RETURNING id
    """), {
        "sub": google_sub,
        "email": email,
        "name": display_name,
        "avatar": avatar_url,
    }).scalar_one_or_none()

    if not updated:
        raise PermissionError("Account access is unavailable.")

    return get_user_by_google_sub(conn, google_sub)


def link_verified_google_user(
    conn,
    *,
    user_id,
    google_sub,
    email,
    email_verified,
):
    google_sub = str(google_sub or "").strip()
    email = str(email or "").strip().lower()

    if not google_sub or not email_verified:
        raise PermissionError("A verified Google identity is required.")

    if not email or len(email) > 254:
        raise ValueError("Invalid Google email.")

    user = conn.execute(text("""
        SELECT id, email, email_verified, google_sub,
               role, account_status
        FROM floodwatch_users
        WHERE id = CAST(:user_id AS UUID)
        FOR UPDATE
    """), {
        "user_id": str(user_id),
    }).mappings().first()

    if not user:
        raise PermissionError("Account not found.")

    if user["account_status"] != "active":
        raise PermissionError("Account is inactive.")

    if user["role"] not in {"agency", "admin"}:
        raise PermissionError("Privileged account required.")

    if not user["email_verified"]:
        raise PermissionError("Account email is not verified.")

    if user["email"].strip().lower() != email:
        raise PermissionError(
            "Google email does not match the existing account."
        )

    if user["google_sub"] not in (None, google_sub):
        raise PermissionError(
            "This account is already linked to another Google identity."
        )

    other_owner = conn.execute(text("""
        SELECT id
        FROM floodwatch_users
        WHERE google_sub = :google_sub
          AND id <> CAST(:user_id AS UUID)
        LIMIT 1
    """), {
        "google_sub": google_sub,
        "user_id": str(user_id),
    }).scalar_one_or_none()

    if other_owner is not None:
        raise PermissionError(
            "This Google identity belongs to another FloodWatch account."
        )

    if user["google_sub"] is None:
        updated = conn.execute(text("""
            UPDATE floodwatch_users
            SET google_sub = :google_sub,
                updated_at = NOW()
            WHERE id = CAST(:user_id AS UUID)
              AND google_sub IS NULL
              AND account_status = 'active'
            RETURNING id
        """), {
            "google_sub": google_sub,
            "user_id": str(user_id),
        }).scalar_one_or_none()

        if updated is None:
            raise PermissionError("Account linking was not completed.")

        _audit(
            conn,
            action="google_account_linked",
            actor_id=user["id"],
            target_id=user["id"],
            details={"provider": "google"},
        )

    return {
        "id": str(user["id"]),
        "role": user["role"],
    }


def submit_agency_request(conn, *, user_id, organization_name, justification, organization_email=None,):
    # Allow an active resident to request agency access.
    organization_name = organization_name.strip()
    justification = justification.strip()
    organization_email = (organization_email.strip().lower() if organization_email else None)

    if not 3 <= len(organization_name) <= 180:
        raise ValueError("Organization name must contain 3–180 characters.")

    if not 20 <= len(justification) <= 2000:
        raise ValueError("Justification must contain 20–2000 characters.")

    if organization_email and len(organization_email) > 254:
        raise ValueError("Organization email is too long.")

    user = conn.execute(text("""
        SELECT id, role, account_status
        FROM floodwatch_users
        WHERE id = CAST(:id AS UUID)
        FOR UPDATE
    """), {"id": str(user_id)}).mappings().first()

    if not user:
        raise ValueError("User account does not exist.")

    if user["account_status"] != "active":
        raise PermissionError("Suspended accounts cannot apply.")

    if user["role"] != "resident":
        raise PermissionError("Only residents may request agency access.")

    request_id = str(uuid4())

    created = conn.execute(text("""
        INSERT INTO floodwatch_agency_requests (
            id, user_id, organization_name,
            organization_email, justification, status
        )
        VALUES (
            CAST(:id AS UUID), CAST(:user_id AS UUID),
            :organization, :email, :justification, 'pending'
        )
        ON CONFLICT DO NOTHING
        RETURNING id
    """), {
        "id": request_id,
        "user_id": str(user_id),
        "organization": organization_name,
        "email": organization_email,
        "justification": justification,
    }).scalar_one_or_none()

    if not created:
        raise ValueError("An agency application is already pending.")

    _audit(conn, action="agency_access_requested", actor_id=user_id, target_id=user_id, details={"request_id": str(created)},)

    return {
        "id": str(created),
        "user_id": str(user_id),
        "status": "pending",
        "organization_name": organization_name,
    }


def _require_active_admin(conn, admin_id):
    admin = conn.execute(text("""
        SELECT id
        FROM floodwatch_users
        WHERE id = CAST(:id AS UUID)
          AND role = 'admin'
          AND account_status = 'active'
    """), {"id": str(admin_id)}).first()

    if not admin:
        raise PermissionError("Administrator privileges are required.")


def list_pending_agency_requests(conn, admin_id, limit=50):
    """List applications for an authorized administrator."""

    _require_active_admin(conn, admin_id)
    limit = max(1, min(int(limit), 100))

    rows = conn.execute(text("""
        SELECT r.id, r.user_id, r.organization_name,
               r.organization_email, r.justification,
               r.status, r.created_at,
               u.display_name, u.email AS applicant_email
        FROM floodwatch_agency_requests r
        JOIN floodwatch_users u ON u.id = r.user_id
        WHERE r.status = 'pending'
        ORDER BY r.created_at ASC
        LIMIT :limit
    """), {"limit": limit}).mappings()

    return [dict(row) for row in rows]


def review_agency_request(conn, *, admin_id, request_id, decision, notes=""):
    # Approve or reject an agency request atomically with role changes.
    if decision not in {"approved", "rejected"}:
        raise ValueError("Decision must be approved or rejected.")

    _require_active_admin(conn, admin_id)

    application = conn.execute(text("""
        SELECT id, user_id, status
        FROM floodwatch_agency_requests
        WHERE id = CAST(:id AS UUID)
        FOR UPDATE
    """), {"id": str(request_id)}).mappings().first()

    if not application:
        raise ValueError("Agency application was not found.")

    if application["status"] != "pending":
        raise ValueError("This application has already been reviewed.")

    applicant_id = application["user_id"]

    if decision == "approved":
        updated = conn.execute(text("""
            UPDATE floodwatch_users
            SET role = 'agency',
                role_updated_at = NOW(),
                updated_at = NOW()
            WHERE id = CAST(:id AS UUID)
              AND role = 'resident'
              AND account_status = 'active'
            RETURNING id
        """), {"id": str(applicant_id)}).scalar_one_or_none()

        if not updated:
            raise PermissionError("Applicant is not eligible for agency approval.")

    conn.execute(text("""
        UPDATE floodwatch_agency_requests
        SET status = :decision,
            reviewed_by = CAST(:admin_id AS UUID),
            reviewed_at = NOW(),
            review_notes = :notes,
            updated_at = NOW()
        WHERE id = CAST(:request_id AS UUID)
    """), {
        "decision": decision,
        "admin_id": str(admin_id),
        "request_id": str(request_id),
        "notes": notes.strip()[:2000],
    })

    _audit(
        conn,
        action=f"agency_request_{decision}",
        actor_id=admin_id,
        target_id=applicant_id,
        previous_role="resident" if decision == "approved" else None,
        new_role="agency" if decision == "approved" else None,
        details={
            "request_id": str(request_id),
            "decision": decision,
        },
    )

    return {
        "request_id": str(request_id),
        "user_id": str(applicant_id),
        "decision": decision,
        "role": "agency" if decision == "approved" else "resident",
    }