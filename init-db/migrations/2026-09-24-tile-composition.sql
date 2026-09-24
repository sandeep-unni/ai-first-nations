-- Adds tile_composition to a database created before 2026-09-24.
-- New databases get it from 01-schema.sql. Safe to run more than once.
CREATE TABLE IF NOT EXISTS tile_composition (
    tile_composition_id  SERIAL PRIMARY KEY,
    analysis_id           INTEGER NOT NULL REFERENCES analysis_result(analysis_id) ON DELETE CASCADE,
    class_label           VARCHAR(100) NOT NULL,
    tile_count            INTEGER NOT NULL CHECK (tile_count >= 0),

    CONSTRAINT tile_composition_unique UNIQUE (
        analysis_id,
        class_label
    )
);
