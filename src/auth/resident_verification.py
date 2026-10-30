# Resident email verification grace-period policy.
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

RESIDENT_GRACE_HOURS = 48
RESIDENT_GRACE_PERIOD = timedelta(hours=RESIDENT_GRACE_HOURS)

# DATABASE SCHEMA
def ensure_resident_verification_schema(engine):
    #Store the beginning of each Resident's verification grace period.
    #Existing active, unverified Residents receive a grace period beginning when this feature is installed.
    #Existing records are never reset on application restart.
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS
                floodwatch_resident_verification (
                    user_id UUID PRIMARY KEY
                        REFERENCES floodwatch_users(id)
                        ON DELETE CASCADE,

                    grace_started_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW()
                )
        """))

        # One-time backfill for existing unverified Residents.
        conn.execute(text("""
            INSERT INTO floodwatch_resident_verification (
                user_id,
                grace_started_at
            )
            SELECT
                id,
                NOW()
            FROM floodwatch_users
            WHERE role = 'resident'
              AND account_status = 'active'
              AND email_verified = FALSE

            ON CONFLICT (user_id) DO NOTHING
        """))

# START RESIDENT GRACE PERIOD
def start_resident_grace_period(conn, user_id):
    #Record the registration timestamp.
    #This must run in the same database transaction that creates the Resident account.
    #Repeated calls cannot extend the deadline.
    conn.execute(text("""
        INSERT INTO floodwatch_resident_verification (
            user_id,
            grace_started_at
        )
        VALUES (
            CAST(:user_id AS UUID),
            NOW()
        )

        ON CONFLICT (user_id) DO NOTHING
    """), {"user_id": str(user_id)})

# RESIDENT ACCESS POLICY
def evaluate_resident_verification(*, email_verified, grace_started_at, now=None, enforce_deadline=False):
    #Evaluate the verification state of a Resident.
    #Verified: Full Resident account access.
    #Unverified, within 48 hours: Basic dashboard access. CSV downloads remain restricted.
    #Unverified, after 48 hours: Dashboard access is restricted only when deadline enforcement has been enabled.
    #The caller must first confirm that the account is active and has the Resident role.
    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Current time must be timezone-aware.")

    # Verified Residents have permanent verification.
    if email_verified:
        return {
            "status": "verified",
            "email_verified": True,
            "verification_required": False,
            "deadline_passed": False,
            "grace_expires_at": None,
            "seconds_remaining": None,
            "can_access_dashboard": True,
            "can_download_csv": True,
        }

    if grace_started_at is None:
        raise ValueError("Unverified Resident has no grace-period record.")

    if (grace_started_at.tzinfo is None or grace_started_at.utcoffset() is None):
        raise ValueError("Grace-period timestamp must be timezone-aware.")

    deadline = (grace_started_at + RESIDENT_GRACE_PERIOD)
    remaining_seconds = max(0, int((deadline - now).total_seconds()))
    deadline_passed = now >= deadline

    if deadline_passed:
        status = ("verification_required" if enforce_deadline else "grace_expired_not_enforced")
    else:
        status = "grace_period"

    return {
        "status": status,
        "email_verified": False,
        "verification_required": deadline_passed,
        "deadline_passed": deadline_passed,
        "grace_expires_at": deadline.isoformat(),
        "seconds_remaining": remaining_seconds,

        # Never restrict public advisories through this policy.
        # This flag is only for authenticated dashboard access.
        "can_access_dashboard": (not deadline_passed or not enforce_deadline),

        # Verified ownership is required for CSV exports.
        "can_download_csv": False,
    }

# DATABASE-BACKED STATUS LOOKUP
def get_resident_verification_status(conn, user_id, *, enforce_deadline=False, now=None):
    #Load the authoritative Resident account state.
    #Do not accept role or email_verified values from browser input.
    row = conn.execute(text("""
        SELECT
            u.role,
            u.account_status,
            u.email_verified,
            v.grace_started_at

        FROM floodwatch_users u

        LEFT JOIN floodwatch_resident_verification v
            ON v.user_id = u.id

        WHERE u.id = CAST(:user_id AS UUID)
        LIMIT 1
    """), {
        "user_id": str(user_id),
    }).mappings().first()

    if row is None:
        return None

    if row["role"] != "resident":
        return None

    if row["account_status"] != "active":
        return {
            "status": "account_inactive",
            "email_verified": bool(row["email_verified"]),
            "verification_required": False,
            "deadline_passed": False,
            "grace_expires_at": None,
            "seconds_remaining": None,
            "can_access_dashboard": False,
            "can_download_csv": False,
        }

    return evaluate_resident_verification(email_verified=bool(row["email_verified"]), grace_started_at=row["grace_started_at"], now=now, enforce_deadline=enforce_deadline)
