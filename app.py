from pathlib import Path
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src.dashboard.routes.public import router as public_router
from src.dashboard.routes.resident import router as resident_router
from src.dashboard.routes.places import router as places_router
from src.dashboard.routes.admin import router as admin_router
from src.dashboard.routes.agency import router as agency_router
from src.auth.config import get_auth_settings
from src.auth.google_oauth import router as google_oauth_router
from src.auth.local_routes import router as local_auth_router
from src.auth.mfa_routes import (router as mfa_router)
from src.auth.mfa_challenge_routes import (router as mfa_challenge_router)
from src.auth.authorization import (require_agency_page, require_admin_page)

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "src" / "web"

app = FastAPI(
    title="Nairobi FloodWatch",
    description=("Near-real-time Nairobi flood monitoring and flood risk prediction assessment"),
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# Authentication configuration
auth_settings = get_auth_settings()

if not auth_settings.oauth_session_secret:
    raise RuntimeError("FLOODWATCH_OAUTH_SESSION_SECRET must be configured.")

app.add_middleware(
    SessionMiddleware,
    secret_key=auth_settings.oauth_session_secret,
    session_cookie="floodwatch_oauth_flow",
    max_age=auth_settings.oauth_state_max_age,
    same_site=auth_settings.cookie_samesite,
    https_only=auth_settings.secure_cookies,
)

# Static frontend assets
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

# API routes
app.include_router(public_router)
app.include_router(resident_router)
app.include_router(places_router)
app.include_router(admin_router)
app.include_router(agency_router)
app.include_router(google_oauth_router)
app.include_router(local_auth_router)
app.include_router(mfa_router)
app.include_router(mfa_challenge_router)

# Public homepage
@app.get("/", include_in_schema=False)
def homepage():
    return FileResponse(WEB_DIR / "templates" / "home.html")

# Public Resident Dashboard
@app.get("/resident", include_in_schema=False)
def resident_page():
    return FileResponse(WEB_DIR / "templates" / "resident.html")

# Restricted Agency Dashboard
@app.get("/agency", include_in_schema=False, dependencies=[Depends(require_agency_page)])
def agency_dashboard():
    return FileResponse(WEB_DIR / "templates" / "agency.html")

# Restricted Administrator Dashboard
@app.get("/admin", include_in_schema=False, dependencies=[Depends(require_admin_page)])
def admin_dashboard():
    return FileResponse(WEB_DIR / "templates" / "admin.html")

# Compatibility redirect for the existing homepage login link
@app.get("/admin/login", include_in_schema=False)
def legacy_login_redirect():
    return RedirectResponse( url="/auth/google/login", status_code=303,)
