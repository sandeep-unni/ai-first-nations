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
