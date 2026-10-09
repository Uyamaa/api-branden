from sqlalchemy import inspect, text

status = {"collected_at": "unknown", "maintenance_void": "unknown"}


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


VOID_COLUMNS = {"voided_at": "TIMESTAMP NULL", "voided_by": "INTEGER NULL", "void_reason": "VARCHAR(200) NULL"}


def ensure_maintenance_void(engine) -> str:
    """Make sure maintenance has the three nullable "voided" columns. Returns 'present', 'added' or 'failed: <why>'.

    Only ADDs nullable columns, so every other service that reads maintenance keeps working and
    existing rows stay as they are (not voided).
    """
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("maintenance")}
        missing = [name for name in VOID_COLUMNS if name not in columns]
        for name in missing:
            definition = VOID_COLUMNS[name]
            if name == "voided_by" and engine.dialect.name == "postgresql":
                definition = "INTEGER NULL REFERENCES users(user_id)"
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE maintenance ADD COLUMN {name} {definition}"))
        result = "added" if missing else "present"
    except Exception as exc:
        result = f"failed: {exc}"
        print(f"could not add the maintenance void columns (run db/002_maintenance_void.sql as the database owner): {exc}")
    status["maintenance_void"] = result
    return result
