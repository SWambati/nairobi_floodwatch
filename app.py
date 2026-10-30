from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from src.dashboard.routes.public import router as public_router
from src.dashboard.routes.resident import router as resident_router
from src.dashboard.routes.places import router as places_router
from src.dashboard.routes.admin import router as admin_router
from src.dashboard.routes.agency import router as agency_router
from src.dashboard.routes.admin_approvals import (router as admin_approvals_router)
from src.auth.config import get_auth_settings
from src.auth.google_oauth import (router as google_oauth_router, get_signed_in_user, SESSION_COOKIE, _post_login_destination)
from src.auth.local_routes import (router as local_auth_router, ensure_local_api_schema)
from src.auth.mfa_routes import router as mfa_router
from src.auth.mfa_challenge_routes import router as mfa_challenge_router
from src.auth.resident_account_routes import (router as resident_account_router)
from src.auth.authorization import (require_agency_page, require_admin_page, require_resident_account)
from src.auth.resident_verification import (ensure_resident_verification_schema)
from src.auth.resident_otp_service import (ensure_resident_otp_schema)
from src.operational.persistence import create_database_engine

#PATH CONFIGURATION
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "src" / "web"
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

#APPLICATION LIFESPAN
@asynccontextmanager
async def lifespan(application: FastAPI):
    #Initialize required authentication database structures before accepting application requests.
    #Existing database records are preserved.
    #Resident email verification deadlines are not reset when the application restarts.
    #Startup fails if database initialization fails.
  
    engine = create_database_engine()

    try:
        ensure_local_api_schema(engine)
        ensure_resident_verification_schema(engine)
        ensure_resident_otp_schema(engine)

    finally:
        engine.dispose()

    yield

#FASTAPI APPLICATION
app = FastAPI(
    title="Nairobi FloodWatch",
    description=("Near-real-time Nairobi flood monitoring and flood risk prediction assessment"),
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan)

#AUTHENTICATION CONFIGURATION
auth_settings = get_auth_settings()

if not auth_settings.oauth_session_secret:
    raise RuntimeError("FLOODWATCH_OAUTH_SESSION_SECRET must be configured.")

# 5. SESSION MIDDLEWARE
app.add_middleware(
    SessionMiddleware,
    secret_key=auth_settings.oauth_session_secret,
    session_cookie="floodwatch_oauth_flow",
    max_age=auth_settings.oauth_state_max_age,
    same_site=auth_settings.cookie_samesite,
    https_only=auth_settings.secure_cookies,
)

#STATIC FRONTEND ASSETS
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

#API AND AUTHENTICATION ROUTERS
app.include_router(public_router)
app.include_router(resident_router)
app.include_router(places_router)
app.include_router(admin_router)
app.include_router(admin_approvals_router)
app.include_router(agency_router)
app.include_router(google_oauth_router)
app.include_router(local_auth_router)
app.include_router(mfa_router)
app.include_router(mfa_challenge_router)
app.include_router(resident_account_router)

#PUBLIC HOMEPAGE
@app.get("/", include_in_schema=False)
def homepage():
    #Serve the public Nairobi FloodWatch homepage.
    return FileResponse(TEMPLATE_DIR / "home.html")

#RESIDENT DASHBOARD
@app.get("/resident", include_in_schema=False)
def resident_page():
    #Serve the public Resident monitoring dashboard.
    #Public flood monitoring remains accessible without authentication.
    #Account-specific functionality is protected separately through authentication and authorization dependencies.
    return FileResponse(TEMPLATE_DIR / "resident.html")

#RESIDENT DASHBOARD ENTRY
@app.get("/resident/entry", include_in_schema=False)
def resident_dashboard_entry(request: Request):
    #Provide session-aware Resident Dashboard navigation.
    #Authenticated residents: Redirect to /resident.
    #Guests and users with other roles: Redirect to the login page.
    #This entry route does not restrict direct public access to /resident.

    user = get_signed_in_user(request)

    if user and user.get("role") == "resident":
        destination = "/resident"
    else:
        destination = "/static/auth/login.html"

    response = RedirectResponse(url=destination, status_code=303)
    response.headers["Cache-Control"] = "no-store"

    return response

#RESIDENT ACCOUNT MANAGEMENT
@app.get("/resident/account", include_in_schema=False, dependencies=[Depends(require_resident_account)])
def resident_account_page():
    #Serve the authenticated Resident account page.
    #Authorization is enforced before the page is served. 
    return FileResponse(TEMPLATE_DIR / "resident_account.html", headers={"Cache-Control": "no-store"})

#PASSWORD RECOVERY PAGE
@app.get("/auth/reset-password", include_in_schema=False)
def password_reset_page():
    #Serve the password-reset interface.
    #Reset tokens are processed by the frontend and validated through the existing authentication API.
    return FileResponse(
        STATIC_DIR / "auth" / "reset-password.html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },)

# AGENCY DASHBOARD
@app.get("/agency", include_in_schema=False, dependencies=[Depends(require_agency_page)])
def agency_dashboard():
    #Serve the Agency Dashboard.
    #Access is restricted to authorized agency user through the existing authorization dependency.
    return FileResponse(TEMPLATE_DIR / "agency.html", headers={"Cache-Control": "no-store"})

#AGENCY DASHBOARD ENTRY
@app.get("/agency/entry", include_in_schema=False)
def agency_dashboard_entry(request: Request):
    #Provide session-aware Agency Dashboard navigation.
    #Unauthenticated visitors and non-agency users are redirected to the login page.
    #Authenticated agency users are routed through the existing post-login destination logic, including the MFA workflow where required.

    user = get_signed_in_user(request)

    if not user or user.get("role") != "agency":
        destination = "/static/auth/login.html"

    else:
        session_token = request.cookies.get(SESSION_COOKIE)
        destination = _post_login_destination(session_token)

    response = RedirectResponse(url=destination, status_code=303)
    response.headers["Cache-Control"] = "no-store"

    return response

#ADMINISTRATOR DASHBOARD
@app.get("/admin", include_in_schema=False, dependencies=[Depends(require_admin_page)])
def admin_dashboard():
    #Serve the Administrator Dashboard.
    #Access is restricted through the existing administrator authorization and MFA controls.
    return FileResponse(TEMPLATE_DIR / "admin.html", headers={"Cache-Control": "no-store"})

#ADMINISTRATOR APPROVALS PAGE FOR AGENCIES
@app.get("/admin/approvals", include_in_schema=False, dependencies=[Depends(require_admin_page)])
def admin_approvals_page():
    return FileResponse(TEMPLATE_DIR / "admin_approvals.html", headers={"Cache-Control": "no-store"})

# ADMINISTRATOR ACCOUNT MANAGEMENT
@app.get("/admin/account", include_in_schema=False, dependencies=[Depends(require_admin_page)])
def admin_account_page():
    #Serve the authenticated Administrator account page.
    #Existing administrator authorization and MFA requirements are enforced before serving the page.
    return FileResponse(TEMPLATE_DIR / "admin_account.html", headers={"Cache-Control": "no-store"})

#LEGACY ADMIN LOGIN REDIRECT
@app.get("/admin/login", include_in_schema=False)
def legacy_login_redirect():
    # Preserve compatibility with older administrator login links.
    return RedirectResponse(url="/auth/google/login", status_code=303)
