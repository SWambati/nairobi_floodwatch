import os
from pathlib import Path
from dotenv import load_dotenv

secrets_file = (Path(os.environ["LOCALAPPDATA"]) / "NairobiFloodWatch" / "secrets.env")
load_dotenv(secrets_file, override=False)

from src.auth.agency_decision_notifications import (deliver_one_decision_email, get_notification_counts)

def main():
    print("\nNairobi FloodWatch - Agency Email Notifications")
    print("=" * 50)

    accepted = 0
    failed = 0

    # Bounded processing protects against endless retries.
    for _ in range(25):
        result = deliver_one_decision_email()

        if result is None:
            break

        if result is True:
            accepted += 1
        else:
            failed += 1

    print("Accepted by SMTP:", accepted)
    print("Failed attempts:", failed)

    print("\nNotification queue:")

    for state, count in get_notification_counts().items():
        print(f" {state}: {count}")


if __name__ == "__main__":
    main()
