import sys
from sqlalchemy import text
from src.operational.persistence import create_database_engine

INDEXES = {
    "uq_prediction_location_model_nairobi_day": """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction_location_model_nairobi_day
        ON flood_risk_prediction (
            location_id,
            model_id,
            ((prediction_time AT TIME ZONE 'Africa/Nairobi')::date)
        )
    """,
    "uq_flood_alert_prediction": """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_flood_alert_prediction
        ON flood_alert (prediction_id)
        WHERE prediction_id IS NOT NULL
    """,
}

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — DATABASE HARDENING")
    print("=" * 50)

    engine = create_database_engine()

    try:
        with engine.begin() as conn:
            duplicate_predictions = conn.execute(text("""
                SELECT COUNT(*) FROM (
                    SELECT location_id, model_id,
                           (prediction_time AT TIME ZONE 'Africa/Nairobi')::date
                    FROM flood_risk_prediction
                    GROUP BY 1, 2, 3
                    HAVING COUNT(*) > 1
                ) duplicates
            """)).scalar_one()

            duplicate_alerts = conn.execute(text("""
                SELECT COUNT(*) FROM (
                    SELECT prediction_id FROM flood_alert
                    WHERE prediction_id IS NOT NULL
                    GROUP BY prediction_id
                    HAVING COUNT(*) > 1
                ) duplicates
            """)).scalar_one()

            print("Duplicate prediction groups:", duplicate_predictions)
            print("Duplicate alert groups:", duplicate_alerts)

            if duplicate_predictions or duplicate_alerts:
                raise RuntimeError("Existing duplicate records must be resolved first.")

            for name, statement in INDEXES.items():
                conn.execute(text(statement))
                print(f"[PASS] Unique index applied: {name}")

        with engine.connect() as conn:
            for name in INDEXES:
                unique = conn.execute(text("""
                    SELECT i.indisunique
                    FROM pg_index i
                    JOIN pg_class c ON c.oid = i.indexrelid
                    WHERE c.oid = to_regclass(:name)
                """), {"name": name}).scalar_one_or_none()

                if unique is not True:
                    raise RuntimeError(f"Unique index not verified: {name}")

            print("[PASS] Both indexes verified in PostgreSQL")

        print("\nDATABASE HARDENING RESULT: PASS")

    finally:
        engine.dispose()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nDATABASE HARDENING RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
