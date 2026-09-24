"""Survey browser checks using a temporary localhost server and demo store.

Run: PLAYWRIGHT_BROWSERS_PATH=/tmp/aifn-playwright .venv/bin/python tests/browser_site_metadata.py
Requires the optional playwright package and its Chromium browser.
"""
from io import BytesIO
import json
import re
from pathlib import Path
import struct
import sys
import threading
from tempfile import TemporaryDirectory

from flask import Flask
from PIL import Image, TiffImagePlugin
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'flask-application'))
from dashboard import install_dashboard
from dashboard_store import DemoStore
from survey_processing import process_next
from test_survey_flow import MODELS, prediction


def photo(name='drone.jpg', metadata=True, format='JPEG'):
    image = Image.new('RGB', (64, 48), (30, 120, 60))
    output = BytesIO()
    exif = Image.Exif()
    if metadata:
        rational = TiffImagePlugin.IFDRational
        exif[271] = 'DJI'
        exif[272] = 'Test drone camera'
        exif[34665] = {36867: '2026:09:14 10:30:00', 33434: rational(1, 250), 34855: 100}
        exif[34853] = {1: 'S', 2: (rational(12), rational(30), rational(0)),
                       3: 'E', 4: (rational(130), rational(45), rational(0)),
                       5: 0, 6: rational(85)}
    image.save(output, format, exif=exif.tobytes())
    data = output.getvalue()
    if metadata and format == 'JPEG':
        xmp = b'''http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" drone-dji:RelativeAltitude="+42.5" drone-dji:AbsoluteAltitude="+85.0" drone-dji:FlightYawDegree="+120.0" drone-dji:RtkFlag="50"/></rdf:RDF></x:xmpmeta>'''
        data = data[:2] + b'\xff\xe1' + struct.pack('>H', len(xmp) + 2) + xmp + data[2:]
    return {'name': name, 'mimeType': {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'TIFF': 'image/tiff'}[format], 'buffer': data}


class QuietHandler(WSGIRequestHandler):
    def log(self, type, message, *args):
        pass


with TemporaryDirectory() as directory, sync_playwright() as p:
    app = Flask(__name__, template_folder=str(ROOT / 'flask-application/templates'),
                static_folder=str(ROOT / 'flask-application/static'))
    app.config.update(SECRET_KEY='frontend-test', UPLOAD_FOLDER=directory, TESTING=True)
    store = DemoStore()
    install_dashboard(app, store)
    writes = []
    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f'http://127.0.0.1:{server.server_port}'

    browser = p.chromium.launch()
    context = browser.new_context()
    context.on('request', lambda request: writes.append(request.url) if request.method != 'GET' else None)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(base_url + '/surveys/new')
    page.get_by_role('button', name='+ Create new site').click()
    page.locator('#new-site-name').fill('New coastal monitoring site')
    page.locator('#new-site-region').fill('Darwin')
    page.locator('#new-site-latitude').fill('-91')
    page.get_by_role('button', name='Use this site').click()
    assert page.locator('#new-site-fields').is_visible()
    page.locator('#new-site-latitude').fill('')
    page.get_by_role('button', name='Use this site').click()
    draft_id = page.locator('#site_id').input_value()
    assert draft_id.startswith('draft:')
    page.locator('#survey_name').fill('Metadata flight')
    page.locator('#survey_date').fill('2026-09-14')
    page.get_by_role('button', name='Continue to images').click()
    page.locator('#survey-images').set_input_files([
        photo(), photo('plain.png', False, 'PNG'), photo('drone.tif', True, 'TIFF'),
        {'name':'damaged.jpg','mimeType':'image/jpeg','buffer':b'not an image'},
    ])
    expect(page.locator('#metadata-progress')).to_contain_text('4 of 4', timeout=30000)
    expect(page.locator('#metadata-progress')).to_contain_text('2 with GPS')
    expect(page.locator('#metadata-progress')).to_contain_text('1 could not be read')
    expect(page.locator('#image-selection')).to_contain_text('GPS -12.500000, 130.750000')
    expect(page.locator('#image-selection')).to_contain_text('2026:09:14 10:30:00')
    with page.expect_download() as download:
        page.get_by_role('button', name='Download image metadata', exact=True).click()
    metadata = json.loads(Path(download.value.path()).read_text())
    first = metadata['images'][0]['summary']
    assert first['latitude'] == -12.5 and first['longitude'] == 130.75, first
    assert float(first['relativeAltitude']) == 42.5, first
    assert float(first['flightYaw']) == 120, first
    assert str(first['rtkFlag']) == '50', first
    assert first['width'] == 64 and first['height'] == 48, first
    assert metadata['images'][1]['summary']['width'] == 64, metadata['images'][1]
    assert metadata['images'][2]['summary']['cameraModel'] == 'Test drone camera'
    assert metadata['images'][3]['status'] == 'error'
    page.get_by_role('button', name='Review survey').click()
    expect(page.locator('#save-survey')).to_be_visible()
    expect(page.locator('#draft-review-notice')).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role('button', name='Download survey draft', exact=True).click()
    draft = json.loads(Path(download.value.path()).read_text())
    assert draft['site']['latitude'] is None
    assert draft['survey']['date'] == '2026-09-14'
    assert len(draft['images']) == 4
    page.get_by_role('button', name='← Back', exact=True).click()
    page.get_by_role('button', name='Remove damaged.jpg', exact=True).click()
    page.get_by_role('button', name='Review survey').click()
    page.get_by_role('button', name='Save and analyse survey').click()
    page.wait_for_url(re.compile(r'/surveys/\d+$'))
    expect(page.get_by_role('heading', name='Metadata flight', exact=True)).to_be_visible()
    assert len(writes) == 1
    saved = store.surveys[-1]
    assert saved['site_name'] == 'New coastal monitoring site'
    assert len(saved['images']) == 3
    assert saved['images'][0]['camera_model'] == 'Test drone camera'
    process_next(app, store, prediction, MODELS)
    page.reload()
    expect(page.get_by_text('Orange: 3 images', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Retry unfinished analysis')).to_have_count(0)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.goto(base_url + '/surveys/new')
    page.locator('#site_id').select_option('1')
    page.locator('#survey_name').fill('Existing site unchanged')
    page.get_by_role('button', name='Continue to images').click()
    page.locator('#survey-images').set_input_files([photo('plain.png', False, 'PNG')])
    expect(page.locator('#metadata-progress')).to_contain_text('1 of 1')
    page.get_by_role('button', name='Review survey').click()
    expect(page.locator('#save-survey')).to_be_visible()
    expect(page.locator('#draft-review-notice')).to_be_hidden()
    page.reload()
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('button', name='+ Create new site').click()
    page.screenshot(path='/tmp/aifn-new-site-mobile.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    # An empty shared site list must still expose new-site creation.
    store.sites = []
    page.reload()
    expect(page.get_by_role('button', name='+ Create new site')).to_be_visible()
    assert len(writes) == 1, writes
    assert not errors, errors
    browser.close()
    server.shutdown()
    print('PASS: new site, original uploads, saved metadata, analysis results, validation, empty site list, EXIF/GPS/XMP, JPEG/PNG/TIFF, malformed image, JSON exports, mobile layout; temporary demo data only.')
