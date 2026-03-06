# AI Resume Screening System (Flask)

A small web app that:

- Accepts **multiple PDF resumes**
- Takes a **job description** text
- Extracts text + skills
- Ranks candidates using **TF‑IDF cosine similarity** + **skill overlap**
- Supports **real login/register** with **hashed passwords**
- Persists **screening runs/results** to **SQLite**

## Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# recommended (PowerShell)
$env:SECRET_KEY="change-this-to-a-random-secret"
$env:DEVELOPER_NAME="Your Name"

python app.py
```

Open: http://127.0.0.1:5000

## Data storage

- SQLite database is created at `instance/resume_screening.db` (ignored by git).
- You can override with `DATABASE_PATH`.

## Deploy (Render)

This repo includes `render.yaml` + a production entrypoint (`wsgi.py`).

1. Push this project to a GitHub repo
2. In Render: **New** → **Blueprint** → select your repo
3. Set env vars in Render:
   - `SECRET_KEY` (Render can auto-generate from `render.yaml`)
   - Optional branding: `APP_NAME`, `DEVELOPER_NAME`

Render will run the app with `gunicorn` using `wsgi.py`.

### Note for Windows

`gunicorn` does not run on Windows (it requires Linux). That’s OK because Render runs Linux.
For local development on Windows, continue using `python app.py`.

### Persisting the SQLite DB (optional)

By default, Render’s filesystem can be ephemeral depending on plan/settings.
If you don't persist the DB, accounts may disappear after redeploy/restart and users will see "Invalid username or password" even if they registered earlier.

To make accounts persist:

1. Add a **Render Disk** to the service (e.g. mount to `/var/data`)
2. Set `DATABASE_PATH=/var/data/resume_screening.db`

This repo's `render.yaml` is configured to use `/var/data/resume_screening.db` when a disk is mounted.

## Notes

- This is a baseline screening/ranking system. For real hiring use-cases, add better parsing (layout-aware), bias/fairness checks, and clear human review steps.
