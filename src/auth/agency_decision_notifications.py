import logging
from functools import lru_cache
from uuid import uuid4
from sqlalchemy import text

from src.auth.email_delivery import send_agency_decision_email
from src.operational.persistence import create_database_engine

logger = logging.getLogger("floodwatch.auth.agency_decision_notifications")

MAX_ATTEMPTS = 8

@lru_cache(maxsize=1)
def _engine():
    return create_database_engine()

def ensure_agency_email_outbox_schema(engine):
    # Create the notification queue.
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS
                floodwatch_agency_email_outbox (
                    id UUID PRIMARY KEY,

                    agency_request_id UUID NOT NULL UNIQUE
                        REFERENCES floodwatch_agency_requests(id)
                        ON DELETE CASCADE,

                    decision TEXT NOT NULL CHECK (
                        decision IN ('approved', 'rejected')
                    ),

                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK (
                            state IN (
                                'pending',
                                'sending',
                                'retry',
                                'sent',
                                'failed'
                            )
                        ),

                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    claimed_at TIMESTAMPTZ,
                    sent_at TIMESTAMPTZ,
                    last_error_type TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_floodwatch_agency_email_outbox_queue
            ON floodwatch_agency_email_outbox (
                state,
                available_at
            )
        """))

def queue_agency_decision_notification(conn, agency_request_id, decision):
    #Add a notification inside the existing approval transaction.
    #The unique request ID prevents duplicate queue records.
    if decision not in {"approved", "rejected"}:
        raise ValueError("Invalid agency decision.")

    conn.execute(
        text("""
            INSERT INTO floodwatch_agency_email_outbox (
                id,
                agency_request_id,
                decision
            )
            VALUES (
                CAST(:id AS UUID),
                CAST(:request_id AS UUID),
                :decision
            )
            ON CONFLICT (agency_request_id) DO NOTHING
        """),
        {
            "id": str(uuid4()),
            "request_id": str(agency_request_id),
            "decision": decision,
        },
    )

def _claim_notification(request_id=None):
    # Atomically claim one pending notification.
    # SKIP LOCKED prevents competing workers from claiming the same message simultaneously.
    #Abandoned claims may be retried after 10 minutes.
    with _engine().begin() as conn:
        row = conn.execute(
            text("""
                WITH candidate AS (
                    SELECT id
                    FROM floodwatch_agency_email_outbox

                    WHERE (
                        :request_id IS NULL
                        OR agency_request_id =
                            CAST(:request_id AS UUID)
                    )

                    AND attempts < :max_attempts

                    AND (
                        (
                            state IN ('pending', 'retry')
                            AND available_at <= NOW()
                        )

                        OR (
                            state = 'sending'
                            AND claimed_at <
                                NOW() - INTERVAL '10 minutes'
                        )
                    )

                    ORDER BY available_at, created_at

                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )

                UPDATE floodwatch_agency_email_outbox AS o
                SET
                    state = 'sending',
                    claimed_at = NOW(),
                    attempts = o.attempts + 1

                FROM candidate

                WHERE o.id = candidate.id

                RETURNING
                    o.id,
                    o.agency_request_id,
                    o.decision,
                    o.attempts
            """),
            {
                "request_id": request_id,
                "max_attempts": MAX_ATTEMPTS,
            },
        ).mappings().first()

        return dict(row) if row else None


def _get_recipient(agency_request_id):
    # Look up current applicant details from existing tables.
    with _engine().connect() as conn:
        row = conn.execute(
            text("""
                SELECT
                    u.email,
                    u.display_name,
                    r.organization_name,
                    r.status

                FROM floodwatch_agency_requests r

                JOIN floodwatch_users u
                    ON u.id = r.user_id

                WHERE r.id = CAST(:request_id AS UUID)
            """),
            {"request_id": str(agency_request_id)},
        ).mappings().first()

        return dict(row) if row else None

def _mark_sent(notification):
    with _engine().begin() as conn:
        conn.execute(
            text("""
                UPDATE floodwatch_agency_email_outbox

                SET
                    state = 'sent',
                    sent_at = NOW(),
                    claimed_at = NULL,
                    last_error_type = NULL

                WHERE id = CAST(:id AS UUID)
                  AND state = 'sending'
                  AND attempts = :attempts
            """),
            {
                "id": str(notification["id"]),
                "attempts": notification["attempts"],
            },
        )

def _mark_failed(notification, error_type):
    attempts = notification["attempts"]

    # Exponential retry delays, capped at one hour.
    delay_seconds = min(3600, 60 * (2 ** min(attempts - 1, 6)))
    next_state = ("failed" if attempts >= MAX_ATTEMPTS else "retry")

    with _engine().begin() as conn:
        conn.execute(
            text("""
                UPDATE floodwatch_agency_email_outbox

                SET
                    state = :state,
                    claimed_at = NULL,
                    last_error_type = :error_type,
                    available_at =
                        NOW() + (:delay * INTERVAL '1 second')

                WHERE id = CAST(:id AS UUID)
                  AND state = 'sending'
                  AND attempts = :attempts
            """),
            {
                "id": str(notification["id"]),
                "attempts": attempts,
                "state": next_state,
                "error_type": error_type,
                "delay": delay_seconds,
            },
        )

def deliver_one_decision_email(request_id=None):
    #Process one eligible notification.
    #Returns:
        #True  - SMTP accepted the message
        #False - Delivery attempt failed
        #None  - No eligible notification available

    notification = _claim_notification(request_id)

    if notification is None:
        return None

    try:
        recipient = _get_recipient(notification["agency_request_id"])

        if recipient is None:
            raise RuntimeError("Agency applicant record missing.")

        if recipient["status"] != notification["decision"]:
            raise RuntimeError("Agency decision no longer matches.")

        send_agency_decision_email(recipient=recipient["email"], applicant_name=recipient["display_name"], organization_name=recipient["organization_name"], decision=notification["decision"])

    except Exception as exc:
        # Log the error type without exposing recipient data.
        logger.exception("Agency decision email attempt failed: %s", type(exc).__name__,)
        _mark_failed(notification, type(exc).__name__,)

        return False

    _mark_sent(notification)
    return True

def get_notification_counts():
    #Return notification statuses for operational checks.
    with _engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT state, COUNT(*) AS total
                FROM floodwatch_agency_email_outbox
                GROUP BY state
                ORDER BY state
            """)
        ).mappings().all()

        return {row["state"]: row["total"] for row in rows}
