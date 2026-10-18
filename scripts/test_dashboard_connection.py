from config.settings import get_settings
from src.dashboard.database import (check_database_connection, get_missing_tables, get_table_counts)

def main():
    print("Nairobi FloodWatch Dashboard Connection Test")
    print("=" * 50)

    #validate application configuration
    settings = get_settings()

    print(f"Application: {settings.app_name}")
    print(f"Environment: {settings.environment}")
    print(f"Timezone: {settings.timezone}")
    print("Database URL: configured")
    print()

    #verify PostgreSQL connection without displaying credentials
    print("Testing PostgreSQL connection...")

    status = check_database_connection()

    print(f"Database: {status['database_name']}")
    print(f"Database user: {status['database_user']}")
    print(f"Server timestamp: {status['server_time']}")
    print("PostgreSQL connection: OK")
    print()

    #confirm all required historical and operational tables exist
    print("Validating database schema...")

    missing_tables = get_missing_tables()

    if missing_tables:
        raise RuntimeError(f"Missing required tables: {missing_tables}")

    print("Required database tables: OK")
    print()

    #display actual counts to confirm the dashboard can retrieve data
    print("Database record counts: ")

    counts = get_table_counts()

    for table_name, count in counts.items():
        print(f"{table_name}: {count:,}")

    if counts["location"] == 0:
        raise RuntimeError("The location table contains no monitoring points")

    if counts["flood_risk_prediction"] == 0:
        raise RuntimeError("No operational predictions are available")

    print()
    print("Dashboard database integration: OK")

if __name__ == "__main__":
    main()
    