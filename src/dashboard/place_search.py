import os
import re
import threading
import time
from functools import lru_cache
import httpx

from config.settings import PROJECT_ROOT
from src.dashboard.ward_lookup import identify_ward

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Approximate Nairobi search rectangle, not a legal county boundary.
NAIROBI_VIEWBOX = "36.60,-1.12,37.18,-1.53"

_request_lock = threading.Lock()
_last_request = 0.0


class PlaceSearchUnavailable(Exception):
    pass


def normalize_place_query(query: str) -> str:
    # Whitespace and basic character validation.
    query = re.sub(r"\s+", " ", query).strip()

    if not 3 <= len(query) <= 70:
        raise ValueError("Place search must contain 3–70 characters.")

    if not re.fullmatch(r"[\w\s.,'’/\-]+", query):
        raise ValueError("Place search contains unsupported characters.")

    return query


@lru_cache(maxsize=256)
def lookup_cached(query: str, contact_email: str) -> list[dict]:
    # Cache repeated searches for this server process.
    # The lock prevents simultaneous requests from exceeding the public provider's maximum request frequency in one process.
    global _last_request

    parameters = {
        "q": f"{query}, Nairobi, Kenya",
        "format": "jsonv2",
        "addressdetails": 1,
        "countrycodes": "ke",
        "viewbox": NAIROBI_VIEWBOX,
        "bounded": 1,
        "limit": 4,
        "email": contact_email,
    }

    headers = {
        "User-Agent": f"NairobiFloodWatch-academic/0.1 ({contact_email})",
        "Accept-Language": "en",
    }

    with _request_lock:
        elapsed = time.monotonic() - _last_request
        delay = max(0.0, 1.1 - elapsed)

        if delay:
            time.sleep(delay)

        _last_request = time.monotonic()

        try:
            with httpx.Client(timeout=12.0) as client:
                response = client.get(
                    NOMINATIM_URL,
                    params=parameters,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

        except (httpx.HTTPError, ValueError) as exc:
            raise PlaceSearchUnavailable("Geographic lookup is temporarily unavailable.") from exc

    if not isinstance(data, list):
        raise PlaceSearchUnavailable("Unexpected geocoding response.")

    return data


def search_nairobi_places(query: str) -> list[dict]:
    query = normalize_place_query(query)

    # A real contact address is required for identifiable provider usage.
    # Do not substitute a fake email or commit the actual address to Git.
    contact_email = os.getenv("NOMINATIM_CONTACT_EMAIL", "").strip()

    if not contact_email or "@" not in contact_email:
        raise PlaceSearchUnavailable("The geographic search service is not configured.")

    results = lookup_cached(query.casefold(), contact_email)
    places = []

    for result in results:
        try:
            latitude = float(result["lat"])
            longitude = float(result["lon"])
        except (KeyError, ValueError, TypeError):
            continue

        ward = identify_ward(latitude, longitude)

        places.append({
            "name": str(result.get("display_name", "")).split(",")[0].strip(),
            "display_name": str(result.get("display_name", ""))[:250],
            "latitude": latitude,
            "longitude": longitude,
            "place_type": str(result.get("type", "place")),
            "ward_name": ward["ward_name"],
            "ward_source": ward["ward_source"],
        })

    return places
