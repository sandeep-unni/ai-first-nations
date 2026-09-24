"""Dashboard and survey persistence, with temporary demo fixtures."""
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
import os
import threading

class DemoStore:
    demo_mode = True

    def __init__(self):
        self.processing_lock = threading.Lock()
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
        surveys = sorted(surveys, key=lambda item: (str(item['survey_date']), item['survey_id']), reverse=True)
        return surveys[:limit] if limit else surveys

    def list_sites(self):
        return sorted(self.sites, key=lambda site: site['site_name'])

    def survey_history(self, site_id=None):
        return [dict(row, image_count=len(row.get('images', [])))
                for row in self.list_surveys(site_id)]

    def get_survey(self, survey_id):
        return next((dict(s, images=s.get('images', [])) for s in self.surveys
                     if s['survey_id'] == survey_id), None)

    def get_survey_by_code(self, code):
        return next((self.get_survey(s['survey_id']) for s in self.surveys if s.get('survey_code') == code), None)

    def create_survey(self, details, images):
        existing = next((s for s in self.surveys if s.get('survey_code') == details['survey_code']), None)
        if existing:
            return existing['survey_id'], False
        details = dict(details)
        new_site = details.pop('new_site', None)
        if new_site:
            site_id = max((s['site_id'] for s in self.sites), default=0) + 1
            self.sites.append(dict(new_site, site_id=site_id))
            details['site_id'] = site_id
        site = next((s for s in self.sites if s['site_id'] == details['site_id']), None)
        if site is None:
            raise ValueError('Select an existing site.')
        survey_id = max((s['survey_id'] for s in self.surveys), default=0) + 1
        for sequence, image in enumerate(images, 1):
            image.update(image_id=survey_id * 1000 + sequence, analyses=[])
        self.surveys.append(dict(details, survey_id=survey_id, site_name=site['site_name'],
                                 status='pending', images=images))
        return survey_id, True

    @contextmanager
    def next_survey(self, storage_id):
        acquired = self.processing_lock.acquire(blocking=False)
        try:
            yield next((s['survey_id'] for s in self.surveys
                        if s['status'] in ('pending', 'processing') and s.get('images')
                        and all(i['storage_provider'] == 'local' and i['storage_container_id'] == storage_id
                                for i in s['images'])), None) if acquired else None
        finally:
            if acquired:
                self.processing_lock.release()

    def set_status(self, survey_id, status):
        next(s for s in self.surveys if s['survey_id'] == survey_id)['status'] = status

    def retry_survey(self, survey_id):
        if self.get_survey(survey_id)['status'] == 'failed':
            self.set_status(survey_id, 'pending')

    def register_models(self, models):
        return {task: index for index, task in enumerate(models, 1)}

    def save_analysis(self, image_id, results):
        image = next(i for s in self.surveys for i in s.get('images', []) if i['image_id'] == image_id)
        image['analyses'] = results

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

    def list_sites(self):
        with self._connect() as conn:
            return conn.execute('select site_id, site_name, region, state from site order by site_name').fetchall()

    def survey_history(self, site_id=None):
        where = 'where v.site_id = %s' if site_id is not None else ''
        with self._connect() as conn:
            return conn.execute(f'''
                select v.*, s.site_name,
                       (select count(*) from image i where i.survey_id=v.survey_id) as image_count
                from survey v join site s on s.site_id=v.site_id
                {where} order by v.survey_date desc, v.survey_id desc
            ''', (site_id,) if site_id is not None else ()).fetchall()

    def get_survey(self, survey_id):
        with self._connect() as conn:
            survey = conn.execute('''select v.*, s.site_name from survey v
                join site s on s.site_id=v.site_id where v.survey_id=%s''', (survey_id,)).fetchone()
            if survey:
                survey['images'] = conn.execute('''select * from image where survey_id=%s
                    order by capture_sequence nulls last, image_id''', (survey_id,)).fetchall()
                results = conn.execute('''select a.*,
                    coalesce((select jsonb_object_agg(p.class_label, p.probability)
                              from class_probability p where p.analysis_id=a.analysis_id), '{}'::jsonb) as probabilities
                    from analysis_result a join image i on i.image_id=a.image_id
                    where i.survey_id=%s order by a.analysis_id''', (survey_id,)).fetchall()
                by_image = {}
                for result in results:
                    by_image.setdefault(result['image_id'], {})[result['analysis_type']] = result
                for image in survey['images']:
                    image['analyses'] = list(by_image.get(image['image_id'], {}).values())
            return survey

    def get_survey_by_code(self, code):
        with self._connect() as conn:
            row = conn.execute('select survey_id from survey where survey_code=%s', (code,)).fetchone()
        return self.get_survey(row['survey_id']) if row else None

    def create_survey(self, details, images):
        # One transaction: a survey and all its image records succeed together.
        with self._connect() as conn:
            # Serialize repeat submissions before creating the associated site.
            conn.execute('select pg_advisory_xact_lock(hashtextextended(%s, 0))', (details['survey_code'],))
            existing = conn.execute('select survey_id from survey where survey_code=%s',
                                    (details['survey_code'],)).fetchone()
            if existing:
                return existing['survey_id'], False
            details = dict(details)
            new_site = details.pop('new_site', None)
            if new_site:
                details['site_id'] = conn.execute('''insert into site
                    (site_code, site_name, region, state, country, latitude, longitude, description)
                    values (%(site_code)s, %(site_name)s, %(region)s, %(state)s, %(country)s,
                            %(latitude)s, %(longitude)s, %(description)s) returning site_id''', new_site).fetchone()['site_id']
            row = conn.execute('''insert into survey
                (survey_code, site_id, survey_name, survey_date, survey_type, notes, status)
                values (%(survey_code)s, %(site_id)s, %(survey_name)s, %(survey_date)s,
                        %(survey_type)s, %(notes)s, 'pending')
                on conflict (survey_code) do nothing returning survey_id''', details).fetchone()
            if row is None:
                existing = conn.execute('select survey_id from survey where survey_code=%s',
                                        (details['survey_code'],)).fetchone()
                return existing['survey_id'], False
            survey_id = row['survey_id']
            from psycopg.types.json import Jsonb
            for sequence, image in enumerate(images, 1):
                conn.execute('''insert into image
                    (survey_id, filename, file_type, file_size_bytes, storage_provider,
                     storage_container_id, storage_item_id, storage_url, width, height,
                     band_count, capture_sequence, capture_date, latitude, longitude,
                     absolute_altitude, relative_altitude, positioning_status, camera_model, source_metadata)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s)''',
                    (survey_id, image['filename'], image['file_type'], image['file_size_bytes'],
                     image['storage_provider'], image['storage_container_id'],
                     image['storage_item_id'], image['storage_url'], image['width'],
                     image['height'], image['band_count'], sequence, image.get('capture_date'),
                     image.get('latitude'), image.get('longitude'), image.get('absolute_altitude'),
                     image.get('relative_altitude'), image.get('positioning_status'), image.get('camera_model'),
                     Jsonb(image.get('source_metadata', {}))))
        return survey_id, True

    @contextmanager
    def next_survey(self, storage_id):
        # Session lock covers inference without holding an open transaction. A crash
        # releases it automatically; the next process resumes 'processing' surveys.
        with self._connect() as conn:
            conn.autocommit = True
            acquired = conn.execute('select pg_try_advisory_lock(71420624) as acquired').fetchone()['acquired']
            try:
                row = conn.execute('''select s.survey_id from survey s
                    where s.status in ('pending', 'processing')
                      and exists (select 1 from image i where i.survey_id=s.survey_id)
                      and not exists (select 1 from image i where i.survey_id=s.survey_id
                          and (i.storage_provider <> 'local' or i.storage_container_id is distinct from %s))
                    order by s.survey_id limit 1''', (storage_id,)).fetchone() if acquired else None
                yield row['survey_id'] if row else None
            finally:
                if acquired:
                    conn.execute('select pg_advisory_unlock(71420624)')

    def set_status(self, survey_id, status):
        with self._connect() as conn:
            conn.execute('update survey set status=%s where survey_id=%s', (status, survey_id))

    def retry_survey(self, survey_id):
        with self._connect() as conn:
            conn.execute("update survey set status='pending' where survey_id=%s and status='failed'", (survey_id,))

    def register_models(self, models):
        ids = {}
        with self._connect() as conn:
            conn.execute('select pg_advisory_xact_lock(71420625)')
            for task, model in models.items():
                row = conn.execute('''select model_id from model
                    where model_name=%s and model_version=%s order by model_id limit 1''',
                    (model['name'], model['version'])).fetchone()
                if not row:
                    row = conn.execute('''insert into model (model_name, model_version, model_type, task, model_path)
                        values (%s, %s, 'EfficientNet-B0', %s, %s) returning model_id''',
                        (model['name'], model['version'], task, model['path'])).fetchone()
                ids[task] = row['model_id']
        return ids

    def save_analysis(self, image_id, results):
        with self._connect() as conn:
            for result in results:
                # Replacing a failed attempt is atomic, including its probabilities.
                params = (image_id, result['model_id'], result['analysis_type'])
                conn.execute('''delete from class_probability where analysis_id in
                    (select analysis_id from analysis_result where image_id=%s and model_id=%s and analysis_type=%s)''', params)
                conn.execute('delete from analysis_result where image_id=%s and model_id=%s and analysis_type=%s', params)
                row = conn.execute('''insert into analysis_result
                    (image_id, model_id, analysis_type, predicted_class, confidence, status, processed_at, error_message)
                    values (%s, %s, %s, %s, %s, %s, now(), %s) returning analysis_id''',
                    (*params, result.get('predicted_class'), result.get('confidence'), result['status'],
                     result.get('error_message'))).fetchone()
                for label, probability in result.get('probabilities', {}).items():
                    conn.execute('''insert into class_probability (analysis_id, class_label, probability)
                        values (%s, %s, %s)''', (row['analysis_id'], label, probability))

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
    if demo_mode:
        return DemoStore()
    if not database_url:
        raise RuntimeError('DATABASE_URL is required when AIFN_DEMO_MODE=false.')
    return PostgresStore(database_url)
