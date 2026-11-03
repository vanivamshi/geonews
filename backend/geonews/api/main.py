from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from geonews.api import routes_admin, routes_user
from geonews.config import get_settings
from geonews.db import init_db
from geonews.telemetry import setup_telemetry
from geonews.tools.registry import load_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_telemetry("geonews-api")
    load_all()
    await init_db()
    yield


app = FastAPI(title="GeoNews", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(routes_user.router)
app.include_router(routes_admin.router)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"ok": True}


def run() -> None:
    import uvicorn

    uvicorn.run("geonews.api.main:app", host="0.0.0.0", port=8000)
