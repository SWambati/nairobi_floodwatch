import logging
from fastapi import APIRouter, HTTPException, Query

from src.dashboard.place_search import (PlaceSearchUnavailable, search_nairobi_places)

router = APIRouter(prefix="/api/places", tags=["Geographic Search"])
logger = logging.getLogger("floodwatch.api.places")

@router.get("/search")
def search_places(q: str = Query(min_length=3, max_length=70)):
    #explicit searches only. The frontend must not call this on every keystroke/ expose the provider as an autocomplete service
    try:
        places = search_nairobi_places(q)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc),) from exc

    except PlaceSearchUnavailable as exc:
        logger.warning("Geographic search unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Geographic lookup is temporarily unavailable") from exc

    return {
        "query": q.strip(),
        "results": places,
        "attribution": "© OpenStreetMap contributors"
    }
