-- AIFN Mangrove Survey Platform — PostgreSQL Schema
-- Matches the finalized ERD (Site, Survey, DatasetSource, Image, Model,
-- Species, AnalysisResult, ClassProbability, Report)

-- ---------------------------------------------------------------------
-- SITE
-- ---------------------------------------------------------------------
CREATE TABLE site (
    site_id       SERIAL PRIMARY KEY,
    site_name     VARCHAR(255) NOT NULL,
    latitude      NUMERIC(9,6),
    longitude     NUMERIC(9,6),
    region        VARCHAR(255),
    state         VARCHAR(100),
    country       VARCHAR(100),
    description   TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- SURVEY
-- ---------------------------------------------------------------------
CREATE TABLE survey (
    survey_id     SERIAL PRIMARY KEY,
    site_id       INTEGER NOT NULL REFERENCES site(site_id),
    survey_name   VARCHAR(255),
    survey_date   DATE NOT NULL,
    survey_type   VARCHAR(100),
    notes         TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    status        VARCHAR(50) NOT NULL DEFAULT 'pending'
);

-- ---------------------------------------------------------------------
-- DATASET_SOURCE  (provenance for external/placeholder imagery)
-- ---------------------------------------------------------------------
CREATE TABLE dataset_source (
    dataset_source_id  SERIAL PRIMARY KEY,
    dataset_name       VARCHAR(255) NOT NULL,
    provider           VARCHAR(255),
    source_type        VARCHAR(100),
    source_url         VARCHAR(500),
    licence             VARCHAR(255),
    geographic_origin  VARCHAR(255),
    description        TEXT,
    access_date        DATE,
    notes              TEXT
);

-- ---------------------------------------------------------------------
-- IMAGE  (belongs to a Survey OR a DatasetSource, not necessarily both)
-- ---------------------------------------------------------------------
CREATE TABLE image (
    image_id            SERIAL PRIMARY KEY,
    survey_id           INTEGER REFERENCES survey(survey_id),
    dataset_source_id   INTEGER REFERENCES dataset_source(dataset_source_id),
    filename             VARCHAR(255) NOT NULL,
    file_path            VARCHAR(500) NOT NULL,
    file_type            VARCHAR(50),
    width                INTEGER,
    height               INTEGER,
    band_count           INTEGER,
    band_configuration   VARCHAR(100),
    capture_date         TIMESTAMP,
    latitude             NUMERIC(9,6),
    longitude            NUMERIC(9,6),
    uploaded_at           TIMESTAMP NOT NULL DEFAULT now(),
    notes                TEXT,
    -- an image should trace back to at least one source
    CONSTRAINT image_has_a_source CHECK (
        survey_id IS NOT NULL OR dataset_source_id IS NOT NULL
    )
);

-- ---------------------------------------------------------------------
-- MODEL  (registered ML model / version)
-- ---------------------------------------------------------------------
CREATE TABLE model (
    model_id       SERIAL PRIMARY KEY,
    model_name     VARCHAR(255) NOT NULL,
    model_version  VARCHAR(50) NOT NULL,
    model_type     VARCHAR(100),
    task           VARCHAR(100),
    model_path     VARCHAR(500),
    description    TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- SPECIES  (reference table for class labels — currently colour
-- morphotype: orange/red/yellow, not taxonomic species)
-- ---------------------------------------------------------------------
CREATE TABLE species (
    species_id       SERIAL PRIMARY KEY,
    common_name      VARCHAR(255) NOT NULL,
    scientific_name  VARCHAR(255),
    description      TEXT
);

-- ---------------------------------------------------------------------
-- ANALYSIS_RESULT  (one model run against one image)
-- ---------------------------------------------------------------------
CREATE TABLE analysis_result (
    analysis_id           SERIAL PRIMARY KEY,
    image_id               INTEGER NOT NULL REFERENCES image(image_id),
    model_id                INTEGER NOT NULL REFERENCES model(model_id),
    predicted_species_id    INTEGER REFERENCES species(species_id),
    analysis_type           VARCHAR(50) NOT NULL,   -- e.g. 'binary' | 'multiclass'
    predicted_class         VARCHAR(100),
    confidence               NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    status                   VARCHAR(50) NOT NULL DEFAULT 'pending',
    processed_at             TIMESTAMP,
    error_message            TEXT
);

-- ---------------------------------------------------------------------
-- CLASS_PROBABILITY  (full probability breakdown per analysis run)
-- ---------------------------------------------------------------------
CREATE TABLE class_probability (
    probability_id   SERIAL PRIMARY KEY,
    analysis_id       INTEGER NOT NULL REFERENCES analysis_result(analysis_id),
    species_id         INTEGER REFERENCES species(species_id),
    class_label        VARCHAR(100) NOT NULL,
    probability         NUMERIC(5,4) NOT NULL CHECK (probability BETWEEN 0 AND 1),
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- REPORT  (per-survey downloadable export)
-- ---------------------------------------------------------------------
CREATE TABLE report (
    report_id       SERIAL PRIMARY KEY,
    survey_id        INTEGER NOT NULL REFERENCES survey(survey_id),
    report_title      VARCHAR(255),
    report_type       VARCHAR(100),
    file_path         VARCHAR(500) NOT NULL,
    generated_at       TIMESTAMP NOT NULL DEFAULT now(),
    generated_by       VARCHAR(255),
    summary_notes      TEXT
);

-- ---------------------------------------------------------------------
-- Helpful indexes for common lookups
-- ---------------------------------------------------------------------
CREATE INDEX idx_survey_site_id ON survey(site_id);
CREATE INDEX idx_image_survey_id ON image(survey_id);
CREATE INDEX idx_image_dataset_source_id ON image(dataset_source_id);
CREATE INDEX idx_analysis_image_id ON analysis_result(image_id);
CREATE INDEX idx_class_probability_analysis_id ON class_probability(analysis_id);
CREATE INDEX idx_report_survey_id ON report(survey_id);
