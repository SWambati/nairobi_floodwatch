# Trusted CLI for bootstrapping the first FloodWatch administrator.
import argparse
import json
from uuid import UUID, uuid4
from sqlalchemy import text

from src.auth.account_service import upsert_verified_google_user
from src.operational.persistence import create_database_engine


def bootstrap_first_admin(conn, user_id, reason):
    user_id = str(UUID(str(user_id)))
    reason = str(reason).strip()

    if not 12 <= len(reason) <= 500:
        raise ValueError("Provisioning reason must contain 12–500 characters.")

    # Serialize role changes during the initial bootstrap transaction.
    conn.execute(text("""
        LOCK TABLE floodwatch_users
        IN SHARE ROW EXCLUSIVE MODE
    """))

    existing_admins = conn.execute(text("""
        SELECT COUNT(*)
        FROM floodwatch_users
        WHERE role = 'admin'
    """)).scalar_one()

    if existing_admins:
        raise PermissionError("Bootstrap refused: an administrator already exists.")

    user = conn.execute(text("""
        SELECT id, role, account_status, email_verified, google_sub
        FROM floodwatch_users
        WHERE id = CAST(:id AS UUID)
        FOR UPDATE
    """), {"id": user_id}).mappings().first()

    if not user:
        raise ValueError("Target account does not exist.")

    if user["role"] != "resident":
        raise PermissionError("Only resident accounts are eligible.")

    if user["account_status"] != "active":
        raise PermissionError("The account must be active.")

    if not user["email_verified"] or not user["google_sub"]:
        raise PermissionError("A verified Google identity is required.")

    updated = conn.execute(text("""
        UPDATE floodwatch_users
        SET role = 'admin',
            role_updated_at = NOW(),
            updated_at = NOW()
        WHERE id = CAST(:id AS UUID)
          AND role = 'resident'
          AND account_status = 'active'
          AND email_verified = TRUE
        RETURNING id
    """), {"id": user_id}).scalar_one_or_none()

    if not updated:
        raise PermissionError("Administrator provisioning was refused.")

    conn.execute(text("""
        INSERT INTO floodwatch_auth_audit (
            id, actor_user_id, target_user_id,
            action, previous_role, new_role, details
        )
        VALUES (
            CAST(:audit_id AS UUID),
            NULL,
            CAST(:user_id AS UUID),
            'first_admin_provisioned',
            'resident',
            'admin',
            CAST(:details AS JSONB)
        )
    """), {
        "audit_id": str(uuid4()),
        "user_id": user_id,
        "details": json.dumps({
            "method": "trusted_local_cli",
            "reason": reason,
        }),
    })

    return user_id


def show_status(engine):
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                COUNT(*) AS total_users,
                COUNT(*) FILTER (
                    WHERE role = 'admin'
                ) AS administrators,
                COUNT(*) FILTER (
                    WHERE role = 'resident'
                      AND account_status = 'active'
                      AND email_verified = TRUE
                ) AS eligible_residents
            FROM floodwatch_users
        """)).mappings().one()

    print("\nNairobi FloodWatch — Administrator Provisioning")
    print("=" * 50)
    print("Registered users:", result["total_users"])
    print("Administrators:", result["administrators"])
    print("Eligible resident accounts:", result["eligible_residents"])

    if result["administrators"]:
        print("Bootstrap status: CLOSED")
    else:
        print("Bootstrap status: AVAILABLE")


def run_self_test(engine):
    # Exercise provisioning in a transaction that is always rolled back.

    test_sub = f"bootstrap-test-{uuid4()}"

    with engine.connect() as conn:
        transaction = conn.begin()

        try:
            user = upsert_verified_google_user(
                conn,
                google_sub=test_sub,
                email="bootstrap-test@example.invalid",
                email_verified=True,
                display_name="Bootstrap Test Account",
            )

            user_id = str(user["id"])

            bootstrap_first_admin(
                conn,
                user_id,
                "Temporary transaction-only provisioning test.",
            )

            role = conn.execute(text("""
                SELECT role
                FROM floodwatch_users
                WHERE id = CAST(:id AS UUID)
            """), {"id": user_id}).scalar_one()

            assert role == "admin"

            audit_count = conn.execute(text("""
                SELECT COUNT(*)
                FROM floodwatch_auth_audit
                WHERE target_user_id = CAST(:id AS UUID)
                  AND action = 'first_admin_provisioned'
            """), {"id": user_id}).scalar_one()

            assert audit_count == 1

            try:
                bootstrap_first_admin(
                    conn,
                    user_id,
                    "Attempt to repeat the first administrator bootstrap.",
                )
            except PermissionError:
                print("Duplicate bootstrap blocked: OK")
            else:
                raise AssertionError("Repeated bootstrap was not blocked.")

            print("Role assignment: OK")
            print("Audit logging: OK")
            print("Provisioning tests: PASSED")

        finally:
            transaction.rollback()
            print("Temporary test changes: ROLLED BACK")


def provision_account(engine, user_id, reason):
    user_id = str(UUID(str(user_id)))

    with engine.connect() as conn:
        target = conn.execute(text("""
            SELECT email, role, account_status, email_verified
            FROM floodwatch_users
            WHERE id = CAST(:id AS UUID)
        """), {"id": user_id}).mappings().first()

    if not target:
        raise ValueError("The specified user account does not exist.")

    print("\nAdministrator Provisioning Request")
    print("=" * 50)
    print("User ID:", user_id)
    print("Email:", target["email"])
    print("Current role:", target["role"])
    print("Account status:", target["account_status"])
    print("Email verified:", target["email_verified"])

    print("\nWARNING: This grants system administrator privileges.")

    confirmation = f"PROVISION {user_id}"
    answer = input(f"\nType '{confirmation}' to confirm:\n> ").strip()

    if answer != confirmation:
        print("Provisioning cancelled.")
        return

    with engine.begin() as conn:
        bootstrap_first_admin(conn, user_id, reason)

    print("\nAdministrator successfully provisioned.")
    print("Role: admin")
    print("Security audit record: CREATED")


def main():
    parser = argparse.ArgumentParser(description="Bootstrap the first Nairobi FloodWatch administrator.")

    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--status", action="store_true", help="Inspect account counts and bootstrap availability.",)
    actions.add_argument("--self-test", action="store_true", help="Test provisioning without committing changes.",)
    actions.add_argument("--user-id", metavar="UUID", help="UUID of an existing verified Google user account.",)
    parser.add_argument("--reason", help="Required justification for real administrator provisioning.",)

    args = parser.parse_args()

    if args.user_id and not args.reason:
        parser.error("--reason is required with --user-id")

    engine = create_database_engine()

    try:
        if args.status:
            show_status(engine)

        elif args.self_test:
            run_self_test(engine)

        else:
            provision_account(engine, args.user_id, args.reason)

    except (ValueError, PermissionError) as exc:
        raise SystemExit(f"Provisioning refused: {exc}") from exc

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
