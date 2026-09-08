from src.database.base import Base
import src.database.models

EXPECTED_TABLES ={
    "location",
    "data_source",
    "weather_data",
    "environmental_feature",
    "flood_event",
    "model",
    "user_account",
    "flood_risk_prediction",
    "flood_alert",
    "alert_recipient",
    
}
#expected columns
EXPECTED_COLUMNS = {
    "location": {
        "location_id",
        "name",
        "administrative_level",
        "latitude",
        "longitude",
        "created_at",
    },

    "data_source": {
        "source_id",
        "name",
        "source_type",
        "description",
        "source_url",
        "update_frequency",
        "is_active",
        "created_at",
    },

    "weather_data": {
        "weather_id",
        "location_id",
        "source_id",
        "timestamp",
        "temperature",
        "humidity",
        "rainfall",
        "wind_speed",
        "pressure",
        "soil_moisture_layer1",
        "soil_moisture_layer2",
        "created_at",
    },

    "environmental_feature": {
        "feature_id",
        "location_id",
        "source_id",
        "feature_name",
        "feature_value",
        "unit",
        "effective_date",
        "created_at",
    },

    "flood_event": {
        "event_id",
        "location_id",
        "source_id",
        "source_event_id",
        "event_type",
        "title",
        "description",
        "start_time",
        "end_time",
        "severity_level",
        "verification_status",
        "created_at",
    },

    "model": {
        "model_id",
        "name",
        "model_type",
        "description",
        "version",
        "training_start_date",
        "training_end_date",
        "performance_metrics",
        "is_active",
        "created_at",
    },

    "user_account": {
        "user_id",
        "full_name",
        "email",
        "password_hash",
        "phone",
        "role",
        "organization",
        "is_active",
        "created_at",
    },

    "flood_risk_prediction": {
        "prediction_id",
        "location_id",
        "model_id",
        "prediction_time",
        "forecast_horizon_hours",
        "risk_level",
        "risk_score",
        "prediction_data",
        "confidence_score",
        "created_at",
    },

    "flood_alert": {
        "alert_id",
        "prediction_id",
        "alert_level",
        "title",
        "message",
        "instructions",
        "issued_at",
        "expires_at",
        "status",
        "created_at",
    },

    "alert_recipient": {
        "recipient_id",
        "alert_id",
        "user_id",
        "channel",
        "delivery_status",
        "sent_at",
        "delivered_at",
    },
}
#schema check
def check_schema():
    actual_tables = set(Base.metadata.tables.keys())
    print("Nairobi FloodWatch Schema Check")
    print("=" * 40)

    #check tables
    print("\n TABLE CHECK")
    print("-" * 40)

    for table in sorted(EXPECTED_TABLES):
        status = "OK" if table in actual_tables else "MISSING"
        print(f"{table:<35} {status}")

    missing_tables = EXPECTED_TABLES - actual_tables
    unexpected_tables = actual_tables - EXPECTED_TABLES

    print("\n COLUMN CHECK")
    print("-" * 40)

    column_errors = False

    for table_name in sorted(EXPECTED_TABLES):
        if table_name not in actual_tables:
            continue

        table = Base.metadata.tables[table_name]
        actual_columns = { column.name for column in table.columns }

        expected_columns = EXPECTED_COLUMNS[table_name]

        missing_columns = expected_columns - actual_columns
        unexpected_columns = actual_columns - expected_columns

        if not missing_columns and not unexpected_columns:
            print(
                f"{table_name:<35} OK"
                f"({len(actual_columns)} columns)"
            )
            continue
        column_errors = True

        print(f"{table_name:<35} ERROR")

        if missing_columns:
            print(
                " Missing columns: "
                f"{sorted(missing_columns)}"
            )
        if unexpected_columns:
            print(
                " Unexpected columns: "
                f"{sorted(unexpected_columns)}"
            )

        print("\nSUMMARY")
        print("-" * 40)

        print(f"Expected tables: {len(EXPECTED_TABLES)}")
        print(f"Defined tables: {len(actual_tables)}")

        errors_found = False

        if missing_tables:
            errors_found = True
            print(f"Missing tables: {sorted(missing_tables)}")
        if unexpected_tables:
            errors_found = True
            print(f"Unexpected tables: {sorted(unexpected_tables)}")

        if column_errors:
            errors_found = True
            print("One or more tables contain missing or unexpected columns.")

        if errors_found:
            print("\nSchema definition Failed")
            raise SystemExit(1)

        print("\nSchema definition OK")

if __name__ == "__main__":
    check_schema()

        

    