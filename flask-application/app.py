from flask import Flask, request, render_template, flash, redirect, url_for, send_from_directory
import os
from werkzeug.utils import secure_filename
from dashboard import install_dashboard

ALLOWED_EXTENSIONS = ['png', 'jpg', 'jpeg']

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
app.secret_key = 'mangrove-detection-secret-key-change-in-production'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

model = None
mangrove_type = None
gpu = None
binary_model = None


def initialize_models():
    global model, mangrove_type, gpu, binary_model
    print("Initializing ML models...")

    # Load multi-class model
    try:
        from ml_model import load_or_train_model

        model, mangrove_type, gpu = load_or_train_model()
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


# Prevent double initialization in Flask reloader
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    initialize_models()


install_dashboard(app)


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

    if model is not None and binary_model is not None:
        try:
            from ml_model import predict_combined

            analysis_result = predict_combined(filepath, binary_model, model, mangrove_type, gpu)
        except Exception as e:
            error_message = f"Error during analysis: {str(e)}"
            print(f"Exception during analysis: {e}")
    elif model is not None:
        try:
            from ml_model import predict_mangrove

            analysis_result, _, error = predict_mangrove(filepath, model, mangrove_type, gpu)
            if error:
                error_message = error
                print(f"Analysis error: {error}")
        except Exception as e:
            error_message = f"Error during analysis: {str(e)}"
            print(f"Exception during analysis: {e}")
    else:
        error_message = "ML models not available. Analysis cannot be performed."

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
