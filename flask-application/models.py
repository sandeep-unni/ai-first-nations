"""
models.py — SQLAlchemy models matching the finalized ERD:
Site, Survey, DatasetSource, Image, Model, Species,
AnalysisResult, ClassProbability, Report.

These mirror schema.sql exactly — if you change one, change the other.
"""
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, Date, DateTime,
    ForeignKey, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Site(Base):
    __tablename__ = "site"

    site_id = Column(Integer, primary_key=True)
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


class DatasetSource(Base):
    __tablename__ = "dataset_source"

    dataset_source_id = Column(Integer, primary_key=True)
    dataset_name = Column(String(255), nullable=False)
    provider = Column(String(255))
    source_type = Column(String(100))
    source_url = Column(String(500))
    licence = Column(String(255))
    geographic_origin = Column(String(255))
    description = Column(Text)
    access_date = Column(Date)
    notes = Column(Text)

    images = relationship("Image", back_populates="dataset_source")


class Image(Base):
    __tablename__ = "image"

    image_id = Column(Integer, primary_key=True)
    survey_id = Column(Integer, ForeignKey("survey.survey_id"))
    dataset_source_id = Column(Integer, ForeignKey("dataset_source.dataset_source_id"))
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_type = Column(String(50))
    width = Column(Integer)
    height = Column(Integer)
    band_count = Column(Integer)
    band_configuration = Column(String(100))
    capture_date = Column(DateTime)
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    uploaded_at = Column(DateTime, server_default=func.now())
    notes = Column(Text)

    __table_args__ = (
        CheckConstraint(
            "survey_id IS NOT NULL OR dataset_source_id IS NOT NULL",
            name="image_has_a_source",
        ),
    )

    survey = relationship("Survey", back_populates="images")
    dataset_source = relationship("DatasetSource", back_populates="images")
    analysis_results = relationship("AnalysisResult", back_populates="image")


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
    analysis_type = Column(String(50), nullable=False)  # 'binary' | 'multiclass'
    predicted_class = Column(String(100))
    confidence = Column(Numeric(5, 4))
    status = Column(String(50), nullable=False, default="pending")
    processed_at = Column(DateTime)
    error_message = Column(Text)

    image = relationship("Image", back_populates="analysis_results")
    model = relationship("Model", back_populates="analysis_results")
    class_probabilities = relationship("ClassProbability", back_populates="analysis_result")


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
    file_path = Column(String(500), nullable=False)
    generated_at = Column(DateTime, server_default=func.now())
    generated_by = Column(String(255))
    summary_notes = Column(Text)

    survey = relationship("Survey", back_populates="reports")
