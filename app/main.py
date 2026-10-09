from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import cache, migrate, models 
from .database import engine
from .deps import require_auth
from .routers import admin, alerts, auth, dashboard, drives, entry, ingest, maintenance, proposals, replacements, reports, users

OWN_TABLES = [models.UserCredential.__table__, models.AlertMessage.__table__, models.FleetSnapshot.__table__,
              models.UserSession.__table__, models.ProposedAction.__table__]


@asynccontextmanager
async def lifespan(_app):
    migrate.ensure_collected_at(engine)
    migrate.ensure_maintenance_void(engine)
    try:
        for table in OWN_TABLES:
            table.create(bind=engine, checkfirst=True)
    except Exception as exc:  # database not reachable yet: the first request will say so
        print(f"could not check the service's own tables: {exc}")
    yield


app = FastAPI(title="Uyamaa Fleet API", description="Dashboard, drives, alerts, maintenance, replacements, users and reports.",
              lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def forget_cache_after_writes(request, call_next):
    """Any successful change (POST/PUT/PATCH/DELETE) makes the cached pages stale, so drop them all."""
    response = await call_next(request)
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status_code < 400
            and not request.url.path.startswith("/api/auth/")):
        cache.clear()
    return response


@app.get("/api/health")
def health():
    if migrate.status["collected_at"].startswith("failed"):
        migrate.ensure_collected_at(engine)  # the database may simply not have been ready at startup
    if migrate.status["maintenance_void"].startswith("failed"):
        migrate.ensure_maintenance_void(engine)
    return {"status": "ok", "collectedAt": migrate.status["collected_at"], "maintenanceVoid": migrate.status["maintenance_void"]}


for module in (dashboard, drives, entry, alerts, maintenance, replacements, users, reports, admin, proposals):
    app.include_router(module.router, dependencies=[Depends(require_auth)])

app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(proposals.machine_router)
