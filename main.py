import os
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI(
    title="Predictive Maintenance - Hardware Inventory Service (api-branden)",
    version="1.0.0"
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pdm_user:pdm_password@localhost:5432/predictive_maintenance"
)

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

class DriveCreate(BaseModel):
    serial_number: str
    model: str
    capacity_bytes: int
    centre_id: int
    status: Optional[str] = "ACTIVE"

@app.get("/health")
def health_check():
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        conn.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database_error": str(e)}

#  GET /api/drives 
@app.get("/api/drives")
def get_all_drives(centre_id: Optional[int] = None, status: Optional[str] = None):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            query = "SELECT * FROM hard_drive WHERE 1=1"
            params = []
            if centre_id:
                query += " AND centre_id = %s"
                params.append(centre_id)
            if status:
                query += " AND status = %s"
                params.append(status)
            query += " ORDER BY installed_at DESC;"
            cur.execute(query, tuple(params))
            drives = cur.fetchall()
        conn.close()
        return {"count": len(drives), "drives": drives}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

#  GET /api/drives/{serial_number} @app.get("/api/drives/{serial_number}")
def get_drive_details(serial_number: str):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hard_drive WHERE serial_number = %s;", (serial_number,))
            drive = cur.fetchone()
        conn.close()
        if not drive:
            raise HTTPException(status_code=404, detail=f"Drive {serial_number} not found")
        return drive
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 3. POST /api/drives 
@app.post("/api/drives", status_code=201)
def register_drive(drive: DriveCreate):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hard_drive (serial_number, model, capacity_bytes, centre_id, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *;
                """,
                (drive.serial_number, drive.model, drive.capacity_bytes, drive.centre_id, drive.status)
            )
            new_drive = cur.fetchone()
            conn.commit()
        conn.close()
        return {"message": "Drive registered successfully", "drive": new_drive}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

#  GET /api/drives/{serial_number}/history @app.get("/api/drives/{serial_number}/history")
def get_drive_history(serial_number: str, limit: int = 20):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM smart_reading 
                WHERE serial_number = %s 
                ORDER BY timestamp DESC 
                LIMIT %s;
                """,
                (serial_number, limit)
            )
            readings = cur.fetchall()
        conn.close()
        return {"serial_number": serial_number, "count": len(readings), "history": readings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

#  DELETE /api/drives/{serial_number} @app.delete("/api/drives/{serial_number}")
def decommission_drive(serial_number: str):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hard_drive 
                SET status = 'DECOMMISSIONED' 
                WHERE serial_number = %s 
                RETURNING *;
                """,
                (serial_number,)
            )
            updated = cur.fetchone()
            conn.commit()
        conn.close()
        if not updated:
            raise HTTPException(status_code=404, detail=f"Drive {serial_number} not found")
        return {"message": f"Drive {serial_number} successfully decommissioned", "drive": updated}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))