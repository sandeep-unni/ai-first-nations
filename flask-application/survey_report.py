"""Downloadable per-survey reports: a standalone HTML summary and a per-image CSV."""
import csv
from io import StringIO

from werkzeug.utils import secure_filename

from survey_processing import tile_composition


def report_filename(survey, survey_code, extension):
    name = secure_filename(str(survey.get('survey_name') or '')) or 'survey'
    return f'{name[:80]}-{survey_code}-report.{extension}'


def image_rows(survey):
    """One flat record per image with its latest detection and classification."""
    rows = []
    for image in survey['images']:
        results = {r['analysis_type']: r for r in image.get('analyses', [])}
        binary = results.get('binary_detection', {})
        classification = results.get('species_classification', {})
        statuses = {r['status'] for r in results.values()}
        status = ('failed' if 'failed' in statuses else
                  'completed' if binary.get('status') == 'completed' else 'pending')
        detected = binary.get('status') == 'completed'
        classified = classification.get('status') == 'completed'
        rows.append(dict(image=image, status=status,
                         mangrove=binary.get('predicted_class') if detected else None,
                         mangrove_confidence=binary.get('confidence') if detected else None,
                         colour_class=classification.get('predicted_class') if classified else None,
                         colour_confidence=classification.get('confidence') if classified else None,
                         classification_skipped=classification.get('status') == 'skipped',
                         tiles=tile_composition(classification['tile_counts'])
                         if classification.get('tile_counts') else None,
                         tile_counts=classification.get('tile_counts') or {}))
    return rows


def _cell(value):
    # Survey names and notes are user text; stop spreadsheets evaluating them as formulas.
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return '' if value is None else value


def _number(value, digits=4):
    return None if value is None else round(float(value), digits)


def report_csv(survey, survey_code):
    rows = image_rows(survey)
    labels = sorted({label for row in rows for label in row['tile_counts']})
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['survey_code', 'survey_name', 'site_name', 'survey_date', 'filename', 'file_type',
                     'width', 'height', 'file_size_bytes', 'capture_date', 'latitude', 'longitude',
                     'absolute_altitude', 'relative_altitude', 'positioning_status', 'rtk_std_latitude',
                     'rtk_std_longitude', 'rtk_std_height', 'camera_model', 'analysis_status',
                     'mangrove_detection', 'mangrove_confidence', 'colour_class', 'colour_class_confidence',
                     'total_tiles', 'mangrove_tiles'] + [f'tiles_{label}' for label in labels])
    for row in rows:
        image = row['image']
        colour = 'skipped (no mangrove)' if row['classification_skipped'] else row['colour_class']
        writer.writerow([_cell(v) for v in [
            survey_code, survey.get('survey_name'), survey.get('site_name'), survey.get('survey_date'),
            image.get('filename'), image.get('file_type'), image.get('width'), image.get('height'),
            image.get('file_size_bytes'), image.get('capture_date'), image.get('latitude'), image.get('longitude'),
            image.get('absolute_altitude'), image.get('relative_altitude'), image.get('positioning_status'),
            image.get('rtk_std_latitude'), image.get('rtk_std_longitude'), image.get('rtk_std_height'),
            image.get('camera_model'), row['status'], row['mangrove'], _number(row['mangrove_confidence']),
            colour, _number(row['colour_confidence']),
            row['tiles']['total_tiles'] if row['tiles'] else None,
            row['tiles']['mangrove_tiles'] if row['tiles'] else None,
        ] + [row['tile_counts'].get(label) for label in labels]])
    # A BOM lets Excel detect UTF-8 in site names and notes.
    return '﻿' + output.getvalue()
