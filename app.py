from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.dashboard.routes.public import router as public_router
from src.dashboard.routes.resident import router as resident_router
from src.dashboard.routes.places import router as places_router

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

@app.get("/", include_in_schema=False)
def homepage():
    return FileResponse(WEB_DIR / "templates" / "index.html")

@app.get('/resident', include_in_schema=False)
def resident_page():
    return FileResponse(WEB_DIR / "templates" / "resident.html")

@app.get('/agency', include_in_schema=False)
def agency_page():
    return FileResponse(WEB_DIR / "templates" / "agency.html")