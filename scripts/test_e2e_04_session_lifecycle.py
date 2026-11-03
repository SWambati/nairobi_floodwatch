import hashlib
import secrets
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import os
from dotenv import load_dotenv
from sqlalchemy import text
from unittest.mock import patch

# Load the existing local development configuration.
secrets_path = (Path(os.environ.get("LOCALAPPDATA", "")) / "NairobiFloodWatch" / "secrets.env")

if secrets_path.is_file():
    load_dotenv(secrets_path, override=False)

from src.auth.password_service import register_local_user
from src.operational.persistence import create_database_engine
import src.auth.google_oauth as auth

class TransactionBoundEngine:
    #Force session reads and revocations to use the test's uncommitted database transaction.
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return nullcontext(self.connection)

    def begin(self):
        return nullcontext(self.connection)

def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}", flush=True)

def insert_session(connection, user_id, expires_at):
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    connection.execute(
        text("""
            INSERT INTO floodwatch_user_sessions (
                token_hash,
                user_id,
                expires_at,
                auth_method
            )
            VALUES (
                :token_hash,
                CAST(:user_id AS UUID),
                :expires_at,
                'password'
            )
        """),
        {
            "token_hash": token_hash,
            "user_id": str(user_id),
            "expires_at": expires_at,
        },
    )
    return token, token_hash

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-04G")
    print("=" * 50)
    print("Mode: ROLLBACK-ONLY SESSION TEST")

    engine = create_database_engine()
    test_user_id = None

    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                # Create a temporary Resident account.
                email = (f"session-test-{uuid4().hex}@example.com")

                user = register_local_user(connection, email=email, password="FloodWatch@Test2026", display_name="Session Lifecycle Test")
                test_user_id = str(user["id"])
                check(user["role"] == "resident", "Temporary Resident account created")
                bound_engine = TransactionBoundEngine(connection)

                # Patch only the session module's engine. No production code or database schema changes.
                with patch.object(auth, "_engine", return_value=bound_engine):
                    #Valid session
                    valid_token, valid_hash = insert_session(connection, test_user_id, datetime.now(timezone.utc) + timedelta(hours=2))
                    found = auth._find_session_user(valid_token)
                    check(found is not None and str(found["id"]) == test_user_id, "Valid session accepted")

                    #Unknown session
                    unknown = secrets.token_urlsafe(48)
                    check(auth._find_session_user(unknown) is None, "Unknown session rejected")

                    #Server-side revocation
                    auth._revoke_user_session(valid_token)
                    check(auth._find_session_user(valid_token) is None, "Revoked session rejected")

                    revoked = connection.execute(
                        text("""
                            SELECT revoked_at
                            FROM floodwatch_user_sessions
                            WHERE token_hash = :token_hash
                        """),
                        {"token_hash": valid_hash},
                    ).scalar_one()

                    check(revoked is not None, "Session revocation stored in PostgreSQL")

                    #Logout audit
                    audit_count = connection.execute(
                        text("""
                            SELECT COUNT(*)
                            FROM floodwatch_auth_audit
                            WHERE target_user_id =
                                CAST(:user_id AS UUID)
                              AND action = 'user_logged_out'
                        """),
                        {"user_id": test_user_id},
                    ).scalar_one()

                    check(audit_count >= 1, "Session revocation recorded in audit log")

                    #Expired session
                    expired_token, _ = insert_session(connection, test_user_id, datetime.now(timezone.utc) - timedelta(hours=1))
                    check(auth._find_session_user(expired_token) is None, "Expired session rejected")

            finally:
                transaction.rollback()
                print("\n[INFO] Temporary session and user records rolled back")

        # Check cleanup using a new connection.
        if test_user_id is not None:
            with engine.connect() as connection:
                remaining = connection.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM floodwatch_users
                        WHERE id = CAST(:user_id AS UUID)
                    """),
                    {"user_id": test_user_id},
                ).scalar_one()

                check(remaining == 0, "Temporary account was not persisted")

        print("\n" + "=" * 50)
        print("E2E-04G RESULT: PASS")
        print("=" * 50)
        print("Database-backed session validation, revocation and expiration verified.")
        print("No existing accounts or sessions changed.")
    finally:
        engine.dispose()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-04G RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
