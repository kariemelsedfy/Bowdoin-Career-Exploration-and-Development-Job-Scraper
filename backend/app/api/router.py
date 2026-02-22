from fastapi import APIRouter

from backend.app.api.routes import health, runs

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(runs.router, prefix="/runs", tags=["runs"])
