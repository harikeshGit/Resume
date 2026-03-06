from __future__ import annotations

# Keep this module as the Render/Gunicorn entrypoint.
# It simply re-exports the Flask app created in `app.py`.
from app import app
