"""Tiny, safe schema changes the API makes to the shared database at startup.

Only ADDs a nullable column, so every other service that reads smart_reading keeps working
and existing rows are untouched (their collected_at stays empty = "time not recorded").
"""
from sqlalchemy import inspect, text

status = {"collected_at": "unknown"}


def ensure_collected_at(engine) -> str:
    """Make sure smart_reading.collected_at exists. Returns 'present', 'added' or 'failed: <why>'."""
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("smart_reading")}
        if "collected_at" in columns:
            result = "present"
        else:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE smart_reading ADD COLUMN collected_at TIMESTAMP NULL"))
            result = "added"
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_smart_reading_drive_collected "
                                  "ON smart_reading (drive_id, collected_at)"))
        except Exception as exc:  # the index only speeds things up
            print(f"could not create the smart_reading index: {exc}")
    except Exception as exc:
        result = f"failed: {exc}"
        print(f"could not add smart_reading.collected_at (run db/001_smart_reading_collected_at.sql as the database owner): {exc}")
    status["collected_at"] = result
    return result
