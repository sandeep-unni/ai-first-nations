"""Create one survey containing a batch of drone images at an existing site."""
from datetime import date, datetime
import hmac
import json
import math
from pathlib import Path
import secrets
import shutil
import warnings
from uuid import uuid4

from flask import abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError, ExifTags
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

FORMATS = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.tif': 'TIFF', '.tiff': 'TIFF'}


def image_metadata(image):
    """Read original EXIF/XMP on the server; never trust browser-supplied tags."""
    def plain(value):
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [plain(v) for v in value]
        if isinstance(value, bytes):
            return {'byte_length': len(value)}
        if isinstance(value, str):
            # EXIF ASCII fields may include a NUL terminator, which PostgreSQL
            # text/JSONB cannot store. The original bytes remain in the image.
            return value.replace('\x00', '').encode('utf-8', 'replace').decode('utf-8')
        if isinstance(value, (int, bool)) or value is None:
            return value
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError, ZeroDivisionError):
            return str(value)

    record = {'source_metadata': {}}
    source = record['source_metadata']
    try:
        exif = image.getexif()
        tags = {ExifTags.TAGS.get(k, str(k)): plain(v) for k, v in exif.items()
                if k not in (34665, 34853)}
        tags.update({ExifTags.TAGS.get(k, str(k)): plain(v)
                     for k, v in exif.get_ifd(34665).items()})
        gps = {ExifTags.GPSTAGS.get(k, str(k)): plain(v)
               for k, v in exif.get_ifd(34853).items()}
        source.update(exif=tags, gps=gps)
        record['camera_model'] = str(tags.get('Model', ''))[:100] or None
        for key, ref, target in [('GPSLatitude', 'GPSLatitudeRef', 'latitude'),
                                 ('GPSLongitude', 'GPSLongitudeRef', 'longitude')]:
            parts = gps.get(key)
            if isinstance(parts, list) and len(parts) == 3:
                value = float(parts[0]) + float(parts[1]) / 60 + float(parts[2]) / 3600
                record[target] = -value if gps.get(ref) in ('S', 'W') else value
        # A timezone-less camera timestamp stays in source_metadata, not TIMESTAMPTZ.
        captured, offset = tags.get('DateTimeOriginal'), tags.get('OffsetTimeOriginal')
        if captured and offset:
            try:
                record['capture_date'] = datetime.strptime(captured + offset, '%Y:%m:%d %H:%M:%S%z')
            except (ValueError, TypeError):
                pass
    except Exception:
        source['exif_warning'] = 'Some EXIF tags could not be decoded.'
    try:
        from defusedxml import ElementTree
        raw = image.info.get('xmp') or image.info.get('XML:com.adobe.xmp')
        if raw:
            root = ElementTree.fromstring(raw)
            xmp = {}
            for node in root.iter():
                for key, value in node.attrib.items():
                    xmp[key.rsplit('}', 1)[-1]] = value
                if node.text and node.text.strip():
                    xmp[node.tag.rsplit('}', 1)[-1]] = node.text.strip()
            source['xmp'] = xmp
            if not record.get('capture_date'):
                try:
                    captured = datetime.fromisoformat(xmp.get('DateTimeOriginal') or xmp.get('CreateDate', ''))
                    if captured.tzinfo is not None:
                        record['capture_date'] = captured
                except ValueError:
                    pass
            for key, target in [('GpsLatitude', 'latitude'), ('GpsLongitude', 'longitude'),
                                ('AbsoluteAltitude', 'absolute_altitude'), ('RelativeAltitude', 'relative_altitude')]:
                if key in xmp:
                    try:
                        value = float(xmp[key])
                        if math.isfinite(value):
                            record[target] = value
                    except (TypeError, ValueError):
                        pass
            record['positioning_status'] = str(xmp.get('GpsStatus', ''))[:20] or None
    except Exception:
        source['xmp_warning'] = 'Some XMP tags could not be decoded.'
    for field, limit in [('latitude', 90), ('longitude', 180),
                         ('absolute_altitude', 9999999), ('relative_altitude', 9999999)]:
        if field in record and not -limit <= record[field] <= limit:
            record.pop(field)
    return record


def new_site_details(raw):
    try:
        site = json.loads(raw)
        if not isinstance(site, dict):
            raise ValueError()
        details = {}
        for field, limit in [('name', 255), ('region', 255), ('state', 100),
                             ('country', 100), ('description', 10000)]:
            value = site.get(field, '')
            if not isinstance(value, str) or len(value.strip()) > limit:
                raise ValueError()
            details['site_name' if field == 'name' else field] = value.strip()
        if not details['site_name']:
            raise ValueError()
        for field, limit in [('latitude', 90), ('longitude', 180)]:
            value = site.get(field)
            details[field] = None if value in (None, '') else float(value)
            if details[field] is not None and (isinstance(value, bool) or not -limit <= details[field] <= limit):
                raise ValueError()
        if (details['latitude'] is None) != (details['longitude'] is None):
            raise ValueError()
        return details
    except (ValueError, TypeError):
        raise ValueError('Enter a valid site name and location; provide both coordinates or leave both blank.') from None


def save_images(files, folder, app):
    """Validate actual image contents; keep original bytes and unique storage names."""
    records = []
    for upload in files:
        filename = secure_filename(upload.filename)
        suffix = Path(filename).suffix.lower()
        if not filename or len(filename) > 255 or suffix not in FORMATS:
            raise ValueError('Choose JPG, PNG or TIFF images with filenames under 256 characters.')
        item = uuid4().hex + suffix
        path = folder / item
        size = 0
        with path.open('xb') as output:
            while chunk := upload.stream.read(1024 * 1024):
                size += len(chunk)
                if size > app.config['SURVEY_MAX_IMAGE_BYTES']:
                    raise ValueError(f'{filename} exceeds the per-image size limit.')
                output.write(chunk)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    # DJI JPGs can contain an MPF index and a second JPEG frame.
                    # Pillow calls this MPO; it remains a valid JPEG container.
                    if image.format != FORMATS[suffix] and not (FORMATS[suffix] == 'JPEG' and image.format == 'MPO'):
                        raise ValueError(f'{filename}: the file contents do not match its image extension.')
                    width, height = image.size
                    bands = len(image.getbands())
                    image.verify()
                with Image.open(path) as image:
                    metadata = image_metadata(image)
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as exc:
            raise ValueError(f'{filename} is damaged, unsupported or too large to safely read.') from exc
        relative = f'{folder.name}/{item}'
        records.append(dict(filename=filename, file_type=suffix[1:], file_size_bytes=size,
                            storage_provider='local', storage_container_id=app.config['SURVEY_STORAGE_ID'],
                            storage_item_id=relative, storage_url=url_for('survey_image', filename=relative),
                            width=width, height=height, band_count=bands, **metadata))
    return records


def install_survey_flow(app, store):
    app.extensions['survey_store'] = store
    app.config.setdefault('SURVEY_UPLOAD_FOLDER', str(Path(app.config['UPLOAD_FOLDER']) / 'surveys'))
    app.config.setdefault('SURVEY_STORAGE_ID', 'aifn-local')
    app.config.setdefault('SURVEY_MAX_IMAGES', 200)
    app.config.setdefault('SURVEY_MAX_IMAGE_BYTES', 100 * 1024 * 1024)
    app.config.setdefault('SURVEY_MAX_REQUEST_BYTES', 2 * 1024 * 1024 * 1024)
    signer = URLSafeTimedSerializer(app.secret_key, salt='create-survey')

    def form_page(values=None, errors=None, status=200):
        session.setdefault('survey_csrf', secrets.token_urlsafe(32))
        values = values if values is not None else {'survey_date': date.today().isoformat()}
        return render_template('dashboard_ui/survey_new.html', active='surveys', demo_mode=store.demo_mode,
                               sites=store.list_sites(), values=values, errors=errors or [],
                               submission_token=values.get('submission_token') or signer.dumps(uuid4().hex),
                               csrf_token=session['survey_csrf'], max_images=app.config['SURVEY_MAX_IMAGES'],
                               max_image_mb=app.config['SURVEY_MAX_IMAGE_BYTES'] // (1024 * 1024),
                               max_total_mb=app.config['SURVEY_MAX_REQUEST_BYTES'] // (1024 * 1024)), status

    @app.route('/surveys/new', methods=['GET', 'POST'], endpoint='survey_step_1')
    def create_survey():
        if request.method == 'GET':
            return form_page()
        # Flask 3.1 supports a separate request size limit for this batch-upload route.
        request.max_content_length = app.config['SURVEY_MAX_REQUEST_BYTES']
        request.max_form_parts = app.config['SURVEY_MAX_IMAGES'] + 20
        values = request.form.to_dict()
        token = values.get('csrf_token', '')
        if not token or not hmac.compare_digest(token, session.get('survey_csrf', '')):
            return form_page(values, ['Your form expired. Please select your images and try again.'], 400)
        try:
            submission_id = signer.loads(values.get('submission_token', ''), max_age=86400)
        except BadSignature:
            values.pop('submission_token', None)
            return form_page(values, ['Your form expired. Please select your images and try again.'], 400)
        errors = []
        try:
            site_id = int(values.get('site_id', ''))
        except ValueError:
            site_id = None
        new_site = None
        if values.get('new_site'):
            try:
                new_site = new_site_details(values['new_site'])
                new_site['site_code'] = 'SITE-' + submission_id
            except ValueError as exc:
                errors.append(str(exc))
        elif site_id not in {s['site_id'] for s in store.list_sites()}:
            errors.append('Select an existing site.')
        name = values.get('survey_name', '').strip()
        if not name or len(name) > 255:
            errors.append('Enter a survey name of 1–255 characters.')
        try:
            survey_date = date.fromisoformat(values.get('survey_date', ''))
            if survey_date > date.today():
                errors.append('The survey date cannot be in the future.')
        except ValueError:
            survey_date = None
            errors.append('Enter a valid survey date.')
        notes = values.get('notes', '').strip()
        if len(notes) > 10000:
            errors.append('Keep notes under 10,000 characters.')
        files = [file for file in request.files.getlist('images') if file.filename]
        if not 1 <= len(files) <= app.config['SURVEY_MAX_IMAGES']:
            errors.append(f"Select between 1 and {app.config['SURVEY_MAX_IMAGES']} images.")
        if errors:
            return form_page(values, errors, 400)
        root = Path(app.config['SURVEY_UPLOAD_FOLDER'])
        folder = root / uuid4().hex
        keep_files = False
        try:
            folder.mkdir(parents=True)
            images = save_images(files, folder, app)
            details = dict(survey_code='SUR-' + submission_id, site_id=site_id, survey_name=name,
                           survey_date=survey_date, survey_type='drone imagery', notes=notes)
            if new_site:
                details['new_site'] = new_site
            survey_id, keep_files = store.create_survey(details, images)
        except ValueError as exc:
            return form_page(values, [str(exc)], 400)
        except Exception:
            app.logger.exception('Could not save survey upload')
            # A connection may fail after COMMIT. Confirm the outcome before
            # deleting originals that the committed survey may already reference.
            try:
                saved = store.get_survey_by_code('SUR-' + submission_id)
                if saved:
                    keep_files = any(i['storage_item_id'].startswith(folder.name + '/') for i in saved['images'])
                    return redirect(url_for('survey_detail', survey_id=saved['survey_id']), code=303)
            except Exception:
                keep_files = True
                app.logger.exception('Upload outcome unknown; retaining originals in %s', folder)
            return form_page(values, ['We could not save your survey. Please reselect your images and try again.'], 503)
        finally:
            if not keep_files:
                shutil.rmtree(folder, ignore_errors=True)
        flash(f'Survey saved with {len(images)} images.' if keep_files else 'This survey has already been saved.', 'success')
        return redirect(url_for('survey_detail', survey_id=survey_id), code=303)

    @app.route('/surveys', endpoint='surveys')
    def survey_history():
        site_id = request.args.get('site_id', type=int)
        return render_template('dashboard_ui/surveys.html', active='surveys', demo_mode=store.demo_mode,
                               surveys=store.survey_history(site_id), sites=store.list_sites(), site_id=site_id)

    @app.route('/surveys/<int:survey_id>', endpoint='survey_detail')
    def survey_detail(survey_id):
        survey = store.get_survey(survey_id)
        if survey is None:
            abort(404)
        # Only files belonging to this app's storage location get local previews.
        for image in survey['images']:
            image['local_url'] = None
            if (image['storage_provider'] == 'local' and
                    image.get('storage_container_id') == app.config['SURVEY_STORAGE_ID']):
                image['local_url'] = url_for('survey_image', filename=image['storage_item_id'])
        from survey_processing import survey_statistics
        session.setdefault('survey_csrf', secrets.token_urlsafe(32))
        return render_template('dashboard_ui/survey_detail.html', active='surveys',
                               demo_mode=store.demo_mode, survey=survey,
                               stats=survey_statistics(survey), csrf_token=session['survey_csrf'])

    @app.get('/surveys/<int:survey_id>/progress', endpoint='survey_progress')
    def survey_progress(survey_id):
        from survey_processing import survey_statistics
        survey = store.get_survey(survey_id)
        if survey is None:
            abort(404)
        stats = survey_statistics(survey)
        return {'status': survey['status'], 'total': len(survey['images']),
                'completed': stats['completed'], 'failed': stats['failed']}, 200, {'Cache-Control': 'no-store'}

    @app.post('/surveys/<int:survey_id>/retry', endpoint='retry_survey')
    def retry_survey(survey_id):
        if not hmac.compare_digest(request.form.get('csrf_token', ''), session.get('survey_csrf', '') or 'invalid'):
            abort(400)
        if store.get_survey(survey_id) is None:
            abort(404)
        store.retry_survey(survey_id)
        return redirect(url_for('survey_detail', survey_id=survey_id), code=303)

    @app.route('/survey-images/<path:filename>', endpoint='survey_image')
    def survey_image(filename):
        response = send_from_directory(app.config['SURVEY_UPLOAD_FOLDER'], filename,
                                       as_attachment=request.args.get('download') == '1')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(error):
        if request.endpoint != 'survey_step_1':
            return error
        return form_page(errors=['This upload exceeds the batch size or file count limit. Choose a smaller batch.'], status=413)
