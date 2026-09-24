"""Run with: .venv/bin/python -m unittest discover -s tests -v.

Set TEST_DATABASE_URL to a disposable local PostgreSQL database to also exercise
real transactions in isolated schemas. Never point this at the shared database.
"""
from datetime import date, timedelta
from io import BytesIO
import os
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from flask import Flask
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'flask-application'))
from dashboard import install_dashboard
from dashboard_store import DemoStore, PostgresStore
from survey_processing import process_next, survey_statistics
from survey_flow import image_metadata

MODELS = {task: dict(name='Test ' + task, version='test-v1', path='test.pth')
          for task in ('binary_detection', 'species_classification')}


def prediction(path):
    return {'binary': {'prediction': 'Mangrove', 'confidence': .9, 'probs': [.1, .9]},
            'multi_class': {'predicted_class': 'orange', 'confidence': .8,
                            'probabilities': {'orange': .8, 'red': .1, 'yellow': .1}}}


def image_file(name='drone.jpg', format='JPEG'):
    data = BytesIO()
    Image.new('RGB', (24, 16), (35, 110, 60)).save(data, format=format)
    data.seek(0)
    return data, name


class SurveyFlowTests(unittest.TestCase):
    def make_store(self):
        return DemoStore()

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = self.make_store()
        self.app = Flask(__name__, template_folder=str(ROOT / 'flask-application/templates'))
        self.app.config.update(TESTING=True, SECRET_KEY='test-secret', UPLOAD_FOLDER=self.directory.name,
                               MAX_CONTENT_LENGTH=50 * 1024 * 1024)
        install_dashboard(self.app, self.store)
        self.client = self.app.test_client()
        self.initial_count = len(self.store.survey_history())

    def form_data(self, **changes):
        response = self.client.get('/surveys/new')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        data = {field: re.search(r'name="' + field + r'" value="([^"]+)"', html).group(1)
                for field in ('csrf_token', 'submission_token')}
        data.update(site_id='1', survey_name='Morning drone flight', survey_date=date.today().isoformat(), notes='Clear skies')
        data.update(changes)
        return data

    def post(self, data=None, files=None):
        if data is None:
            data = self.form_data()
        return self.client.post('/surveys/new', data=dict(data, images=files if files is not None else [image_file()]))

    def saved_files(self):
        return list(Path(self.directory.name).rglob('*.*'))

    def test_multiple_images_same_survey_duplicate_names_and_second_visit(self):
        response = self.post(files=[image_file(), image_file(), image_file('map.tif', 'TIFF')])
        self.assertEqual(response.status_code, 303)
        survey_id = int(response.location.rsplit('/', 1)[1])
        survey = self.store.get_survey(survey_id)
        self.assertEqual(survey['site_id'], 1)
        self.assertEqual(survey['status'], 'pending')
        self.assertEqual(len(survey['images']), 3)
        self.assertEqual(len({i['storage_item_id'] for i in survey['images']}), 3)
        detail = self.client.get(response.location)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b'Pending analysis', detail.data)
        for image in survey['images']:
            with self.client.get(image['storage_url']) as response_image:
                self.assertEqual(response_image.status_code, 200)
        second = self.post()
        self.assertEqual(second.status_code, 303)
        self.assertNotEqual(second.location, response.location)
        self.assertEqual(len(self.store.survey_history()), self.initial_count + 2)
        self.assertEqual(self.client.get('/surveys?site_id=1').status_code, 200)

    def test_repeated_submission_does_not_duplicate_records_or_files(self):
        data = self.form_data()
        first = self.post(data)
        second = self.post(data)
        self.assertEqual(first.location, second.location)
        self.assertEqual(len(self.store.survey_history()), self.initial_count + 1)
        self.assertEqual(len(self.saved_files()), 1)

    def test_missing_images_and_invalid_metadata(self):
        for changes in ({'site_id': '99999'}, {'survey_name': ' '}, {'survey_date': 'invalid'},
                        {'survey_date': (date.today() + timedelta(days=1)).isoformat()}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post(self.form_data(**changes)).status_code, 400)
        self.assertEqual(self.post(files=[]).status_code, 400)
        self.assertEqual(len(self.store.survey_history()), self.initial_count)
        self.assertFalse(self.saved_files())

    def test_invalid_image_rolls_back_whole_batch(self):
        response = self.post(files=[image_file(), (BytesIO(b'not an image'), 'broken.png')])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.store.survey_history()), self.initial_count)
        self.assertFalse(self.saved_files())

    def test_content_extension_mismatch_is_rejected(self):
        self.assertEqual(self.post(files=[image_file('fake.png', 'JPEG')]).status_code, 400)
        self.assertFalse(self.saved_files())

    def test_dji_multi_picture_jpeg_is_preserved(self):
        data = BytesIO()
        Image.new('RGB', (24, 16)).save(data, 'MPO', save_all=True,
                                      append_images=[Image.new('RGB', (12, 8))])
        original = data.getvalue()
        data.seek(0)
        response = self.post(files=[(data, 'DJI_original.JPG')])
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.saved_files()[0].read_bytes(), original)

    def test_file_count_and_individual_size_limits(self):
        self.app.config['SURVEY_MAX_IMAGES'] = 1
        self.assertEqual(self.post(files=[image_file(), image_file()]).status_code, 400)
        self.app.config['SURVEY_MAX_IMAGE_BYTES'] = 10
        self.assertEqual(self.post().status_code, 400)
        self.assertFalse(self.saved_files())

    def test_request_limit_has_friendly_error(self):
        self.app.config['SURVEY_MAX_REQUEST_BYTES'] = 100
        response = self.post()
        self.assertEqual(response.status_code, 413)
        self.assertIn(b'Choose a smaller batch', response.data)

    def test_csrf_and_expired_submission_are_rejected(self):
        self.assertEqual(self.post(self.form_data(csrf_token='bad')).status_code, 400)
        self.assertEqual(self.post(self.form_data(submission_token='bad')).status_code, 400)
        self.assertFalse(self.saved_files())

    def test_database_failure_cleans_files_and_preserves_form(self):
        with patch.object(self.store, 'create_survey', side_effect=RuntimeError('Database unavailable')), self.assertLogs(self.app.logger, level='ERROR'):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'Morning drone flight', response.data)
        self.assertFalse(self.saved_files())

    def test_lost_commit_acknowledgement_does_not_delete_originals(self):
        create = self.store.create_survey
        def lost_acknowledgement(details, images):
            create(details, images)
            raise ConnectionError('connection lost after commit')
        with patch.object(self.store, 'create_survey', side_effect=lost_acknowledgement), self.assertLogs(self.app.logger, level='ERROR'):
            response = self.post()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.saved_files()), 1)
        self.assertEqual(self.client.get(response.location).status_code, 200)

    def test_unknown_survey_and_path_traversal(self):
        self.assertEqual(self.client.get('/surveys/999999').status_code, 404)
        self.assertEqual(self.client.get('/survey-images/../../.env').status_code, 404)

    def test_new_site_saved_once_with_survey(self):
        count = len(self.store.list_sites())
        data = self.form_data(site_id='draft:local', new_site=json.dumps({
            'name': 'New mangrove site', 'latitude': -17.6, 'longitude': 146.1, 'country': 'Australia'}))
        response = self.post(data)
        self.assertEqual(response.status_code, 303)
        survey = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))
        self.assertEqual(survey['site_name'], 'New mangrove site')
        self.assertEqual(self.post(data).location, response.location)
        self.assertEqual(len(self.store.list_sites()), count + 1)

    def test_invalid_new_site_creates_nothing(self):
        count = len(self.store.list_sites())
        for site in ({'name': ''}, {'name': 'X', 'latitude': 10},
                     {'name': 'X', 'latitude': 91, 'longitude': 10},
                     {'name': 'X', 'latitude': float('nan'), 'longitude': 10}, [], None):
            with self.subTest(site=site):
                response = self.post(self.form_data(new_site=json.dumps(site)))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.store.list_sites()), count)
        self.assertFalse(self.saved_files())

    def test_server_extracts_and_persists_metadata(self):
        output = BytesIO()
        exif = Image.Exif()
        exif[272] = 'DJI test camera'
        exif[270] = 'DJI description\x00'
        exif[34665] = {36867: '2026:09:14 10:30:00'}
        xmp = b'''<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:d="http://www.dji.com/drone-dji/1.0/" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
            <d:Description d:GpsLatitude="-17.6" d:GpsLongitude="146.1" d:RelativeAltitude="12.2" xmp:CreateDate="2026-09-14T10:30:00+10:00"/></x:xmpmeta>'''
        Image.new('RGB', (24, 16)).save(output, 'JPEG', exif=exif, xmp=xmp)
        original = output.getvalue()
        output.seek(0)
        response = self.post(files=[(output, 'metadata.jpg')])
        self.assertEqual(response.status_code, 303)
        image = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))['images'][0]
        self.assertAlmostEqual(float(image['latitude']), -17.6)
        self.assertAlmostEqual(float(image['relative_altitude']), 12.2)
        self.assertEqual(image['camera_model'], 'DJI test camera')
        self.assertEqual(image['source_metadata']['exif']['ImageDescription'], 'DJI description')
        self.assertEqual(image['capture_date'].year, 2026)
        self.assertEqual(image['source_metadata']['exif']['DateTimeOriginal'], '2026:09:14 10:30:00')
        self.assertEqual(self.saved_files()[0].read_bytes(), original)

    def test_analysis_stores_results_and_probabilities(self):
        response = self.post(files=[image_file(), image_file()])
        self.assertTrue(process_next(self.app, self.store, prediction, MODELS))
        survey = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))
        self.assertEqual(survey['status'], 'completed')
        self.assertEqual(survey_statistics(survey)['classes'], {'orange': 2})
        self.assertEqual(survey_statistics(survey)['completed'], 2)
        for image in survey['images']:
            self.assertEqual(len(image['analyses']), 2)
            classification = next(r for r in image['analyses'] if r['analysis_type'] == 'species_classification')
            self.assertAlmostEqual(float(classification['probabilities']['orange']), .8)
        self.assertEqual(self.client.get(response.location).status_code, 200)
        self.assertFalse(process_next(self.app, self.store, prediction, MODELS))

    def test_tile_composition_percentages(self):
        response = self.post(files=[image_file(), image_file()])
        outputs = iter([{'Non-Mangrove': 2, 'orange': 6, 'red': 2, 'yellow': 0},
                        {'Non-Mangrove': 0, 'orange': 2, 'red': 0, 'yellow': 0}])
        def tiled(path):
            return dict(prediction(path), tiles=next(outputs))
        process_next(self.app, self.store, tiled, MODELS)
        survey = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))
        composition = survey_statistics(survey)['composition']
        self.assertEqual((composition['total_tiles'], composition['mangrove_tiles']), (12, 10))
        self.assertEqual({r['label']: r['percent'] for r in composition['rows']},
                         {'orange': 80.0, 'red': 20.0, 'yellow': 0.0})
        detail = self.client.get(response.location).get_data(as_text=True)
        self.assertIn('Mangrove composition', detail)
        self.assertIn('80.0%', detail)
        self.assertIn('Orange 75% · Red 25% of 8 mangrove tiles', detail)

    def test_sites_page_lists_sites_with_latest_survey(self):
        response = self.post()
        survey_id = int(response.location.rsplit('/', 1)[1])
        html = self.client.get('/sites').get_data(as_text=True)
        site = next(s for s in self.store.site_overview() if s['site_id'] == 1)
        self.assertEqual(site['last_survey_id'], survey_id)
        self.assertEqual(site['last_survey_status'], 'pending')
        self.assertIn(site['site_name'], html)
        self.assertIn('Pending', html)
        self.assertIn('/surveys/new?site_id=1', html)
        process_next(self.app, self.store, lambda path: dict(prediction(path), tiles={'Non-Mangrove': 1, 'orange': 3}), MODELS)
        site = next(s for s in self.store.site_overview() if s['site_id'] == 1)
        self.assertEqual(site['latest_completed_id'], survey_id)
        self.assertEqual({k: int(v) for k, v in site['latest_tiles'].items()}, {'Non-Mangrove': 1, 'orange': 3})
        html = self.client.get('/sites').get_data(as_text=True)
        self.assertIn('Orange 100%', html)
        self.assertIn('75% of imaged tiles are mangrove', html)

    def test_site_detail_shows_latest_composition_and_history(self):
        response = self.post()
        process_next(self.app, self.store, lambda path: dict(prediction(path), tiles={'orange': 3, 'red': 1}), MODELS)
        page = self.client.get('/sites/1')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Mangrove composition', html)
        self.assertIn('75.0%', html)
        self.assertIn(response.location.rsplit('/', 1)[1], html)
        self.assertIn('/surveys/new?site_id=1', html)
        self.assertEqual(self.client.get('/sites/99999').status_code, 404)

    def test_sites_page_settings_and_class_colours_come_from_data(self):
        response = self.post(self.form_data(site_id='draft:local', new_site=json.dumps({
            'name': 'Mapped site', 'latitude': -17.6, 'longitude': 146.1})))
        site_id = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))['site_id']
        process_next(self.app, self.store, lambda path: dict(prediction(path), tiles={'orange': 2, 'teal-ish': 1}), MODELS)
        self.app.config.update(SITES_RECENT_DAYS=7, SITES_MAP_URL='https://maps.example/{lat},{lon}')
        html = self.client.get('/sites').get_data(as_text=True)
        self.assertIn('in the last 7 days', html)
        self.assertIn('https://maps.example/-17.6', html)
        self.assertIn('#e2622a', html)  # validated step for a colour-named class
        # A class that isn't a CSS colour name still gets a generated colour.
        self.assertRegex(html, r'Teal-ish 33%')
        detail = self.client.get(f'/sites/{site_id}').get_data(as_text=True)
        self.assertIn('Orange, Teal-ish are the model', detail)
        self.assertIn('https://maps.example/-17.6', detail)
        self.app.config.update(SITES_MAP_URL='')
        self.assertNotIn('maps.example', self.client.get(f'/sites/{site_id}').get_data(as_text=True))

    def test_results_page_empty_then_summarises_analyses(self):
        html = self.client.get('/results').get_data(as_text=True)
        self.assertIn('No analysis results yet', html)
        response = self.post(files=[image_file(), image_file()])
        survey_id = int(response.location.rsplit('/', 1)[1])
        tiles = iter([{'Non-Mangrove': 1, 'orange': 2, 'teal-ish': 1}, {'orange': 4}])
        process_next(self.app, self.store, lambda path: dict(prediction(path), tiles=next(tiles)), MODELS)
        html = self.client.get('/results').get_data(as_text=True)
        self.assertIn('Mangrove colour mix', html)
        self.assertIn('88%', html)  # 7 of 8 tiles are mangrove
        self.assertIn('Teal-ish', html)
        self.assertIn('conic-gradient(', html)
        self.assertIn('Binary detection', html)
        self.assertIn('Species classification', html)
        self.assertIn('mean confidence 80%', html)
        self.assertIn(f'/surveys/{survey_id}', html)
        self.assertIn('2 / 2 images analysed', html)
        survey = self.store.get_survey(survey_id)
        # Traceability: Survey ID, model version per task and a preview of the original.
        self.assertIn(f'Survey ID <code>{survey["survey_code"]}</code>', html)
        self.assertIn('orange', html)
        self.assertIn('80% confidence', html)
        self.assertIn(survey['images'][0]['storage_url'], html)
        if not self.store.demo_mode:  # the demo store keeps no model records or timestamps
            self.assertIn('<code title="test-v1">test-v1</code>', html)
            self.assertIn('Analysed 20', html)
        site_page = self.client.get('/sites/1').get_data(as_text=True)
        self.assertIn(survey['survey_code'], site_page)

    def test_results_page_filters_by_site_and_counts_failures(self):
        self.post()
        def broken(path):
            raise RuntimeError('model unavailable')
        process_next(self.app, self.store, broken, MODELS)
        html = self.client.get('/results?site_id=1').get_data(as_text=True)
        self.assertIn('1 failed', html)
        self.assertIn('Failed 1', html)
        other = self.client.get('/results?site_id=999').get_data(as_text=True)
        self.assertIn('No analysis results yet', other)

    def test_sites_and_results_survive_bad_settings_and_sparse_rows(self):
        with patch.dict(os.environ, {'AIFN_SITES_RECENT_DAYS': 'ninety', 'AIFN_RESULTS_PREVIEW_TYPES': ' .JPG, ,png'}):
            app = Flask(__name__, template_folder=str(ROOT / 'flask-application/templates'))
            app.config.update(TESTING=True, SECRET_KEY='test', UPLOAD_FOLDER=self.directory.name)
            install_dashboard(app, self.store)
        self.assertEqual(app.config['SITES_RECENT_DAYS'], 90)
        self.assertEqual(app.config['RESULTS_PREVIEW_TYPES'], ('jpg', 'png'))
        response = self.post(self.form_data(site_id='draft:local', new_site=json.dumps({'name': 'Sparse site'})))
        survey = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))
        models = self.store.register_models(MODELS)
        # Results with no class, no confidence and a failure, as older or partial rows may have.
        self.store.save_analysis(survey['images'][0]['image_id'], [
            dict(analysis_type='binary_detection', status='completed', model_id=models['binary_detection']),
            dict(analysis_type='species_classification', status='failed', error_message='x',
                 model_id=models['species_classification'])])
        self.app.config.update(SITES_MAP_URL='https://maps.example/{latitude}/{0}')
        for url in ('/sites', '/sites/1', f"/sites/{survey['site_id']}", '/results',
                    f"/results?site_id={survey['site_id']}", '/results?site_id=abc'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        html = self.client.get('/results').get_data(as_text=True)
        self.assertIn('Failed 1', html)

    def test_compare_page_shows_change_between_surveys(self):
        html = self.client.get('/compare').get_data(as_text=True)
        self.assertEqual(self.client.get('/compare').status_code, 200)
        self.assertRegex(html, 'No surveys to compare yet|Not enough analysed surveys to compare')
        first = self.post(self.form_data(survey_date=(date.today() - timedelta(days=30)).isoformat()))
        process_next(self.app, self.store, lambda path: dict(prediction(path), tiles={'Non-Mangrove': 2, 'orange': 6, 'red': 2}), MODELS)
        html = self.client.get('/compare?site_id=1').get_data(as_text=True)
        self.assertIn('Not enough analysed surveys to compare', html)
        second = self.post()
        process_next(self.app, self.store, lambda path: dict(prediction(path), tiles={'orange': 5, 'red': 5}), MODELS)
        first_id, second_id = (int(r.location.rsplit('/', 1)[1]) for r in (first, second))
        html = self.client.get('/compare').get_data(as_text=True)  # opens on the busiest site
        self.assertIn('<strong>100%</strong>', html)
        self.assertIn(f'from 80% on {(date.today() - timedelta(days=30)).isoformat()}', html)
        self.assertIn('data-tip-value="80.0%"', html)  # trend point for the earlier survey
        self.assertIn('▲ +20.0 pts', html)   # mangrove share
        self.assertIn('compare-delta up">▲ +20.0 pts', html)  # more mangrove is tinted as good
        self.assertIn('compare-delta neutral">▼ -25.0 pts', html)   # orange 75% -> 50%, a neutral shift
        self.assertIn('compare-delta neutral">▲ +25.0 pts', html)   # red 25% -> 50%
        self.assertIn('30 days', html)
        self.assertNotIn('different model versions', html)
        # Picking the surveys in reverse order still reads change forwards in time.
        swapped = self.client.get(f'/compare?site_id=1&from={second_id}&to={first_id}').get_data(as_text=True)
        self.assertIn('from 80% on', swapped)
        # A newer model on the second survey is flagged for traceability.
        newer = dict(MODELS, species_classification=dict(MODELS['species_classification'], version='test-v2'))
        survey = self.store.get_survey(second_id)
        results = [dict(r, model_id=self.store.register_models(newer)['species_classification'])
                   for r in survey['images'][0]['analyses'] if r['analysis_type'] == 'species_classification']
        self.store.save_analysis(survey['images'][0]['image_id'], results)
        if not self.store.demo_mode:  # the demo store keeps no model records
            html = self.client.get('/compare?site_id=1').get_data(as_text=True)
            self.assertIn('different model versions', html)
            self.assertIn('Model changed', html)
        self.assertEqual(self.client.get('/compare?site_id=999&from=abc').status_code, 200)

    def test_new_survey_preselects_site_from_site_page(self):
        html = self.client.get('/surveys/new?site_id=1').get_data(as_text=True)
        self.assertRegex(html, r'<option value="1"\s+selected>')

    def test_reanalysis_replaces_tile_counts(self):
        response = self.post()
        process_next(self.app, self.store, lambda path: dict(prediction(path), tiles={'orange': 3}), MODELS)
        survey = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))
        image = survey['images'][0]
        results = [dict(r, tile_counts={'orange': 1, 'red': 1}) for r in image['analyses']
                   if r['analysis_type'] == 'species_classification']
        self.store.save_analysis(image['image_id'], results)
        survey = self.store.get_survey(survey['survey_id'])
        self.assertEqual(survey_statistics(survey)['composition']['total_tiles'], 2)

    def test_non_mangrove_skips_classification(self):
        response = self.post()
        def non_mangrove(path):
            return {'binary': {'prediction': 'Non-Mangrove', 'confidence': .9, 'probs': [.9, .1]}, 'multi_class': None}
        process_next(self.app, self.store, non_mangrove, MODELS)
        survey = self.store.get_survey(int(response.location.rsplit('/', 1)[1]))
        self.assertEqual(survey['status'], 'completed')
        self.assertEqual(survey_statistics(survey)['classes'], {})
        self.assertEqual(survey_statistics(survey)['completed'], 1)

    def test_partial_failure_retry_keeps_completed_results(self):
        data = self.form_data()
        response = self.post(data, files=[image_file(), image_file()])
        calls = []
        def failing(path):
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError('inference failed')
            return prediction(path)
        with self.assertLogs(self.app.logger, level='ERROR'):
            process_next(self.app, self.store, failing, MODELS)
        survey_id = int(response.location.rsplit('/', 1)[1])
        self.assertEqual(self.store.get_survey(survey_id)['status'], 'failed')
        self.assertEqual(len(self.saved_files()), 2)
        self.assertEqual(self.client.post(response.location + '/retry', data={'csrf_token': 'bad'}).status_code, 400)
        self.assertEqual(self.client.post(response.location + '/retry', data={'csrf_token': data['csrf_token']}).status_code, 303)
        with patch(__name__ + '.prediction', wraps=prediction) as predictor:
            process_next(self.app, self.store, predictor, MODELS)
            self.assertEqual(predictor.call_count, 1)
        survey = self.store.get_survey(survey_id)
        self.assertEqual(survey['status'], 'completed')
        self.assertEqual(survey_statistics(survey)['completed'], 2)
        self.assertEqual(survey_statistics(survey)['failed'], 0)

    def test_restart_resumes_processing_and_lock_excludes_second_consumer(self):
        response = self.post()
        survey_id = int(response.location.rsplit('/', 1)[1])
        self.store.set_status(survey_id, 'processing')
        with self.store.next_survey(self.app.config['SURVEY_STORAGE_ID']) as claimed:
            self.assertEqual(claimed, survey_id)
            self.assertFalse(process_next(self.app, self.store, prediction, MODELS))
        self.assertTrue(process_next(self.app, self.store, prediction, MODELS))
        self.assertEqual(self.store.get_survey(survey_id)['status'], 'completed')

    def test_other_storage_is_not_processed(self):
        self.post()
        self.app.config['SURVEY_STORAGE_ID'] = 'another-computer'
        self.assertFalse(process_next(self.app, self.store, prediction, MODELS))

    def test_metadata_without_timezone_does_not_invent_timestamp(self):
        exif = Image.Exif()
        exif[34665] = {36867: '2026:09:14 10:30:00'}
        data = BytesIO()
        Image.new('RGB', (10, 10)).save(data, 'JPEG', exif=exif)
        data.seek(0)
        with Image.open(data) as image:
            metadata = image_metadata(image)
        self.assertNotIn('capture_date', metadata)
        self.assertEqual(metadata['source_metadata']['exif']['DateTimeOriginal'], '2026:09:14 10:30:00')

    def test_xml_entities_are_not_expanded(self):
        image = Image.new('RGB', (10, 10))
        image.info['xmp'] = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
        metadata = image_metadata(image)
        self.assertIn('xmp_warning', metadata['source_metadata'])


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for local PostgreSQL integration tests')
class PostgresSurveyFlowTests(SurveyFlowTests):
    def make_store(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        from psycopg import sql
        url = os.environ['TEST_DATABASE_URL']
        schema = 'test_surveys_' + uuid4().hex
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('create schema {}').format(sql.Identifier(schema)))
        def cleanup():
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute(sql.SQL('drop schema {} cascade').format(sql.Identifier(schema)))
        self.addCleanup(cleanup)
        url = make_conninfo(url, options=f'-c search_path={schema}')
        with psycopg.connect(url) as conn:
            conn.execute((ROOT / 'init-db/01-schema.sql').read_text())
            conn.execute("insert into site (site_code,site_name) values ('TEST','Test monitoring site')")
        return PostgresStore(url)

    def test_database_transaction_rolls_back_survey_and_first_image(self):
        details = dict(survey_code='TEST-ROLLBACK', site_id=1, survey_name='Rollback test',
                       survey_date=date.today(), survey_type='drone imagery', notes='')
        image = dict(filename='drone.jpg', file_type='jpg', file_size_bytes=20, storage_provider='local',
                     storage_container_id='test', storage_item_id='first', storage_url='/first',
                     width=20, height=20, band_count=3)
        with self.assertRaises(Exception):
            self.store.create_survey(details, [image, dict(image, storage_item_id='second', width='not-an-integer')])
        self.assertEqual(self.store.survey_history(), [])
        with self.store._connect() as conn:
            self.assertEqual(conn.execute('select count(*) as n from image').fetchone()['n'], 0)

    def test_database_transaction_rolls_back_new_site(self):
        from survey_flow import new_site_details
        site = new_site_details('{"name":"Rollback site"}')
        site['site_code'] = 'SITE-ROLLBACK'
        details = dict(survey_code='TEST-SITE-ROLLBACK', new_site=site, site_id=None,
                       survey_name='Rollback', survey_date=date.today(), survey_type='drone imagery', notes='')
        with self.assertRaises(Exception):
            self.store.create_survey(details, [{}])
        self.assertEqual(len(self.store.list_sites()), 1)
        self.assertEqual(self.store.survey_history(), [])


if __name__ == '__main__':
    unittest.main()
