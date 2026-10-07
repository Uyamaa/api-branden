from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (registers the tables)
from .deps import require_auth
from .routers import alerts, auth, dashboard, drives, entry, maintenance, replacements, reports, users

app = FastAPI(title="Uyamaa Fleet API", description="Dashboard, drives, alerts, maintenance, replacements, users and reports.")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"status": "ok"}


for module in (dashboard, drives, entry, alerts, maintenance, replacements, users, reports):
    app.include_router(module.router, dependencies=[Depends(require_auth)])

# Sign-in itself must stay open.
app.include_router(auth.router)
