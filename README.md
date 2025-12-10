# LocazioneTuristica

Semplice applicazione per gestire locazioni turistiche.

Run locally with:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
UVICORN_RELOAD=1 python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or with Docker Compose:

```bash
docker compose up --build
```

Data volume is mounted at `./data` and will contain the SQLite DB and attachments.

- Login:
- The seed script creates an admin user with `must_change_password` true if no users are present.
- Run `python scripts/seed.py` to initialize the DB and create a default admin if needed.

Notes about file uploads:
- Supported file types: PDF, images (jpg/png/webp), ODT, XLS/XLSX
- Max upload size default: 10MB (changeable via settings table or env variable)

Quick script to build & run
---------------------------
You can use the helper shell script to run the app locally or with Docker:

```bash
# dev mode (venv + uvicorn reload)
./scripts/run.sh dev

# docker mode (foreground)
./scripts/run.sh docker

# docker mode (detached)
./scripts/run.sh docker-detached

# seed DB only
./scripts/run.sh seed
```

