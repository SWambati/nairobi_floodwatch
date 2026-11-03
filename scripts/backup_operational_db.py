import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.operational.persistence import create_database_engine

def locate_pg_dump():
    executable = shutil.which("pg_dump")
    if executable:
        return executable

    # Common Windows PostgreSQL installation directory.
    root = Path("C:/Program Files/PostgreSQL")
    if root.exists():
        candidates = sorted(root.glob("*/bin/pg_dump.exe"), reverse=True)

        if candidates:
            return str(candidates[0])

    raise RuntimeError("pg_dump was not found. Install PostgreSQL client tools or add its bin directory to PATH.")

def main():
    engine = create_database_engine()
    try:
        url = engine.url
        backup_dir = (Path.home() / "FloodWatch-Private-Backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d_%H%M%S")
        output_file = (backup_dir / f"floodwatch_before_e2e03_{timestamp}.dump")
        environment = os.environ.copy()
        environment["PGHOST"] = url.host or "localhost"
        environment["PGPORT"] = str(url.port or 5432)
        environment["PGUSER"] = url.username or ""
        environment["PGDATABASE"] = url.database or ""

        if url.password is not None:
            environment["PGPASSWORD"] = url.password

        if url.query.get("sslmode"):
            environment["PGSSLMODE"] = str(url.query["sslmode"])

        command = [locate_pg_dump(), "--format=custom", "--no-owner", "--no-privileges", "--file", str(output_file), "--dbname", environment["PGDATABASE"]]

        print("Backing up FloodWatch PostgreSQL...")
        result = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            output_file.unlink(missing_ok=True)
            raise RuntimeError("PostgreSQL backup failed:\n" + result.stderr.strip())

        if not output_file.is_file() or (output_file.stat().st_size == 0):
            raise RuntimeError("Backup was not created successfully.")

        print("\nPOSTGRESQL BACKUP: PASS")
        print("Backup:", output_file)
        print("Size:", f"{output_file.stat().st_size / 1048576:.2f} MB")
        print("\nKeep this backup private. It contains database records.")

    finally:
        engine.dispose()

if __name__ == "__main__":
    main()
