from sqlalchemy import create_engine, text
from config.settings import DATABASE_URL

def check_database_connection():
    engine = create_engine(DATABASE_URL)

    try:
        with engine.connect() as connection:
            database = connection.execute(text("SELECT current_database();")).scalar()
            version = connection.execute(text("SELECT version();")).scalar()

            print("Nairobi FloodWatch Database Check")
            print("-" * 40)
            print(f"Connection: OK")
            print(f"Database: {database}")
            print(f"PostgreSQL Version: {version}")

    except Exception as exc:
        print("Nairobi FloodWatch Database Check")
        print("-" * 40)
        print(f"Connection: FAILED")
        print(f"Error: {exc}")
        raise

if __name__ == "__main__":
    check_database_connection()