from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (registers the tables)
from .routers import alerts, dashboard, drives, entry, maintenance, replacements, reports, users

# The service never creates tables: the schema belongs to the shared database.

app = FastAPI(title="Uyamaa Fleet API", description="Dashboard, drives, alerts, maintenance, replacements, users and reports.")

# The frontend normally reaches this through its nginx proxy (same origin), so CORS is only a fallback.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"status": "ok"}


for module in (dashboard, drives, entry, alerts, maintenance, replacements, users, reports):
    app.include_router(module.router)
