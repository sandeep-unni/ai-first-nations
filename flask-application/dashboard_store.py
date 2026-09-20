"""Read-only dashboard data, including the reference dashboard demo fixtures."""
from collections import Counter
from datetime import date, datetime
import os

class DemoStore:
    demo_mode = True

    def __init__(self):
        self.sites = [{'site_id': 1, 'site_name': 'Koori Beach', 'region': 'Victoria', 'state': 'VIC', 'country': 'Australia', 'latitude': -38.28, 'longitude': 144.62, 'description': 'Coastal monitoring site.'}, {'site_id': 2, 'site_name': 'Mill Point', 'region': 'Western Australia', 'state': 'WA', 'country': 'Australia', 'latitude': -32.06, 'longitude': 115.74, 'description': 'Mangrove monitoring site.'}, {'site_id': 3, 'site_name': 'Reef Creek', 'region': 'Queensland', 'state': 'QLD', 'country': 'Australia', 'latitude': -17.52, 'longitude': 146.03, 'description': 'Queensland survey location.'}, {'site_id': 4, 'site_name': 'Wadawurrung Estuary', 'region': 'Victoria', 'state': 'VIC', 'country': 'Australia', 'latitude': -38.15, 'longitude': 144.36, 'description': 'Estuary monitoring location.'}]
        self.surveys = [self._survey(15, 1, 'Koori Beach - May 12, 2025', '2025-05-12', 'yellow', 0.91, {'yellow': 0.91, 'orange': 0.06, 'red': 0.03}), self._survey(14, 2, 'Mill Point - May 10, 2025', '2025-05-10', 'orange', 0.83, {'yellow': 0.1, 'orange': 0.83, 'red': 0.07}), self._survey(13, 3, 'Reef Creek - May 8, 2025', '2025-05-08', 'red', 0.79, {'yellow': 0.11, 'orange': 0.1, 'red': 0.79}), self._survey(12, 4, 'Wadawurrung Estuary - May 5, 2025', '2025-05-05', 'yellow', 0.75, {'yellow': 0.75, 'orange': 0.16, 'red': 0.09}), self._survey(11, 2, 'Mill Point - May 3, 2025', '2025-05-03', 'orange', 0.71, {'yellow': 0.17, 'orange': 0.71, 'red': 0.12}), self._survey(10, 1, 'Koori Beach - Apr 12, 2025', '2025-04-12', 'yellow', 0.84, {'yellow': 0.84, 'orange': 0.1, 'red': 0.06})]

    def _survey(self, survey_id, site_id, survey_name, survey_date, species, confidence, probabilities):
        site = next((site for site in self.sites if site['site_id'] == site_id))
        return {'survey_id': survey_id, 'site_id': site_id, 'site_name': site['site_name'], 'survey_name': survey_name, 'survey_date': survey_date, 'survey_type': 'drone imagery', 'notes': '', 'status': 'completed', 'filename': 'sample_orthomosaic.jpg', 'stored_name': None, 'mangrove_detected': True, 'binary_confidence': 0.92, 'predicted_species': species, 'species_confidence': confidence, 'probabilities': probabilities, 'analysis_source': 'demo'}

    def dashboard_summary(self):
        today = date.today()
        this_month = sum((1 for survey in self.surveys if str(survey['survey_date']).startswith(f'{today.year:04d}-{today.month:02d}')))
        completed = sum((1 for survey in self.surveys if survey['status'] == 'completed'))
        species = Counter((survey.get('predicted_species') for survey in self.surveys if survey.get('predicted_species')))
        return {'total_sites': len(self.sites), 'total_surveys': len(self.surveys), 'surveys_this_month': this_month, 'completed_surveys': completed, 'species_counts': dict(species)}

    def list_surveys(self, site_id=None, limit=None):
        surveys = self.surveys
        if site_id is not None:
            surveys = [survey for survey in surveys if survey['site_id'] == site_id]
        surveys = sorted(surveys, key=lambda item: item['survey_date'], reverse=True)
        return surveys[:limit] if limit else surveys

class PostgresStore:
    demo_mode = False

    def __init__(self, database_url):
        self.database_url = database_url
        self.binary_model_id = int(os.getenv('AIFN_BINARY_MODEL_ID', '1'))
        self.species_model_id = int(os.getenv('AIFN_SPECIES_MODEL_ID', '2'))

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def dashboard_summary(self):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute('select count(*) as count from site')
            total_sites = cur.fetchone()['count']
            cur.execute('select count(*) as count from survey')
            total_surveys = cur.fetchone()['count']
            cur.execute("select count(*) as count from survey where date_trunc('month', survey_date::timestamp) = date_trunc('month', current_date::timestamp)")
            surveys_this_month = cur.fetchone()['count']
            cur.execute("select count(*) as count from survey where status = 'completed'")
            completed_surveys = cur.fetchone()['count']
            cur.execute("select predicted_class, count(*) as count from analysis_result where analysis_type = 'species_classification' and status = 'completed' group by predicted_class")
            species_counts = {row['predicted_class']: row['count'] for row in cur.fetchall() if row['predicted_class']}
        return {'total_sites': total_sites, 'total_surveys': total_surveys, 'surveys_this_month': surveys_this_month, 'completed_surveys': completed_surveys, 'species_counts': species_counts}

    def list_surveys(self, site_id=None, limit=None):
        where = 'where v.site_id = %s' if site_id is not None else ''
        params = [site_id] if site_id is not None else []
        limit_sql = ' limit %s' if limit else ''
        if limit:
            params.append(limit)
        query = f"\n            select v.survey_id, v.site_id, s.site_name, v.survey_name, v.survey_date,\n                   v.survey_type, v.notes, v.status,\n                   i.filename, i.storage_url AS file_path,\n                   b.predicted_class as binary_class,\n                   b.confidence as binary_confidence,\n                   sp.predicted_class as predicted_species,\n                   sp.confidence as species_confidence\n            from survey v\n            join site s on s.site_id = v.site_id\n            left join lateral (\n                select * from image ix where ix.survey_id = v.survey_id order by ix.uploaded_at desc limit 1\n            ) i on true\n            left join lateral (\n                select * from analysis_result ar\n                where ar.image_id = i.image_id and ar.analysis_type = 'binary_detection'\n                order by ar.processed_at desc nulls last, ar.analysis_id desc limit 1\n            ) b on true\n            left join lateral (\n                select * from analysis_result ar\n                where ar.image_id = i.image_id and ar.analysis_type = 'species_classification'\n                order by ar.processed_at desc nulls last, ar.analysis_id desc limit 1\n            ) sp on true\n            {where}\n            order by v.survey_date desc, v.survey_id desc\n            {limit_sql}\n        "
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            row['mangrove_detected'] = row.pop('binary_class', None) == 'Mangrove'
            row['probabilities'] = self._probabilities_for_survey(row['survey_id'])
        return rows

    def _probabilities_for_survey(self, survey_id):
        query = "\n            select cp.class_label, cp.probability\n            from class_probability cp\n            join analysis_result ar on ar.analysis_id = cp.analysis_id\n            join image i on i.image_id = ar.image_id\n            where i.survey_id = %s and ar.analysis_type = 'species_classification'\n            order by cp.class_label\n        "
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, (survey_id,))
            return {row['class_label']: float(row['probability']) for row in cur.fetchall()}

def get_store():
    demo_mode = os.getenv('AIFN_DEMO_MODE', 'true').lower() == 'true'
    database_url = os.getenv('DATABASE_URL')
    if demo_mode or not database_url:
        return DemoStore()
    return PostgresStore(database_url)
