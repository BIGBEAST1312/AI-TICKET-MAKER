"""Entry point for a production web server such as gunicorn.

    gunicorn wsgi:app

Locally, keep using `python run.py`; gunicorn does not run on Windows.
"""

from app.server import create_app

app = create_app()
