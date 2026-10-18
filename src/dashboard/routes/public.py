import logging
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder

from src.dashboard.database import check_database_connection
from src.dashboard.queries import get_locations, get_overview

router = APIRouter(prefix="/api", tags=["Public Monitoring"])
logger = logging.getLogger("floodwatch.api")

def database_error(exc: Exception):
    #record the actual error on the server without exposing connection details or PostgreSQL credentials to the public

    logger.error("FloodWatch public API database request failed", exc_info=(type(exc), exc, exc.__traceback__),)

    raise HTTPException(status_code=503, detail="FloodWatch monitoring data is temporarily unavailable") from exc

@router.get("/health")
def health():
    try:
        connected = check_database_connection()

    except Exception as exc:
        database_error(exc)

    if not connected:
        raise HTTPException(status_code=503, detail="Database Unavailable")

    return {"status": "ok", "database": connected}

@router.get("/overview")
def overview():
    try:
        return jsonable_encoder(get_overview())

    except Exception as exc:
        database_error(exc)

@router.get("/locations")
def locations():
    try:
        return jsonable_encoder(get_locations())

    except Exception as exc:
        database_error(exc)

@router.get("/locations/{location_id}")
def location_detail(location_id: int):

    try:
        rows = get_locations(location_id)

    except Exception as exc:
        database_error(exc)

    if not rows:
        raise HTTPException(status_code=404, detail="Monitoring location was not found")

    return jsonable_encoder(rows[0])