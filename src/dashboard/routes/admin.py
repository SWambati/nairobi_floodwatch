import logging
from functools import lru_cache
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import SQLAlchemyError

from src.dashboard.admin_queries import (get_operational_overview, get_recent_runs, get_run_events, get_latest_stage_results, get_recent_errors)
from src.operational.persistence import create_database_engine
from src.auth.authorization import require_admin_access

logger = logging.getLogger("floodwatch.admin")

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin Monitoring"],
    dependencies=[Depends(require_admin_access)],
    include_in_schema=False,
)


@lru_cache(maxsize=1)
def get_engine():
    """Reuse the PostgreSQL engine and its connection pool."""
    return create_database_engine()


def execute_query(query, *args, **kwargs):
    """Convert database failures into safe API responses."""
    try:
        return jsonable_encoder(query(get_engine(), *args, **kwargs))

    except SQLAlchemyError as exc:
        logger.exception("Admin monitoring database query failed")
        raise HTTPException(status_code=503, detail="Operational monitoring is temporarily unavailable.",) from exc


@router.get("/health")
def admin_health():
    overview = execute_query(get_operational_overview)

    return {
        "database": "reachable",
        "latest_pipeline_run": overview["latest_run"],
        "last_success": overview["last_success"],
        "checked_scope": "database connectivity and stored pipeline records",
    }


@router.get("/overview")
def admin_overview():
    return execute_query(get_operational_overview)


@router.get("/runs")
def admin_runs(limit: int = Query(20, ge=1, le=100)):
    return {
        "runs": execute_query(get_recent_runs, limit),
    }


@router.get("/runs/{run_id}/events")
def admin_run_events(run_id: UUID, limit: int = Query(200, ge=1, le=500),):
    return {
        "run_id": str(run_id),
        "events": execute_query(get_run_events, run_id, limit),
    }


@router.get("/stages")
def admin_stages():
    return {
        "stages": execute_query(get_latest_stage_results),
    }


@router.get("/errors")
def admin_errors(limit: int = Query(30, ge=1, le=100)):
    return {
        "errors": execute_query(get_recent_errors, limit),
    }