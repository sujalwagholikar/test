"""
server.py — single entrypoint that ties everything together for Vercel.

Vercel's Python runtime (@vercel/python) looks for a WSGI-compatible
`app` (or `application`/`handler`) object in this file and forwards every
request matched by vercel.json's routes into it. Django's own WSGI app
does all real work: serving index.html / admin.html via templates, and
answering every /api/... endpoint defined in api/urls.py.

Locally you can also just run this file directly:
    python server.py
which boots a plain dev server on http://127.0.0.1:8000
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django
from django.core.wsgi import get_wsgi_application
from django.core.management import call_command

django.setup()

# Make sure tables exist. On Vercel this runs against /tmp/db.sqlite3 (see
# core/settings.py) — fine for a demo/hackathon deploy. For production,
# point DATABASES at a hosted Postgres instance instead and run migrations
# as a separate build step.
try:
    call_command("migrate", "--run-syncdb", interactive=False, verbosity=0)
except Exception as exc:  # pragma: no cover
    print(f"[server.py] migration warning: {exc}")

app = get_wsgi_application()       # <-- what Vercel's Python runtime calls
application = app                  # alias some WSGI servers look for

if __name__ == "__main__":
    # Local dev convenience: `python server.py`
    from wsgiref.simple_server import make_server
    port = int(os.environ.get("PORT", 8000))
    print(f"Serving on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    with make_server("0.0.0.0", port, app) as httpd:
        httpd.serve_forever()
