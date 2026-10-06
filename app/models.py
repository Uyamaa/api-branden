from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Text
from .database import Base


class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, index=True)
    full_Name = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    role = Column(String(20), nullable=False)


class DataCenter(Base):
    __tablename__ = "dataCenter"
    data_center_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    location = Column(String(100), nullable=False)


class HardDrive(Base):
    __tablename__ = "hardDrive"
    drive_id = Column(Integer, primary_key=True, index=True)
    data_center_id = Column(Integer, ForeignKey("dataCenter.data_center_id"), nullable=False)
    serial_number = Column(String(50), unique=True, nullable=False)
    model = Column(String(50), nullable=False)
    capacity = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)


class SmartReading(Base):
    __tablename__ = "smartReading"
    reading_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hardDrive.drive_id"), nullable=False)
    temperature = Column(Float, nullable=False)
    power_on_hours = Column(Integer, nullable=False)
    reallocatedSectors = Column(Integer, nullable=False)
    spinRetryCount = Column(Integer, nullable=False)
    end_to_end_error = Column(Integer, nullable=False)
    reported_uncorrectable = Column(Integer, nullable=False)
    command_timeout = Column(Integer, nullable=False)
    current_pending_sector = Column(Integer, nullable=False)
    offline_uncorrectable = Column(Integer, nullable=False)


class Prediction(Base):
    __tablename__ = "prediction"
    prediction_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hardDrive.drive_id"), nullable=False)
    predicted_failure_date = Column(Date, nullable=False)
    risk_level = Column(String(20), nullable=False)
    confidence_level = Column(Float, nullable=False)


class Maintenance(Base):
    __tablename__ = "maintenance"
    maintenance_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hardDrive.drive_id"), nullable=False)
    dataCenterID = Column(Integer, ForeignKey("dataCenter.data_center_id"), nullable=False)
    maintenance_date = Column(Date, nullable=False)
    maintenance_type = Column(String(50), nullable=False)
    performed_by = Column(Integer, ForeignKey("users.user_id"), nullable=False)


class Alert(Base):
    __tablename__ = "alert"
    alert_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hardDrive.drive_id"), nullable=False)
    prediction_id = Column(Integer, ForeignKey("prediction.prediction_id"), nullable=False)
    data_center_id = Column(Integer, ForeignKey("dataCenter.data_center_id"), nullable=False)
    alert_date = Column(Date, nullable=False)
    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    outcome = Column(Text, nullable=False)


class Replacement(Base):
    __tablename__ = "replacement"
    replacement_id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("hardDrive.drive_id"), nullable=False)
    data_center_id = Column(Integer, ForeignKey("dataCenter.data_center_id"), nullable=False)
    replacement_date = Column(Date, nullable=False)
    replaced_by = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    reason = Column(Text, nullable=False)
    new_drive_id = Column(Integer, nullable=False)