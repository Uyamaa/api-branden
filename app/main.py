from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (registers the tables)
from .database import Base, engine
from .routers import alerts, dashboard, drives, maintenance, replacements, reports, users

# Creates any missing tables. Existing tables from the other services are left untouched.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Uyamaa Fleet API", description="Dashboard, drives, alerts, maintenance, replacements, users and reports.")

# The frontend normally reaches this through its nginx proxy (same origin), so CORS is only a fallback.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"status": "ok"}


for module in (dashboard, drives, alerts, maintenance, replacements, users, reports):
    app.include_router(module.router)
