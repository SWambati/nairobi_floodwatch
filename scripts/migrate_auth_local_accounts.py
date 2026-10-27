# Add local email/password account support to Nairobi FloodWatch.

import argparse
from sqlalchemy import text
from src.operational.persistence import create_database_engine

def account_counts(conn):
    return dict(conn.execute(text("""
        SELECT
            COUNT(*) AS users,
            COUNT(*) FILTER (
                WHERE role = 'admin'
            ) AS administrators,
            COUNT(*) FILTER (
                WHERE google_sub IS NOT NULL
            ) AS google_accounts
        FROM floodwatch_users
    """)).mappings().one())

def check_email_conflicts(conn):
    conflicts = conn.execute(text("""
        SELECT COUNT(*)
        FROM (
            SELECT LOWER(BTRIM(email))
            FROM floodwatch_users
            WHERE email_verified IS TRUE
            GROUP BY LOWER(BTRIM(email))
            HAVING COUNT(*) > 1
        ) duplicates
    """)).scalar_one()

    if conflicts:
        raise RuntimeError("Verified accounts contain duplicate email addresses. Resolve those records before migrating.")

def migrate(conn):
    check_email_conflicts(conn)

    conn.execute(text("""
        ALTER TABLE floodwatch_users
        ADD COLUMN IF NOT EXISTS password_hash TEXT
    """))

    conn.execute(text("""
        ALTER TABLE floodwatch_users
        ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ
    """))

    conn.execute(text("""
        ALTER TABLE floodwatch_users
        ALTER COLUMN google_sub DROP NOT NULL
    """))

    conn.execute(text("""
        ALTER TABLE floodwatch_users
        DROP CONSTRAINT IF EXISTS
            floodwatch_users_account_status_check
    """))

    conn.execute(text("""
        ALTER TABLE floodwatch_users
        ADD CONSTRAINT floodwatch_users_account_status_check
        CHECK (
            account_status IN (
                'active',
                'suspended',
                'pending_verification'
            )
        )
    """))

    conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'floodwatch_users'::regclass
                  AND conname = 'floodwatch_users_identity_required'
            ) THEN
                ALTER TABLE floodwatch_users
                ADD CONSTRAINT floodwatch_users_identity_required
                CHECK (
                    google_sub IS NOT NULL
                    OR password_hash IS NOT NULL
                );
            END IF;
        END
        $$
    """))

    conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_floodwatch_verified_email_ci
        ON floodwatch_users (LOWER(BTRIM(email)))
        WHERE email_verified IS TRUE
    """))

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS
            idx_floodwatch_local_email
        ON floodwatch_users (LOWER(BTRIM(email)))
        WHERE password_hash IS NOT NULL
    """))


def verify(conn):
    columns = {
        row["column_name"]: row["is_nullable"]
        for row in conn.execute(text("""
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'floodwatch_users'
              AND table_schema = current_schema()
        """)).mappings()
    }

    assert columns.get("google_sub") == "YES"
    assert columns.get("password_hash") == "YES"
    assert columns.get("password_changed_at") == "YES"

    constraints = {
        row["conname"]: row["definition"]
        for row in conn.execute(text("""
            SELECT
                conname,
                pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = 'floodwatch_users'::regclass
        """)).mappings()
    }

    assert "pending_verification" in constraints.get("floodwatch_users_account_status_check", "")
    assert "floodwatch_users_identity_required" in constraints

    indexes = {
        row["indexname"]
        for row in conn.execute(text("""
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'floodwatch_users'
        """)).mappings()
    }

    assert "idx_floodwatch_verified_email_ci" in indexes
    assert "idx_floodwatch_local_email" in indexes

    print("Nullable Google identifier: OK")
    print("Password hash column: OK")
    print("Password change timestamp: OK")
    print("Pending verification status: OK")
    print("Authentication identity constraint: OK")
    print("Email indexes: OK")

def main():
    parser = argparse.ArgumentParser(description="Migrate FloodWatch accounts for password authentication.")

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
                before = account_counts(conn)

                print("Nairobi FloodWatch - Account Migration")
                print("=" * 50)
                print("Existing users:", before["users"])
                print("Existing administrators:", before["administrators"])
                print("Existing Google accounts:", before["google_accounts"])

                if not args.verify:
                    migrate(conn)

                verify(conn)
                after = account_counts(conn)

                if before != after:
                    raise RuntimeError("Migration unexpectedly changed account counts.")

                if args.apply:
                    transaction.commit()
                    print("Migration: COMMITTED")

                else:
                    transaction.rollback()

                    if args.dry_run:
                        print("Migration dry run: ROLLED BACK")
                    else:
                        print("Schema verification: PASSED")

                print("Existing account counts: PRESERVED")

            finally:
                if transaction.is_active:
                    transaction.rollback()

    finally:
        engine.dispose()

if __name__ == "__main__":
    main()
