from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String, Text

from .database import Base


class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    role = Column(String(20), nullable=False)


class DataCenter(Base):
    __tablename__ = "data_center"
    data_center_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    location = Column(String(100), nullable=False)


class HardDrive(Base):
    __tablename__ = "hard_drive"
    drive_id = Column(Integer, primary_key=True, index=True)
    data_center_id = Column(Integer, ForeignKey("data_center.data_center_id"), nullable=False)
    serial_number = Column(String(50), unique=True, nullable=False)
    model = Column(String(50), nullable=False)
    capacity = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)


class SmartReading(Base):
    __tablename__ = "smart_reading"
    reading_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hard_drive.drive_id"), nullable=False)
    temperature = Column(Float, nullable=False)
    power_on_hours = Column(Integer, nullable=False)
    reallocated_sectors = Column(Integer, nullable=False)
    spin_retry_count = Column(Integer, nullable=False)
    end_to_end_error = Column(Integer, nullable=False)
    reported_uncorrectable = Column(Integer, nullable=False)
    command_timeout = Column(Integer, nullable=False)
    current_pending_sector = Column(Integer, nullable=False)
    offline_uncorrectable = Column(Integer, nullable=False)


class Prediction(Base):
    __tablename__ = "prediction"
    prediction_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hard_drive.drive_id"), nullable=False)
    predicted_failure_date = Column(Date, nullable=False)
    risk_level = Column(String(20), nullable=False)
    confidence_level = Column(Float, nullable=False)


class Maintenance(Base):
    __tablename__ = "maintenance"
    maintenance_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hard_drive.drive_id"), nullable=False)
    data_center_id = Column(Integer, ForeignKey("data_center.data_center_id"), nullable=False)
    maintenance_date = Column(Date, nullable=False)
    maintenance_type = Column(String(50), nullable=False)
    performed_by = Column(Integer, ForeignKey("users.user_id"), nullable=False)


class Alert(Base):
    __tablename__ = "alert"
    alert_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hard_drive.drive_id"), nullable=False)
    prediction_id = Column(Integer, ForeignKey("prediction.prediction_id"), nullable=False)
    data_center_id = Column(Integer, ForeignKey("data_center.data_center_id"), nullable=False)
    alert_date = Column(Date, nullable=False)
    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    outcome = Column(Text, nullable=False)


class Replacement(Base):
    __tablename__ = "replacement"
    replacement_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hard_drive.drive_id"), nullable=False)
    data_center_id = Column(Integer, ForeignKey("data_center.data_center_id"), nullable=False)
    replacement_date = Column(Date, nullable=False)
    replaced_by = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    reason = Column(Text, nullable=False)
    new_drive_id = Column(Integer, nullable=False)


class UserCredential(Base):
    """Sign-in secret for a user. Kept apart from `users` so other services are unaffected."""
    __tablename__ = "user_credential"
    user_id = Column(Integer, ForeignKey("users.user_id"), primary_key=True)
    password_hash = Column(String(100), nullable=False)


class AlertMessage(Base):
    """The latest written alert (message + steps) for a drive. One row per drive."""
    __tablename__ = "alert_message"
    drive_id = Column(Integer, ForeignKey("hard_drive.drive_id"), primary_key=True)
    risk_level = Column(String(20), nullable=False)
    probability = Column(Float, nullable=False)
    message = Column(Text, nullable=False)
    steps = Column(Text, nullable=False)  # JSON list of strings
    source = Column(String(10), nullable=False)  # "llm" or "template"
    created_at = Column(DateTime, nullable=False)


class FleetSnapshot(Base):
    """One row per day and scope, written when the dashboard loads. Feeds the trend lines."""
    __tablename__ = "fleet_snapshot"
    snapshot_date = Column(Date, primary_key=True)
    scope = Column(String(50), primary_key=True)
    total_drives = Column(Integer, nullable=False)
    at_risk = Column(Integer, nullable=False)
    open_alerts = Column(Integer, nullable=False)
    replacements = Column(Integer, nullable=False)
