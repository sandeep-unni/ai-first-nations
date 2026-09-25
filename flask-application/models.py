"""
models.py — SQLAlchemy models

These mirror schema.sql exactly — if you change one, change the other.
"""
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, Date, DateTime,
    ForeignKey, BigInteger, CheckConstraint, UniqueConstraint, text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
from sqlalchemy.dialects.postgresql import JSONB


class Site(Base):
    __tablename__ = "site"

    site_id = Column(Integer, primary_key=True)
    site_code = Column(String(50), nullable=False, unique=True,server_default=text(
        "'SITE-' || LPAD(nextval('site_code_seq')::text, 6, '0')"))
    
    site_name = Column(String(255), nullable=False)
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    region = Column(String(255))
    state = Column(String(100))
    country = Column(String(100))
    description = Column(Text)
    created_at = Column(DateTime, server_default=func.now())

    surveys = relationship("Survey", back_populates="site")


class Survey(Base):
    __tablename__ = "survey"

    survey_id = Column(Integer, primary_key=True)
    survey_code = Column(String(50), nullable=False, unique=True)
    site_id = Column(Integer, ForeignKey("site.site_id"), nullable=False)
    survey_name = Column(String(255))
    survey_date = Column(Date, nullable=False)
    survey_type = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    status = Column(String(50), nullable=False, default="pending")

    site = relationship("Site", back_populates="surveys")
    images = relationship("Image", back_populates="survey")
    reports = relationship("Report", back_populates="survey")
    survey_files = relationship("SurveyFile", back_populates="survey")


class SurveyFile(Base):
    __tablename__ = "survey_file"

    survey_file_id = Column(Integer, primary_key=True)

    survey_id = Column(Integer, ForeignKey("survey.survey_id"), nullable=False)

    file_type = Column(String(10), nullable=False)
    filename = Column(String(255), nullable=False)
    file_size_bytes = Column(BigInteger)
    format_version = Column(String(50))
    # SHA-256 fingerprint used to verify file integrity and detect duplicates
    checksum_sha256 = Column(String(64))

    storage_provider = Column(String(50), nullable=False)
    storage_container_id = Column(String(255))
    storage_item_id = Column(String(500))
    storage_url = Column(Text, nullable=False)

    uploaded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),)

    survey = relationship("Survey", back_populates="survey_files",)

    __table_args__ = (
        CheckConstraint(
            "file_type IN ('MRK', 'NAV', 'OBS', 'RTK')",
            name="survey_file_type_check",
        ),
        UniqueConstraint(
            "survey_id",
            "file_type",
            name="survey_file_type_unique",
        ),
        UniqueConstraint(
            "storage_provider",
            "storage_container_id",
            "storage_item_id",
            name="survey_file_storage_unique",
        ),
    )


class Image(Base):
    __tablename__ = "image"

    image_id = Column(Integer, primary_key=True)
    survey_id = Column(Integer, ForeignKey("survey.survey_id"), nullable=False)

    filename = Column(String(255), nullable=False)
    file_type = Column(String(50))
    file_size_bytes = Column(BigInteger)

    storage_provider = Column(String(50), nullable=False)
    storage_container_id = Column(String(255))
    storage_item_id = Column(String(500))
    storage_url = Column(Text, nullable=False)

    width = Column(Integer)
    height = Column(Integer)
    band_count = Column(Integer)
    band_configuration = Column(String(100))
    capture_sequence = Column(Integer)
    capture_date = Column(DateTime(timezone=True))

    latitude = Column(Numeric(12, 9))
    longitude = Column(Numeric(12, 9))
    absolute_altitude = Column(Numeric(10, 3))
    relative_altitude = Column(Numeric(10, 3))

    positioning_status = Column(String(20))
    rtk_std_latitude = Column(Numeric(10, 6))
    rtk_std_longitude = Column(Numeric(10, 6))
    rtk_std_height = Column(Numeric(10, 6))

    camera_model = Column(String(100))
    source_metadata = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    uploaded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    survey = relationship("Survey", back_populates="images")
    analysis_results = relationship("AnalysisResult", back_populates="image")

    __table_args__ = (
        UniqueConstraint(
            "survey_id",
            "capture_sequence",
            name="image_capture_sequence_unique",
        ),

        UniqueConstraint(
            "storage_provider",
            "storage_container_id",
            "storage_item_id",
            name="image_storage_unique",
        ),
    )


class Model(Base):
    __tablename__ = "model"

    model_id = Column(Integer, primary_key=True)
    model_name = Column(String(255), nullable=False)
    model_version = Column(String(50), nullable=False)
    model_type = Column(String(100))
    task = Column(String(100))
    model_path = Column(String(500))
    description = Column(Text)
    created_at = Column(DateTime, server_default=func.now())

    analysis_results = relationship("AnalysisResult", back_populates="model")


class Species(Base):
    __tablename__ = "species"

    species_id = Column(Integer, primary_key=True)
    common_name = Column(String(255), nullable=False)
    scientific_name = Column(String(255))
    description = Column(Text)


class AnalysisResult(Base):
    __tablename__ = "analysis_result"

    analysis_id = Column(Integer, primary_key=True)
    image_id = Column(Integer, ForeignKey("image.image_id"), nullable=False)
    model_id = Column(Integer, ForeignKey("model.model_id"), nullable=False)
    predicted_species_id = Column(Integer, ForeignKey("species.species_id"))
    analysis_type = Column(String(50), nullable=False)  
    predicted_class = Column(String(100))
    confidence = Column(Numeric(5, 4))
    status = Column(String(50), nullable=False, default="pending")
    processed_at = Column(DateTime)
    error_message = Column(Text)

    image = relationship("Image", back_populates="analysis_results")
    model = relationship("Model", back_populates="analysis_results")
    class_probabilities = relationship("ClassProbability", back_populates="analysis_result")

    __table_args__ = (
        CheckConstraint(
            "analysis_type IN "
            "('binary_detection', 'species_classification')",
            name="analysis_result_type_check",
        ),
    )


class ClassProbability(Base):
    __tablename__ = "class_probability"

    probability_id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey("analysis_result.analysis_id"), nullable=False)
    species_id = Column(Integer, ForeignKey("species.species_id"))
    class_label = Column(String(100), nullable=False)
    probability = Column(Numeric(5, 4), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    analysis_result = relationship("AnalysisResult", back_populates="class_probabilities")


class Report(Base):
    __tablename__ = "report"

    report_id = Column(Integer, primary_key=True)
    survey_id = Column(Integer, ForeignKey("survey.survey_id"), nullable=False)
    report_title = Column(String(255))
    report_type = Column(String(100))
    filename = Column(String(255), nullable=False)
    file_size_bytes = Column(BigInteger)

    # SHA-256 fingerprint used to verify file integrity
    checksum_sha256 = Column(String(64))

    storage_provider = Column(String(50), nullable=False)
    storage_container_id = Column(String(255), nullable=False)
    storage_item_id = Column(String(500), nullable=False)
    storage_url = Column(Text)
    generated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),)
    generated_by = Column(String(255))
    summary_notes = Column(Text)

    survey = relationship("Survey", back_populates="reports")

    __table_args__ = (
        UniqueConstraint(
            "storage_provider",
            "storage_container_id",
            "storage_item_id",
            name="report_storage_unique",
        ),
    )