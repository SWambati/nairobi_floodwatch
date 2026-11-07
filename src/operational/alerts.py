
from datetime import datetime, timedelta, timezone

import math
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# Alert configuration
ALERT_VALIDITY_HOURS = 24
VALID_RISK_LEVELS = {"LOW", "MODERATE", "HIGH"}


# Alert content
def build_alert_content(location_name: str, risk_level: str, risk_score: float) -> dict:
    # Convert model-derived risk classifications into user-facing advisories.
    # LOW does not create an active alert, MODERATE creates an advisory, while HIGH creates a stronger warning.

    risk_level = str(risk_level).upper()

    if risk_level not in VALID_RISK_LEVELS:
        raise ValueError(f"Unsupported flood-risk level: {risk_level}")

    # Validate the numeric risk score before creating an advisory.
    try:
        risk_score = float(risk_score)
    except (TypeError, ValueError):
        raise ValueError("Flood-risk score must be numeric.") from None
    
    if not math.isfinite(risk_score):
        raise ValueError("Flood-risk score must be finite.")
    
    if not 0.0 <= risk_score <= 1.0:
        raise ValueError("Flood-risk score must be between 0 and 1.")

    if risk_level == "LOW":
        return {
            "alert_level": "LOW",
            "title": f"Low Flood Risk - {location_name}",
            "message": f"Flood risk is currently assessed as low for {location_name}.",
            "instructions": (
                "No immediate flood action is required. "
                "Continue normal monitoring of local weather conditions."
            ),
            "active": False,
        }

    if risk_level == "MODERATE":
        return {
            "alert_level": "ADVISORY",
            "title": f"Flood Risk Advisory - {location_name}",
            "message": (
                f"Elevated flood-risk conditions have been identified for {location_name}. "
                f"The current model risk score is {risk_score:.3f}."
            ),
            "instructions": (
                "Monitor weather and local drainage conditions closely. "
                "Avoid known flood-prone areas during heavy rainfall and "
                "remain prepared for further advisories."
            ),
            "active": True,
        }

    return {
        "alert_level": "WARNING",
        "title": f"High Flood Risk Warning - {location_name}",
        "message": (
            f"High flood-risk conditions have been identified for {location_name}. "
            f"The current model risk score is {risk_score:.3f}."
        ),
        "instructions": (
            "Exercise increased caution. Avoid flooded roads, drainage channels "
            "and known flood-prone areas. Follow official local emergency guidance "
            "and be prepared to move to safer ground if conditions worsen."
        ),
        "active": True,
    }


# Alert persistence
def persist_alerts(
    engine: Engine,
    predictions: pd.DataFrame,
    prediction_ids: list[int],
    issued_at: datetime | None = None,
) -> dict:
    # Generate and persist flood advisories for operational predictions.
    # Each prediction should have at most one associated alert.
    # Existing alerts are updated on reruns rather than duplicated.

    if len(predictions) != len(prediction_ids):
        raise ValueError("Prediction rows and prediction IDs do not match.")

    issued_at = issued_at or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=ALERT_VALIDITY_HOURS)

    created = 0
    updated = 0
    resolved = 0
    expired = 0
    superseded = 0
    active = 0

    with engine.begin() as connection:
        # Expire active alerts whose 24-hour validity period has elapsed.
        result = connection.execute(
            text("""
                UPDATE flood_alert
                SET status = 'EXPIRED'
                WHERE status = 'ACTIVE'
                  AND expires_at IS NOT NULL
                  AND expires_at <= :issued_at
            """),
            {"issued_at": issued_at},
        )
        expired = result.rowcount

        for (_, row), prediction_id in zip(predictions.iterrows(), prediction_ids):
            location_id = int(row["location_id"])
            prediction_id = int(prediction_id)
            location_name = str(row["location_name"])
            risk_level = str(row["risk_level"]).upper()
            risk_score = float(row["flood_probability"])

            # Supersede previous active alerts for this location.
            # Only the current prediction should retain an active alert.
            result = connection.execute(
                text("""
                    UPDATE flood_alert AS a
                    SET
                        status = 'SUPERSEDED',
                        expires_at = :issued_at
                    FROM flood_risk_prediction AS p
                    WHERE a.prediction_id = p.prediction_id
                      AND p.location_id = :location_id
                      AND a.prediction_id <> :prediction_id
                      AND a.status = 'ACTIVE'
                """),
                {
                    "location_id": location_id,
                    "prediction_id": prediction_id,
                    "issued_at": issued_at,
                },
            )
            superseded += result.rowcount

            # Build the advisory content from the model risk category.
            content = build_alert_content(
                location_name,
                risk_level,
                risk_score,
            )

            # Check whether the current prediction already has an alert.
            existing_alert = connection.execute(
                text("""
                    SELECT alert_id, status
                    FROM flood_alert
                    WHERE prediction_id = :prediction_id
                    ORDER BY alert_id DESC
                    LIMIT 1
                """),
                {"prediction_id": prediction_id},
            ).mappings().first()

            # LOW risk does not require an active alert.
            # Resolve an existing advisory if its risk has fallen to LOW.
            if not content["active"]:
                if existing_alert is not None and existing_alert["status"] != "RESOLVED":
                    connection.execute(
                        text("""
                            UPDATE flood_alert
                            SET
                                alert_level = 'LOW',
                                title = :title,
                                message = :message,
                                instructions = :instructions,
                                issued_at = :issued_at,
                                expires_at = :expires_at,
                                status = 'RESOLVED'
                            WHERE alert_id = :alert_id
                        """),
                        {
                            "alert_id": int(existing_alert["alert_id"]),
                            "title": content["title"],
                            "message": content["message"],
                            "instructions": content["instructions"],
                            "issued_at": issued_at,
                            "expires_at": issued_at,
                        },
                    )
                    resolved += 1

                continue

            parameters = {
                "prediction_id": prediction_id,
                "alert_level": content["alert_level"],
                "title": content["title"],
                "message": content["message"],
                "instructions": content["instructions"],
                "issued_at": issued_at,
                "expires_at": expires_at,
                "status": "ACTIVE",
            }

            if existing_alert is not None:
                # Refresh the existing advisory or warning for this prediction.
                connection.execute(
                    text("""
                        UPDATE flood_alert
                        SET
                            alert_level = :alert_level,
                            title = :title,
                            message = :message,
                            instructions = :instructions,
                            issued_at = :issued_at,
                            expires_at = :expires_at,
                            status = :status
                        WHERE alert_id = :alert_id
                    """),
                    {
                        **parameters,
                        "alert_id": int(existing_alert["alert_id"]),
                    },
                )
                updated += 1

            else:
                # Insert the first advisory or warning for this prediction.
                connection.execute(
                    text("""
                        INSERT INTO flood_alert (
                            prediction_id,
                            alert_level,
                            title,
                            message,
                            instructions,
                            issued_at,
                            expires_at,
                            status,
                            created_at
                        )
                        VALUES (
                            :prediction_id,
                            :alert_level,
                            :title,
                            :message,
                            :instructions,
                            :issued_at,
                            :expires_at,
                            :status,
                            NOW()
                        )
                    """),
                    parameters,
                )
                created += 1

            active += 1

    return {
        "created": created,
        "updated": updated,
        "resolved": resolved,
        "expired": expired,
        "superseded": superseded,
        "active": active,
    }
