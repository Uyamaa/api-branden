from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (registers the tables)
from .database import engine
from .deps import require_auth
from .routers import alerts, auth, dashboard, drives, entry, maintenance, replacements, reports, users

# The service never touches the shared tables. It only creates the three tables it owns itself
# (sign-in passwords, written alerts, daily dashboard numbers) if they are missing.
OWN_TABLES = [models.UserCredential.__table__, models.AlertMessage.__table__, models.FleetSnapshot.__table__]


@asynccontextmanager
async def lifespan(_app):
    try:
        for table in OWN_TABLES:
            table.create(bind=engine, checkfirst=True)
    except Exception as exc:  # database not reachable yet: the first request will say so
        print(f"could not check the service's own tables: {exc}")
    yield


app = FastAPI(title="Uyamaa Fleet API", description="Dashboard, drives, alerts, maintenance, replacements, users and reports.",
              lifespan=lifespan)

# The frontend normally reaches this through its nginx proxy (same origin), so CORS is only a fallback.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"status": "ok"}


for module in (dashboard, drives, entry, alerts, maintenance, replacements, users, reports):
    app.include_router(module.router, dependencies=[Depends(require_auth)])

# Sign-in itself must stay open.
app.include_router(auth.router)
