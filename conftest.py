import os
import tempfile
from datetime import date

import pytest

# Tests run against a throw-away SQLite file, never the real database.
_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ["ALERT_SERVICE_URL"] = ""  # the alert service is switched on inside its own tests
os.environ["AUTH_REQUIRED"] = "false"  # auth is switched on inside the auth tests

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    today = date.today()
    db.add_all([
        models.DataCenter(data_center_id=1, name="London DC-01", location="London"),
        models.DataCenter(data_center_id=2, name="Frankfurt DC-02", location="Frankfurt"),
        models.User(user_id=1, full_name="Amina Khan", email="amina@x.test", role="Administrator"),
        models.User(user_id=2, full_name="Lukas Weber", email="lukas@x.test", role="Technician"),
        models.HardDrive(drive_id=1, data_center_id=1, serial_number="ZL2C4M8Q", model="Exos X18", capacity=18, status="Critical"),
        models.HardDrive(drive_id=2, data_center_id=1, serial_number="ZL2C4M9R", model="Exos X18", capacity=18, status="Warning"),
        models.HardDrive(drive_id=3, data_center_id=2, serial_number="7JG2K9HG", model="Ultrastar", capacity=16, status="Healthy"),
        models.HardDrive(drive_id=4, data_center_id=2, serial_number="7JG1R3VA", model="Ultrastar", capacity=16, status="Healthy"),
        models.SmartReading(drive_id=1, temperature=48, power_on_hours=52416, reallocated_sectors=128, spin_retry_count=2,
                            end_to_end_error=0, reported_uncorrectable=6, command_timeout=3,
                            current_pending_sector=16, offline_uncorrectable=8),
        models.Prediction(prediction_id=1, drive_id=1, predicted_failure_date=date(2026, 10, 9), risk_level="High", confidence_level=0.91),
        models.Prediction(prediction_id=2, drive_id=2, predicted_failure_date=date(2026, 11, 18), risk_level="Medium", confidence_level=0.84),
        models.Alert(alert_id=1, drive_id=1, prediction_id=1, data_center_id=1, alert_date=today, alert_type="Predicted failure", severity="Critical", outcome="Open"),
        models.Alert(alert_id=2, drive_id=2, prediction_id=2, data_center_id=1, alert_date=date(2026, 10, 5), alert_type="Pending sectors", severity="Warning", outcome="Acknowledged"),
        models.Alert(alert_id=3, drive_id=3, prediction_id=2, data_center_id=2, alert_date=date(2026, 10, 1), alert_type="Telemetry restored", severity="Info", outcome="Resolved"),
        models.Maintenance(maintenance_id=1, drive_id=1, data_center_id=1, maintenance_date=date(2026, 10, 5), maintenance_type="SMART diagnostic", performed_by=1),
        models.Replacement(replacement_id=1, drive_id=3, data_center_id=2, replacement_date=date(2026, 10, 4), replaced_by=2, reason="Reallocated sectors", new_drive_id=4),
    ])
    db.commit()
    db.close()
    return TestClient(app)
