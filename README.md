The Flask dashboard reads `.env` from the project root. For Railway, set
`AIFN_DEMO_MODE=false` and `DATABASE_URL` to the Railway public PostgreSQL URL
with `?sslmode=require&connect_timeout=10`. Keep credentials in `.env`, which
is ignored by Git. Shell environment variables take precedence over `.env`.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python flask-application/app.py
```

Open http://127.0.0.1:5000/dashboard. The dashboard reads existing database
records; the ML workflow also needs the model dependencies and weights.

To run the configured pgAdmin service:

```bash
docker compose up -d pgadmin
```

Open http://127.0.0.1:5050 and expand **Servers → AIFN Railway → Databases →
railway → Schemas → public → Tables**. This runs pgAdmin in desktop mode,
without a web login, bound only to this computer's loopback address.
The Railway server definition is in `pgadmin/servers.json`.

Local setup requires `PGADMIN_DEFAULT_PASSWORD` (a random bootstrap password)
in `.env`, and `.secrets/railway.pgpass` containing
`host:port:database:username:password` using the Railway credentials.
Both have already been configured on this computer. The `.secrets` directory
is private (mode 700) and Git-ignored; its pgpass file must be readable by the
container. On first launch, pgAdmin copies it into its persistent volume with
mode 600. Changes to the source password file after initialization also need
to be applied to `/var/lib/pgadmin/.pgpass` in the container.

Stop pgAdmin with `docker compose stop pgadmin`. Its configuration persists
in the `pgadmin_data` volume.

The separate `db` service is optional local PostgreSQL, started with
`docker compose up -d db`. It listens on port 5433 and initializes the schema
from `init-db` only when its data volume is empty. It does not copy Railway
data. Starting it does not switch the app away from the configured Railway URL.

## Create a survey

Open /surveys/new. Select a monitoring site or choose **Create new site**, enter
the survey details, add the original drone images, then **Save and analyse survey**.
A new site, its survey and image records are saved in one PostgreSQL transaction.
Submitting the same form again does not create duplicate sites or surveys.

JPG, PNG and TIFF are supported: up to 200 images, 100 MiB per image and 2 GiB per
request. Extract ZIP files before selecting their images; mission sidecar files
are not part of this image-upload flow. Originals are preserved byte for byte.

The browser previews metadata. The server independently extracts EXIF and DJI XMP
from the saved originals, including available camera, GPS, altitude and capture
time. Raw decoded tags are kept in image.source_metadata. A capture time without
a recorded timezone stays in the metadata; no timezone is guessed for capture_date.
Enter the survey date and optional site coordinates in the site/details step.

The existing binary mangrove detector runs first; mangrove images then pass to
the existing orange/red/yellow classifier. The bundled weights are loaded directly,
without downloading ImageNet weights or attempting to train during startup.
Inference streams small batches of tiles and processes one image at a time.
These labels are colour classes, not validated species, health or canopy coverage.

The survey page shows progress, per-image predictions/probabilities and aggregate
image counts and mean class confidence. Results and model versions are stored in
the existing model, analysis_result and class_probability tables; aggregate
statistics are calculated from those saved results. No schema changes are needed
when the database already matches init-db/01-schema.sql.

A single background thread polls pending surveys. A PostgreSQL advisory lock
allows only one consumer at a time. Each image's results commit separately;
interrupted processing resumes after the next service start and first request.
Failed surveys retain their originals and successful results, with a **Retry
unfinished analysis** button. Run one web worker: multiple workers unnecessarily
retain copies of the models. No Redis service or separate worker deployment is
required for this small, single-service setup. Keep Railway Serverless sleeping
disabled while using this background processor.

Demo mode uses temporary in-memory records but can still run the real models.
Set AIFN_PROCESS_SURVEYS=false to disable automatic inference during manual tests.

## Railway deployment

Configure the **website service**, not the Postgres service:

1. Attach an image volume mounted at /data. Allocate at least 5 GB for the supplied
   1.4 GB survey, temporary multipart files, and headroom; allow more for future
   surveys. A 0.5 GB trial volume cannot hold this survey. This may require a plan
   change; attaching storage and changing billing are manual deployment steps.
2. Set AIFN_DEMO_MODE=false, DATABASE_URL to the Postgres service reference,
   SECRET_KEY to a stable random secret, and AIFN_STORAGE_ID to a stable unique
   value such as aifn-railway-production. Keep credentials out of Git and screenshots.
3. Install requirements.txt. It selects pinned CPU-only PyTorch wheels for Linux
   to avoid the unused CUDA runtime. Training notebooks may need their own GPU
   environment. CPU wheel reference: https://pytorch.org/get-started/previous-versions/
4. Replace the Flask development start command with:

~~~sh
gunicorn --chdir flask-application app:app --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 2 --timeout 300
~~~

Railway supplies RAILWAY_VOLUME_MOUNT_PATH automatically. Images default to
/data/uploads/surveys. Multipart temporary files also use the volume and are
automatically removed when the request closes. AIFN_UPLOAD_FOLDER can override
the upload root, but on Railway it must remain inside that volume. Startup fails
with an explicit configuration error if the required storage/secret settings are
missing, rather than accepting uploads into disposable storage.

Back up the image volume and database together. A volume mounted on PostgreSQL
does not hold the website's image files. Existing files on a developer's computer
are not moved by this change; retain their existing storage IDs and locations.

Railway requires request bodies to finish uploading within five minutes. The
current form sends one multipart batch; select fewer images if your connection
cannot upload the batch in that time. A request interrupted before commit does
not create a survey; retry with the same form token if the outcome is uncertain.
See https://docs.railway.com/networking/public-networking/specs-and-limits and
https://docs.railway.com/volumes for the platform limits and volume setup.

The pending and retry mechanism survives process restarts; it does not provide
resumable network uploads. A large-scale upload service would use direct object
storage uploads and a dedicated job worker, which are outside this small flow.

## Local verification

~~~sh
# No shared database writes:
env -u TEST_DATABASE_URL AIFN_DEMO_MODE=true DATABASE_URL='' .venv/bin/python -m unittest discover -s tests -v

# Optional transactions/locking, using only a disposable local database:
TEST_DATABASE_URL='postgresql://aifn:aifn_dev_password@127.0.0.1:5433/aifn_mangrove' .venv/bin/python -m unittest discover -s tests -v

# Browser checks (optional playwright dependency):
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/browser_site_metadata.py
~~~

Tests cover site/survey transaction rollback, original files and metadata,
duplicate submissions, processing, probabilities, skipped classification,
failures/retries, resumed processing and exclusive queue consumption. Integration
tests create and remove isolated schemas; never set TEST_DATABASE_URL to Railway.
