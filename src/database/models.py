from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


# LOCATION
class Location(Base):
    __tablename__ = "location"

    location_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    administrative_level: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    latitude: Mapped[Decimal] = mapped_column(
        Numeric(9, 6),
        nullable=False,
    )

    longitude: Mapped[Decimal] = mapped_column(
        Numeric(9, 6),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    weather_records = relationship(
        "WeatherData",
        back_populates="location",
    )

    flood_events = relationship(
        "FloodEvent",
        back_populates="location",
    )

    predictions = relationship(
        "FloodRiskPrediction",
        back_populates="location",
    )

    environmental_features = relationship(
        "EnvironmentalFeature",
        back_populates="location",
    )

    __table_args__ = (
        Index(
            "ix_location_coordinates",
            "latitude",
            "longitude",
        ),
    )

# DATA SOURCE
class DataSource(Base):
    __tablename__ = "data_source"

    source_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )

    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    source_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    update_frequency: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    weather_records = relationship(
        "WeatherData",
        back_populates="source",
    )

    flood_events = relationship(
        "FloodEvent",
        back_populates="source",
    )

    environmental_features = relationship(
        "EnvironmentalFeature",
        back_populates="source",
    )

# WEATHER DATA
class WeatherData(Base):
    __tablename__ = "weather_data"

    weather_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    location_id: Mapped[int] = mapped_column(
        ForeignKey("location.location_id"),
        nullable=False,
    )

    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.source_id"),
        nullable=False,
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    temperature: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )

    humidity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    rainfall: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 2),
        nullable=True,
    )

    wind_speed: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )

    pressure: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 2),
        nullable=True,
    )

    # ERA5-Land volumetric soil-water content
    soil_moisture_layer1: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4),
        nullable=True,
    )

    soil_moisture_layer2: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    location = relationship(
        "Location",
        back_populates="weather_records",
    )

    source = relationship(
        "DataSource",
        back_populates="weather_records",
    )

    __table_args__ = (
       
        UniqueConstraint(
            "location_id",
            "source_id",
            "timestamp",
            name="uq_weather_location_source_timestamp",
        ),

        Index(
            "ix_weather_location_timestamp",
            "location_id",
            "timestamp",
        ),

        Index(
            "ix_weather_source_timestamp",
            "source_id",
            "timestamp",
        ),
    )

# ENVIRONMENTAL FEATURE
class EnvironmentalFeature(Base):
    """
    Processed environmental/GIS attributes extracted by ETL.

    Examples:
        elevation_m
        slope_deg
        built_up_surface
        built_up_fraction
        flood_hazard_score
        vegetation_fraction
        land_cover_class

    Raw GeoTIFFs and boundary files remain outside PostgreSQL.
    """

    __tablename__ = "environmental_feature"

    feature_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    location_id: Mapped[int] = mapped_column(
        ForeignKey("location.location_id"),
        nullable=False,
    )

    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.source_id"),
        nullable=False,
    )

    feature_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    feature_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False,
    )

    unit: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    effective_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    location = relationship(
        "Location",
        back_populates="environmental_features",
    )

    source = relationship(
        "DataSource",
        back_populates="environmental_features",
    )

    __table_args__ = (
        Index(
            "ix_environmental_location_feature_date",
            "location_id",
            "feature_name",
            "effective_date",
        ),

        Index(
            "ix_environmental_source",
            "source_id",
        ),
    )

# FLOOD EVENT
class FloodEvent(Base):
    __tablename__ = "flood_event"

    event_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    location_id: Mapped[int] = mapped_column(
        ForeignKey("location.location_id"),
        nullable=False,
    )

    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.source_id"),
        nullable=False,
    )

    source_event_id: Mapped[Optional[str]] = mapped_column(
        String(150),
        nullable=True,
    )

    event_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    title: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    severity_level: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
    )

    verification_status: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    location = relationship(
        "Location",
        back_populates="flood_events",
    )

    source = relationship(
        "DataSource",
        back_populates="flood_events",
    )

    __table_args__ = (
        Index(
            "ix_flood_event_location_start",
            "location_id",
            "start_time",
        ),

        Index(
            "ix_flood_event_source_event_id",
            "source_id",
            "source_event_id",
        ),
    )

# MODEL
class Model(Base):
    __tablename__ = "model"

    model_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    model_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    version: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    training_start_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    training_end_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    performance_metrics: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    predictions = relationship(
        "FloodRiskPrediction",
        back_populates="model",
    )

    __table_args__ = (
        UniqueConstraint(
            "name",
            "version",
            name="uq_model_name_version",
        ),
    )

# USER ACCOUNT
class UserAccount(Base):
    __tablename__ = "user_account"

    user_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    phone: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
    )

    role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    organization: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    alert_recipients = relationship(
        "AlertRecipient",
        back_populates="user",
    )

# FLOOD RISK PREDICTION
class FloodRiskPrediction(Base):
    __tablename__ = "flood_risk_prediction"

    prediction_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    location_id: Mapped[int] = mapped_column(
        ForeignKey("location.location_id"),
        nullable=False,
    )

    model_id: Mapped[int] = mapped_column(
        ForeignKey("model.model_id"),
        nullable=False,
    )

    prediction_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    forecast_horizon_hours: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    risk_level: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    risk_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    
    prediction_data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    confidence_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    location = relationship(
        "Location",
        back_populates="predictions",
    )

    model = relationship(
        "Model",
        back_populates="predictions",
    )

    alerts = relationship(
        "FloodAlert",
        back_populates="prediction",
    )

    __table_args__ = (
        Index(
            "ix_prediction_location_time",
            "location_id",
            "prediction_time",
        ),

        Index(
            "ix_prediction_risk_level",
            "risk_level",
        ),
    )

# FLOOD ALERT
class FloodAlert(Base):
    __tablename__ = "flood_alert"

    alert_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("flood_risk_prediction.prediction_id"),
        nullable=False,
    )

    alert_level: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    instructions: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    prediction = relationship(
        "FloodRiskPrediction",
        back_populates="alerts",
    )

    recipients = relationship(
        "AlertRecipient",
        back_populates="alert",
    )

    __table_args__ = (
        Index(
            "ix_flood_alert_status_issued",
            "status",
            "issued_at",
        ),
    )


# ALERT RECIPIENT
class AlertRecipient(Base):
    __tablename__ = "alert_recipient"

    recipient_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    alert_id: Mapped[int] = mapped_column(
        ForeignKey("flood_alert.alert_id"),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_account.user_id"),
        nullable=False,
    )

    channel: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    delivery_status: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
    )

    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    alert = relationship(
        "FloodAlert",
        back_populates="recipients",
    )

    user = relationship(
        "UserAccount",
        back_populates="alert_recipients",
    )

    __table_args__ = (
       
        UniqueConstraint(
            "alert_id",
            "user_id",
            "channel",
            name="uq_alert_recipient_channel",
        ),

        Index(
            "ix_alert_recipient_user",
            "user_id",
        ),

        Index(
            "ix_alert_recipient_status",
            "delivery_status",
        ),
    )