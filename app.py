from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Depends
from fastapi.responses import FileResponse

from src.dashboard.routes.public import router as public_router
from src.dashboard.routes.resident import router as resident_router
from src.dashboard.routes.places import router as places_router
from src.dashboard.routes.admin import router as admin_router
from src.dashboard.admin_auth import (router as admin_auth_router, require_admin)

#locate frontend files indepenently of the terminal working directory
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "src" / "web"

app = FastAPI(
    title="Nairobi FloodWatch",
    description="Near-real-time Nairobi flood monitoring and flood risk prediction assessment",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None
)

#serve static frontend CSS and JS files
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

#register API routes that read actual PostgreSQL data
app.include_router(public_router)
app.include_router(resident_router)
app.include_router(places_router)
app.include_router(admin_router)
app.include_router(admin_auth_router)

@app.get("/", include_in_schema=False)
def homepage():
    return FileResponse(WEB_DIR / "templates" / "home.html")

@app.get('/resident', include_in_schema=False)
def resident_page():
    return FileResponse(WEB_DIR / "templates" / "resident.html")

@app.get('/agency', include_in_schema=False)
def agency_page():
    return FileResponse(WEB_DIR / "templates" / "agency.html")

@app.get("/admin", include_in_schema=False)
def admin_page( _: str = Depends(require_admin)):
    template = (Path(__file__).resolve().parent / "src" / "web" / "templates" / "admin.html")
    return FileResponse(template)

@app.get("/admin/login", include_in_schema=False)
def admin_login_page():
    template = (Path(__file__).resolve().parent / "src" / "web" / "templates" / "admin_login.html")
    return FileResponse(template, headers={"Cache-Control": "no-store"},)

# @app.get("/", include_in_schema=False)
# def homepage():
#     return FileResponse(
#         Path(__file__).resolve().parent
#         / "src"
#         / "web"
#         / "templates"
#         / "home.html"
#     )