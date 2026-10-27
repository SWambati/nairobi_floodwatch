#PostgreSQL account and authorization schema for Nairobi FloodWatch.

from sqlalchemy import text
SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS floodwatch_users (
        id UUID PRIMARY KEY,
        google_sub TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL,
        email_verified BOOLEAN NOT NULL DEFAULT FALSE,
        display_name TEXT NOT NULL DEFAULT '',
        avatar_url TEXT,

        role TEXT NOT NULL DEFAULT 'resident'
            CHECK (role IN ('resident', 'agency', 'admin')),

        account_status TEXT NOT NULL DEFAULT 'active'
            CHECK (account_status IN ('active', 'suspended')),

        role_updated_at TIMESTAMPTZ,
        last_login_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS floodwatch_agency_requests (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL
            REFERENCES floodwatch_users(id),

        organization_name TEXT NOT NULL,
        organization_email TEXT,
        justification TEXT NOT NULL,

        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (
                status IN (
                    'pending', 'approved',
                    'rejected', 'withdrawn'
                )
            ),

        reviewed_by UUID
            REFERENCES floodwatch_users(id),

        reviewed_at TIMESTAMPTZ,
        review_notes TEXT,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS floodwatch_auth_audit (
        id UUID PRIMARY KEY,

        actor_user_id UUID
            REFERENCES floodwatch_users(id),

        target_user_id UUID
            REFERENCES floodwatch_users(id),

        action TEXT NOT NULL,
        previous_role TEXT,
        new_role TEXT,

        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_floodwatch_users_role_status
    ON floodwatch_users(role, account_status)
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_floodwatch_agency_requests_status
    ON floodwatch_agency_requests(status, created_at DESC)
    """,

    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_floodwatch_one_pending_request
    ON floodwatch_agency_requests(user_id)
    WHERE status = 'pending'
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_floodwatch_auth_audit_time
    ON floodwatch_auth_audit(occurred_at DESC)
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_floodwatch_auth_audit_target
    ON floodwatch_auth_audit(target_user_id, occurred_at DESC)
    """,
]


def ensure_auth_schema(engine):
    # Create account tables without changing existing flood-monitoring data.

    if engine.dialect.name != "postgresql":
        raise ValueError("FloodWatch authentication requires PostgreSQL.")

    with engine.begin() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(text(statement))

    return {
        "users": "floodwatch_users",
        "agency_requests": "floodwatch_agency_requests",
        "audit": "floodwatch_auth_audit",
    }
