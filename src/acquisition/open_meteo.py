import json
import time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests
from config.paths import PROCESSED_DIR
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


#Configuration
NAIROBI_TIMEZONE = "Africa/Nairobi"
GENERIC_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ECMWF_FORECAST_URL = "https://api.open-meteo.com/v1/ecmwf"

PAST_DAYS = 7
FORECAST_DAYS = 1
MAX_ATTEMPTS = 4
REQUEST_TIMEOUT_SECONDS = 30
RETRY_BASE_SECONDS = 2
MAX_RETRY_AFTER_SECONDS = 30

GENERIC_HOURLY_VARIABLES = ["precipitation"]
GENERIC_CURRENT_VARIABLES = ["temperature_2m", "relative_humidity_2m", "precipitation", "surface_pressure"]
ECMWF_HOURLY_VARIABLES = ["temperature_2m", "relative_humidity_2m", "surface_pressure", "precipitation", "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm"]


#Path configuration
OPEN_METEO_DIR = PROCESSED_DIR / "operational" / "open_meteo"


class OpenMeteoClient:
    def __init__(self, max_attempts: int = MAX_ATTEMPTS, timeout_seconds: int = REQUEST_TIMEOUT_SECONDS):
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

   
    @staticmethod
    def _retry_after_delay(value):
        if not value:
            return None

        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            try:
                target = parsedate_to_datetime(value)
                return max(0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _request(self, url: str, params: dict):
        last_error = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)

            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = type(exc).__name__

            else:
                status = response.status_code

                if status == 429:
                    delay = self._retry_after_delay(response.headers.get("Retry-After"))

                    if delay is None or delay > MAX_RETRY_AFTER_SECONDS or attempt == self.max_attempts:
                        raise RuntimeError("Open-Meteo rate limited requests (HTTP 429). Acquisition deferred.")

                    time.sleep(delay)
                    continue

                if 400 <= status < 500:
                    raise RuntimeError(f"Open-Meteo rejected request: HTTP {status}")

                if status >= 500:
                    last_error = f"HTTP {status}"

                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise RuntimeError("Open-Meteo returned invalid JSON.") from exc

                    if isinstance(payload, dict) and payload.get("error"):
                        raise RuntimeError(f"Open-Meteo API error: {payload.get('reason', 'unknown')}")

                    return payload

            if attempt < self.max_attempts:
                delay = min(RETRY_BASE_SECONDS * (2 ** (attempt - 1)), 30)
                time.sleep(delay)

        raise RuntimeError(f"Open-Meteo request failed after {self.max_attempts} attempts: {last_error}")

    @staticmethod
    def _normalise_payload(payload) -> list[dict]:
        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            return [payload]

        raise TypeError("Unexpected Open-Meteo response structure.")

    @staticmethod
    def _coordinate_params(locations: pd.DataFrame) -> dict:
        required = {"latitude", "longitude"}
        missing = required - set(locations.columns)

        if missing:
            raise ValueError(f"Location input is missing required columns: {sorted(missing)}")

        if locations.empty:
            raise ValueError("Location input contains no rows.")

        latitudes = pd.to_numeric(locations["latitude"], errors="raise")
        longitudes = pd.to_numeric(locations["longitude"], errors="raise")

        if not latitudes.between(-90, 90).all():
            raise ValueError("Invalid latitude value detected.")

        if not longitudes.between(-180, 180).all():
            raise ValueError("Invalid longitude value detected.")

        timezone_list = ",".join([NAIROBI_TIMEZONE] * len(locations))

        return {
            "latitude": ",".join(latitudes.astype(str)),
            "longitude": ",".join(longitudes.astype(str)),
            "timezone": timezone_list,
            "past_days": PAST_DAYS,
            "forecast_days": FORECAST_DAYS,
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
            "timeformat": "iso8601",
            "cell_selection": "land"
        }

    def _fetch_generic(self, locations: pd.DataFrame):
        params = self._coordinate_params(locations)
        params.update({
            "hourly": ",".join(GENERIC_HOURLY_VARIABLES),
            "current": ",".join(GENERIC_CURRENT_VARIABLES),
        })

        return self._request(GENERIC_FORECAST_URL, params)

    def _fetch_ecmwf(self, locations: pd.DataFrame):
        params = self._coordinate_params(locations)
        params.update({"hourly": ",".join(ECMWF_HOURLY_VARIABLES)})

        return self._request(ECMWF_FORECAST_URL, params)

    @staticmethod
    def _hourly_frame(payload: dict, location_id: str, variables: list[str], source: str, fetched_at_utc: str) -> pd.DataFrame:

        if "hourly" not in payload or "time" not in payload["hourly"]:
            raise ValueError(f"Open-Meteo {source} response is missing hourly timestamps.")

        hourly = payload["hourly"]
        frame = pd.DataFrame({"local_time": hourly["time"]})

        for variable in variables:
            if variable not in hourly:
                raise ValueError(f"Open-Meteo {source} response is missing {variable}.")

            frame[variable] = hourly[variable]

        frame.insert(0, "location_id", location_id)
        frame["source"] = source
        frame["requested_latitude"] = payload.get("latitude")
        frame["requested_longitude"] = payload.get("longitude")
        frame["api_elevation_m"] = payload.get("elevation")
        frame["timezone"] = payload.get("timezone")
        frame["utc_offset_seconds"] = payload.get("utc_offset_seconds")
        frame["fetched_at_utc"] = fetched_at_utc

        return frame

    @staticmethod
    def _current_frame(payload: dict, location_id: str, fetched_at_utc: str) -> pd.DataFrame:
        current = payload.get("current")

        if not current:
            raise ValueError("Open-Meteo response does not contain current conditions.")

        row = {
            "location_id": location_id,
            "local_time": current.get("time"),
            "temperature_2m": current.get("temperature_2m"),
            "relative_humidity_2m": current.get("relative_humidity_2m"),
            "precipitation": current.get("precipitation"),
            "surface_pressure": current.get("surface_pressure"),
            "source": "open_meteo_best_match",
            "fetched_at_utc": fetched_at_utc
        }

        return pd.DataFrame([row])

    def fetch_locations(self, locations: pd.DataFrame) -> dict[str, pd.DataFrame]:

        if "location_id" not in locations.columns:
            raise ValueError("Location input must contain location_id.")

        fetched_at_utc = datetime.now(timezone.utc).isoformat()

        generic_payload = self._normalise_payload(self._fetch_generic(locations))
        ecmwf_payload = self._normalise_payload(self._fetch_ecmwf(locations))

        if len(generic_payload) != len(locations):
            raise ValueError("Generic Open-Meteo response count does not match location count.")

        if len(ecmwf_payload) != len(locations):
            raise ValueError("ECMWF Open-Meteo response count does not match location count.")

        generic_frames = []
        ecmwf_frames = []
        current_frames = []

        for index, location in locations.reset_index(drop=True).iterrows():
            location_id = str(location["location_id"])

            generic_frames.append(
                self._hourly_frame(
                    generic_payload[index],
                    location_id,
                    GENERIC_HOURLY_VARIABLES,
                    "open_meteo_best_match",
                    fetched_at_utc,
                )
            )

            ecmwf_frames.append(self._hourly_frame(ecmwf_payload[index], location_id, ECMWF_HOURLY_VARIABLES, "open_meteo_ecmwf_ifs", fetched_at_utc))
            current_frames.append(self._current_frame(generic_payload[index], location_id, fetched_at_utc))

        return {
            "generic_hourly": pd.concat(generic_frames, ignore_index=True),
            "ecmwf_hourly": pd.concat(ecmwf_frames, ignore_index=True),
            "current": pd.concat(current_frames, ignore_index=True),
        }

    def fetch_locations_batched(self, locations: pd.DataFrame, batch_size: int = 40) -> dict[str, pd.DataFrame]:
        if batch_size < 1:
            raise ValueError("Batch size must be at least 1.")

        if locations.empty:
            raise ValueError("Location input contains no rows")

        outputs = {"generic_hourly": [],
                  "ecmwf_hourly": [],
                  "current": []
                  }

        for start in range(0, len(locations), batch_size):
            subset = locations.iloc[start:start + batch_size].copy()
            batch = self.fetch_locations(subset)

            for name in outputs:
                outputs[name].append(batch[name])

        return {name: pd.concat(frames, ignore_index=True)
                for name, frames in outputs.items()}


def save_open_meteo_snapshot(batch: dict[str, pd.DataFrame]) -> dict[str, Path]:
    OPEN_METEO_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    files = {
        "generic_hourly": OPEN_METEO_DIR / f"generic_hourly_{timestamp}.csv",
        "ecmwf_hourly": OPEN_METEO_DIR / f"ecmwf_hourly_{timestamp}.csv",
        "current": OPEN_METEO_DIR / f"current_conditions_{timestamp}.csv",
        "metadata": OPEN_METEO_DIR / f"ingestion_{timestamp}.json",
    }

    batch["generic_hourly"].to_csv(files["generic_hourly"], index=False)
    batch["ecmwf_hourly"].to_csv(files["ecmwf_hourly"], index=False)
    batch["current"].to_csv(files["current"], index=False)

    metadata = {
        "fetched_at_utc": timestamp,
        "timezone": NAIROBI_TIMEZONE,
        "past_days": PAST_DAYS,
        "forecast_days": FORECAST_DAYS,
        "generic_source": "Open-Meteo Best Match Forecast API",
        "era5_like_source": "Open-Meteo ECMWF IFS Forecast API",
        "generic_hourly_rows": len(batch["generic_hourly"]),
        "ecmwf_hourly_rows": len(batch["ecmwf_hourly"]),
        "current_rows": len(batch["current"]),
    }

    with files["metadata"].open("w", encoding="utf-8") as file: 
        json.dump(metadata, file, indent=2)

    return files