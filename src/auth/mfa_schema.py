#PostgreSQL schema for Nairobi FloodWatch MFA.
import argparse
from sqlalchemy import inspect, text
from src.operational.persistence import create_database_engine

MFA_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS floodwatch_mfa_factors (
        user_id UUID PRIMARY KEY
            REFERENCES floodwatch_users(id)
            ON DELETE CASCADE,

        secret_ciphertext TEXT NOT NULL,

        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'active')),

        pending_expires_at TIMESTAMPTZ,
        activated_at TIMESTAMPTZ,

        last_accepted_step BIGINT NOT NULL DEFAULT -1
            CHECK (last_accepted_step >= -1),

        failed_attempts INTEGER NOT NULL DEFAULT 0
            CHECK (failed_attempts >= 0),

        locked_until TIMESTAMPTZ,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        CONSTRAINT floodwatch_mfa_factor_state_check
            CHECK (
                (
                    status = 'pending'
                    AND activated_at IS NULL
                    AND pending_expires_at IS NOT NULL
                )
                OR
                (
                    status = 'active'
                    AND activated_at IS NOT NULL
                )
            )
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS floodwatch_mfa_recovery_codes (
        id UUID PRIMARY KEY,

        user_id UUID NOT NULL
            REFERENCES floodwatch_mfa_factors(user_id)
            ON DELETE CASCADE,

        code_hash TEXT NOT NULL UNIQUE
            CHECK (LENGTH(code_hash) = 64),

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        used_at TIMESTAMPTZ
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS floodwatch_mfa_challenges (
        token_hash TEXT PRIMARY KEY
            CHECK (LENGTH(token_hash) = 64),

        user_id UUID NOT NULL
            REFERENCES floodwatch_users(id)
            ON DELETE CASCADE,

        purpose TEXT NOT NULL DEFAULT 'login'
            CHECK (purpose IN ('login', 'step_up')),

        auth_method TEXT NOT NULL
            CHECK (auth_method IN ('google', 'password')),

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,

        used_at TIMESTAMPTZ,
        revoked_at TIMESTAMPTZ,

        failed_attempts INTEGER NOT NULL DEFAULT 0
            CHECK (failed_attempts >= 0),

        CONSTRAINT floodwatch_mfa_challenge_expiry_check
            CHECK (expires_at > created_at)
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_floodwatch_mfa_recovery_user
    ON floodwatch_mfa_recovery_codes(user_id, used_at)
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_floodwatch_mfa_challenge_user
    ON floodwatch_mfa_challenges(user_id, created_at DESC)
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_floodwatch_mfa_challenge_expiry
    ON floodwatch_mfa_challenges(expires_at)
    """,

    """
    ALTER TABLE floodwatch_user_sessions
    ADD COLUMN IF NOT EXISTS mfa_verified_at TIMESTAMPTZ
    """,

    """
    ALTER TABLE floodwatch_user_sessions
    ADD COLUMN IF NOT EXISTS auth_method TEXT
    """,

    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'floodwatch_user_sessions'::regclass
              AND conname = 'floodwatch_session_auth_method_check'
        ) THEN
            ALTER TABLE floodwatch_user_sessions
            ADD CONSTRAINT floodwatch_session_auth_method_check
            CHECK (
                auth_method IS NULL
                OR auth_method IN ('google', 'password')
            );
        END IF;
    END
    $$
    """,
]

MFA_TABLES = [
    "floodwatch_mfa_factors",
    "floodwatch_mfa_recovery_codes",
    "floodwatch_mfa_challenges",
]

def migrate_mfa_schema(conn):
    # Apply the MFA schema within the caller's transaction.
    if conn.engine.dialect.name != "postgresql":
        raise ValueError("MFA requires PostgreSQL.")

    for statement in MFA_SCHEMA_STATEMENTS:
        conn.execute(text(statement))

def verify_mfa_schema(conn):
    # Verify MFA tables and session columns exist.
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())

    for table in MFA_TABLES:
        if table not in existing:
            raise RuntimeError(f"Missing MFA table: {table}")

        print(f"{table}: OK")

    required_factor_columns = {
        "user_id",
        "secret_ciphertext",
        "status",
        "pending_expires_at",
        "activated_at",
        "last_accepted_step",
        "failed_attempts",
        "locked_until",
    }

    factor_columns = {
        column["name"]
        for column in inspector.get_columns("floodwatch_mfa_factors")
    }

    if not required_factor_columns.issubset(factor_columns):
        raise RuntimeError("MFA factor columns are incomplete.")

    required_session_columns = {
        "mfa_verified_at",
        "auth_method",
    }

    session_columns = {
        column["name"]
        for column in inspector.get_columns("floodwatch_user_sessions")
    }

    if not required_session_columns.issubset(session_columns):
        raise RuntimeError("MFA session columns are incomplete.")

    print("MFA factor fields: OK")
    print("MFA session fields: OK")


def account_snapshot(conn):
    # Ensure migration does not alter existing accounts or sessions.
    return dict(conn.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM floodwatch_users)
                AS users,
            (SELECT COUNT(*) FROM floodwatch_users
             WHERE role = 'admin')
                AS administrators,
            (SELECT COUNT(*) FROM floodwatch_user_sessions)
                AS sessions
    """)).mappings().one())

def main():
    parser = argparse.ArgumentParser(description="Initialize Nairobi FloodWatch MFA schema.")

    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--dry-run", action="store_true")
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    engine = create_database_engine()

    try:
        with engine.connect() as conn:
            transaction = conn.begin()

            try:
                before = account_snapshot(conn)
                print("\nNairobi FloodWatch - MFA Schema")
                print("=" * 50)

                if not args.verify:
                    migrate_mfa_schema(conn)

                verify_mfa_schema(conn)
                after = account_snapshot(conn)

                if before != after:
                    raise RuntimeError("MFA migration unexpectedly changed account or session counts.")

                if args.apply:
                    transaction.commit()
                    print("MFA migration: COMMITTED")

                else:
                    transaction.rollback()

                    if args.dry_run:
                        print("MFA dry run: ROLLED BACK")
                    else:
                        print("MFA schema verification: PASSED")

                print("Existing accounts and sessions: PRESERVED")

            finally:
                if transaction.is_active:
                    transaction.rollback()

    finally:
        engine.dispose()

if __name__ == "__main__":
    main()
