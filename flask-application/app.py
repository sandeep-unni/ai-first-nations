from flask import Flask, Request, current_app, request, render_template, flash, redirect, url_for, send_from_directory
import os
import secrets
import threading
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from dashboard import install_dashboard

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

ALLOWED_EXTENSIONS = ['png', 'jpg', 'jpeg']

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.environ.get('AIFN_UPLOAD_FOLDER') or os.path.join(
    os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', app.root_path), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['SURVEY_STORAGE_ID'] = os.environ.get('AIFN_STORAGE_ID', 'aifn-local')

if os.environ.get('RAILWAY_ENVIRONMENT_ID'):
    required = ['SECRET_KEY', 'AIFN_STORAGE_ID', 'RAILWAY_VOLUME_MOUNT_PATH']
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError('Configure Railway survey storage before starting: ' + ', '.join(missing))
    if not Path(app.config['UPLOAD_FOLDER']).resolve().is_relative_to(Path(os.environ['RAILWAY_VOLUME_MOUNT_PATH']).resolve()):
        raise RuntimeError('AIFN_UPLOAD_FOLDER must be inside the attached Railway volume.')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


class UploadRequest(Request):
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        # Multipart originals can exceed ephemeral disk capacity. TemporaryFile is
        # removed on close/process exit, including aborted uploads.
        return tempfile.TemporaryFile(dir=current_app.config['UPLOAD_FOLDER'])


app.request_class = UploadRequest

model = None
mangrove_type = None
gpu = None
binary_model = None
model_lock = threading.Lock()
processor_start_lock = threading.Lock()


def initialize_models():
    global model, mangrove_type, gpu, binary_model
    print("Initializing ML models...")

    # Load multi-class model
    try:
        import torch
        torch.set_num_threads(1)
        from ml_model import load_model

        model, mangrove_type, gpu = load_model()
        print("Multi-class ML model ready.")
    except Exception as e:
        print(f"Warning: Error preparing multi-class ML model: {e}")
        model = None
        mangrove_type = None
        gpu = None

    # Load binary mangrove detector
    try:
        from binary_detector import load_binary_model

        binary_model = load_binary_model()
        print("Binary mangrove detector ready.")
    except Exception as e:
        print(f"Warning: Error loading binary mangrove detector: {e}")
        binary_model = None


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def survey_prediction(filepath):
    # Load only when needed, keeping dashboard startup independent of ML.
    with model_lock:
        if model is None or binary_model is None:
            initialize_models()
        if model is None or binary_model is None:
            raise RuntimeError('Both saved ML models are required for survey analysis.')
        from ml_model import predict_combined
        return predict_combined(filepath, binary_model, model, mangrove_type, gpu)


install_dashboard(app)


@app.before_request
def ensure_survey_processor():
    if not app.testing and os.getenv('AIFN_PROCESS_SURVEYS', 'true').lower() == 'true':
        from survey_processing import start_processor
        # Request threads can arrive together; startup must happen only once.
        with processor_start_lock:
            start_processor(app, survey_prediction)


@app.route('/')
def index():
    return redirect(url_for('dashboard'))


@app.route('/', methods=['POST'])
@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        if 'orthomosaic' not in request.files:
            flash('No file part')
            return redirect(request.url)

        file = request.files['orthomosaic']

        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            flash(f'File uploaded successfully: {filename}')
            return redirect(url_for('analyze', name=filename))
        else:
            flash('Invalid file type. Allowed: PNG, JPG, JPEG')
            return redirect(request.url)

    return render_template('home.html')


@app.route('/files/<name>')
def serve_file(name):
    return send_from_directory(app.config['UPLOAD_FOLDER'], name)


@app.route('/analyze/<name>')
def analyze(name):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], name)

    if not os.path.exists(filepath):
        flash(f'File not found: {name}')
        return redirect(url_for('upload'))

    analysis_result = None
    error_message = None

    try:
        analysis_result = survey_prediction(filepath)
    except Exception:
        app.logger.exception('Image analysis failed')
        error_message = 'Analysis could not finish. Check the model and original image, then retry.'

    image_url = url_for('serve_file', name=name)

    return render_template(
        'analysis_results.html',
        image_url=image_url,
        filename=name,
        result=analysis_result,
        error=error_message
    )


@app.route('/uploads/<name>')
def uploads(name):
    return render_template(
        'uploaded.html',
        image_url=url_for('serve_file', name=name)
    )


@app.route('/base')
def base():
    return render_template('base.html')


if __name__ == '__main__':
    app.run(debug=True)
