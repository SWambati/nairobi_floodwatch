import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from src.dashboard.queries import get_locations
from src.dashboard.resident_queries import get_resident_history

router = APIRouter(prefix="/api/resident", tags=["Resident Monitoring"])
logger = logging.getLogger("floodwatch.resident")

@router.get("/locations/{location_id}/history")
def location_history(location_id: int, days: int = Query(30, ge=1, le=90)):
    #return only publicly available observation-point data
    #do not submit user's browser coordinate to the endpoints

    try:
        if not get_locations(location_id):
            raise HTTPException(status_code=404, detail="Monitoring point not found")
        return jsonable_encoder(get_resident_history(location_id, days))

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to read resident history for location %s", location_id)
        raise HTTPException(status_code=503, detail="Monitoring history is temporarily unavailable") from exc