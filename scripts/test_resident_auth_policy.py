# Resident verification lifecycle regression test.
# Uses a temporary test account and rolls back all database changes. Does not send emails or modify existing users.
import os
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from sqlalchemy import text


# Load local FloodWatch secrets before importing application code.
load_dotenv(Path(os.environ["LOCALAPPDATA"]) / "NairobiFloodWatch" / "secrets.env",  override=True)

from src.operational.persistence import create_database_engine
from src.auth.password_service import register_local_user
from src.auth.resident_verification import (start_resident_grace_period, get_resident_verification_status)
from src.auth.resident_otp_service import (issue_resident_email_otp, verify_resident_email_otp, OTPRequestLimited)


def main():
    print("FloodWatch Resident Authentication Regression")
    print("=" * 50)

    engine = create_database_engine()

    try:
        with engine.connect() as conn:
            # Every operation stays inside this transaction.
            transaction = conn.begin()
            try:
                # 1. Create temporary Resident
                test_email = (f"otp-test-{uuid4().hex}@example.com")
                user = register_local_user(conn, email=test_email, password="FloodWatch@Test2026", display_name="Temporary OTP Test")
                user_id = user["id"]

                start_resident_grace_period(conn, user_id)

                assert user["role"] == "resident"
                assert user["account_status"] == "active"
                assert user["email_verified"] is False

                print("1. Resident registration: PASSED")


                # 2. Initial verification grace period
                status = get_resident_verification_status(conn, user_id, enforce_deadline=True)

                assert status["status"] == "grace_period"
                assert status["can_access_dashboard"]
                assert not status["can_download_csv"]

                print("2. Initial 48-hour grace period: PASSED")

                # 3. Simulate expiry of 48 hours
                conn.execute(text("""
                    UPDATE floodwatch_resident_verification
                    SET grace_started_at =
                        NOW() - INTERVAL '49 hours'
                    WHERE user_id = CAST(:user_id AS UUID)
                """), {
                    "user_id": user_id,
                })

                expired = get_resident_verification_status(conn, user_id, enforce_deadline=True)

                assert expired["status"] == "verification_required"
                assert not expired["can_access_dashboard"]
                assert not expired["can_download_csv"]

                print("3. Expired grace period: PASSED")

                # 4. Issue the first OTP
                first = issue_resident_email_otp(conn, user_id)
                code = first["code"]

                assert len(code) == 6
                assert code.isdigit()

                print("4. Six-digit OTP generation: PASSED")

                # 5. Five incorrect attempts
                wrong_code = f"{(int(code) + 1) % 1000000:06d}"

                for _ in range(5):
                    assert not verify_resident_email_otp(conn, user_id, wrong_code)

                # The correct code must now be unusable.
                assert not verify_resident_email_otp(conn, user_id, code)

                print("5. Five-attempt lockout: PASSED")

                # 6. Issue another OTP after cooldown
                conn.execute(text("""
                    UPDATE floodwatch_resident_email_otps
                    SET last_sent_at =
                        NOW() - INTERVAL '61 seconds'
                    WHERE user_id = CAST(:user_id AS UUID)
                """), {
                    "user_id": user_id,
                })

                second = issue_resident_email_otp(conn, user_id)

                # Simulate expiration without waiting.
                conn.execute(text("""
                    UPDATE floodwatch_resident_email_otps
                    SET expires_at =
                        NOW() - INTERVAL '1 second'
                    WHERE user_id = CAST(:user_id AS UUID)
                """), {
                    "user_id": user_id,
                })

                assert not verify_resident_email_otp(conn, user_id, second["code"])

                print("6. Expired OTP rejection: PASSED")

                # 7. Test hourly send restriction
                conn.execute(text("""
                    UPDATE floodwatch_resident_email_otps
                    SET last_sent_at =
                        NOW() - INTERVAL '61 seconds'
                    WHERE user_id = CAST(:user_id AS UUID)
                """), {
                    "user_id": user_id,
                })

                issue_resident_email_otp(conn, user_id)

                # The fourth request within the hour must be rejected.
                conn.execute(text("""
                    UPDATE floodwatch_resident_email_otps
                    SET last_sent_at =
                        NOW() - INTERVAL '61 seconds'
                    WHERE user_id = CAST(:user_id AS UUID)
                """), {
                    "user_id": user_id,
                })

                limited = False

                try:
                    issue_resident_email_otp(conn, user_id)

                except OTPRequestLimited:
                    limited = True
                assert limited

                print("7. Hourly OTP rate limit: PASSED")

                # 8. Simulate a new hourly window
                conn.execute(text("""
                    UPDATE floodwatch_resident_email_otps
                    SET
                        window_started_at =
                            NOW() - INTERVAL '61 minutes',
                        last_sent_at =
                            NOW() - INTERVAL '61 seconds'
                    WHERE user_id = CAST(:user_id AS UUID)
                """), {
                    "user_id": user_id,
                })

                final_otp = issue_resident_email_otp(conn, user_id)

                # 9. Complete email verification
                assert verify_resident_email_otp(conn, user_id, final_otp["code"])
                verified = get_resident_verification_status(conn, user_id, enforce_deadline=True)
                assert verified["status"] == "verified"
                assert verified["email_verified"]
                assert verified["can_access_dashboard"]
                assert verified["can_download_csv"]

                print("8. Successful OTP verification: PASSED")
                
                # 10. Reject replay of used OTP
                assert not verify_resident_email_otp(conn, user_id, final_otp["code"])

                print("9. OTP replay prevention: PASSED")
                print("=" * 50)
                print("All Resident policy tests: PASSED")

            finally:
                # Never commit the temporary test account.
                transaction.rollback()
                print("Temporary test data: ROLLED BACK")

    finally:
        engine.dispose()

if __name__ == "__main__":
    main()
