"""Small durable survey queue: PostgreSQL records work; one thread runs inference.

The database advisory lock prevents concurrent consumers. Completed images are
committed individually so an interrupted survey can resume without starting over.
"""
from collections import Counter
import hashlib
from pathlib import Path
import threading


def tile_composition(tile_counts):
    """Tile counts -> rows with each mangrove type's share of the mangrove tiles."""
    tiles = {label: int(count) for label, count in (tile_counts or {}).items()}
    total = sum(tiles.values())
    mangrove_tiles = total - tiles.get('Non-Mangrove', 0)
    rows = [dict(label=label, tiles=count,
                 percent=100 * count / mangrove_tiles if mangrove_tiles else 0.0)
            for label, count in sorted(tiles.items()) if label != 'Non-Mangrove']
    return dict(rows=rows, total_tiles=total, mangrove_tiles=mangrove_tiles,
                mangrove_percent=100 * mangrove_tiles / total if total else 0.0)


def survey_statistics(survey):
    counts = Counter()
    tiles = Counter()
    confidences = []
    completed = failed = mangrove = 0
    for image in survey['images']:
        results = {r['analysis_type']: r for r in image.get('analyses', [])}
        binary = results.get('binary_detection', {})
        classification = results.get('species_classification', {})
        if any(r['status'] == 'failed' for r in results.values()):
            failed += 1
        elif binary.get('status') == 'completed' and classification.get('status') in ('completed', 'skipped'):
            completed += 1
        if binary.get('status') == 'completed' and binary.get('predicted_class') == 'Mangrove':
            mangrove += 1
        if classification.get('status') == 'completed':
            counts[classification['predicted_class']] += 1
            confidences.append(float(classification['confidence']))
        if classification.get('status') in ('completed', 'skipped'):
            tiles.update({label: int(n) for label, n in (classification.get('tile_counts') or {}).items()})
    return dict(completed=completed, failed=failed, mangrove=mangrove,
                classes=dict(counts), mean_confidence=sum(confidences) / len(confidences) if confidences else None,
                composition=tile_composition(tiles))


def model_descriptions():
    root = Path(__file__).parent
    descriptions = {}
    for task, filename in [('binary_detection', 'bestBinary.pth'),
                           ('species_classification', 'best_mangrove_model.pth')]:
        with (root / filename).open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        descriptions[task] = dict(name='AIFN ' + task, version='survey-v1-' + digest[:32], path=filename)
    return descriptions


def process_next(app, store, predict, models):
    """Process at most one survey; also used for deterministic local tests."""
    with store.next_survey(app.config['SURVEY_STORAGE_ID']) as survey_id:
        if survey_id is None:
            return False
        store.set_status(survey_id, 'processing')
        try:
            model_ids = store.register_models(models)
            survey = store.get_survey(survey_id)
            failed = False
            for image in survey['images']:
                previous = {r['analysis_type']: r for r in image.get('analyses', [])}
                if all(previous.get(task, {}).get('model_id') == model_id and
                       previous[task]['status'] in ('completed', 'skipped')
                       for task, model_id in model_ids.items()):
                    continue
                try:
                    root = Path(app.config['SURVEY_UPLOAD_FOLDER']).resolve()
                    path = (root / image['storage_item_id']).resolve()
                    if not path.is_relative_to(root) or not path.is_file():
                        raise ValueError('The original image is unavailable in this storage location.')
                    output = predict(str(path))
                    binary = output['binary']
                    classification = output['multi_class']
                    if binary['prediction'] == 'Mangrove' and classification is None:
                        raise ValueError('Mangrove classification did not return a result.')
                    results = [dict(analysis_type='binary_detection', status='completed',
                                    predicted_class=binary['prediction'], confidence=binary['confidence'],
                                    probabilities=dict(zip(['Non-Mangrove', 'Mangrove'], binary['probs']))),
                               dict(analysis_type='species_classification',
                                    status='completed' if classification else 'skipped',
                                    tile_counts=output.get('tiles') or {},
                                    **(classification or {}))]
                except Exception:
                    app.logger.exception('Analysis failed for survey %s image %s', survey_id, image['image_id'])
                    failed = True
                    results = [dict(analysis_type=task, status='failed',
                                    error_message='Analysis could not finish. Retry after checking the model and original image.')
                               for task in model_ids]
                for result in results:
                    result['model_id'] = model_ids[result['analysis_type']]
                store.save_analysis(image['image_id'], results)
            store.set_status(survey_id, 'failed' if failed else 'completed')
        except Exception:
            # Database errors leave a resumable 'processing' row if this write also fails.
            app.logger.exception('Survey %s could not be processed', survey_id)
            store.set_status(survey_id, 'failed')
        return True


def start_processor(app, predict):
    """Start once per web process. Deploy with one worker to bound ML memory."""
    if 'survey_processor' in app.extensions:
        return
    stop = threading.Event()

    def run():
        models = None
        while not stop.is_set():
            try:
                if models is None:
                    models = model_descriptions()
                if process_next(app, app.extensions['survey_store'], predict, models):
                    continue
            except Exception:
                app.logger.exception('Survey processor unavailable; will retry')
            stop.wait(5)

    thread = threading.Thread(target=run, name='survey-analysis', daemon=True)
    app.extensions['survey_processor'] = (thread, stop)
    thread.start()
