# TestPortal — Online Test Platform (Django + Vercel)

A minimal, JEE-Mains-style online examination portal.

- `index.html` (served by Django templates) — student portal: browse tests, take a timed test with a question palette, auto-submit on timeout, instant scored review.
- `admin.html` — admin console: create tests (title, subject, duration, instructions), add/delete questions with options and correct-answer marking, publish/unpublish, view student results per test.
- `server.py` — single entrypoint. Boots Django, runs migrations, and exposes the WSGI app that both Vercel and local `python server.py` use.
- `core/` — Django project (settings/urls/wsgi).
- `api/` — Django app: models (`Test`, `Question`, `Option`, `Attempt`, `Answer`) and all JSON API endpoints.

## Run locally

```bash
python -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Visit:
- `http://127.0.0.1:8000/` — student portal
- `http://127.0.0.1:8000/admin-panel` — admin console

(You can also run `python server.py` directly — it runs migrations itself and serves on port 8000.)

## Deploy to Vercel

1. Push this folder to a GitHub repo.
2. In Vercel: **New Project → Import** the repo. Vercel auto-detects `vercel.json` and `@vercel/python`.
3. Deploy. No environment variables are required for a quick demo.
4. Visit `your-project.vercel.app/` for students and `/admin-panel` for admin.

### Important production note

Vercel's Python functions are stateless/serverless: the SQLite file lives at `/tmp` and is **not guaranteed to persist** across cold starts or between different function instances. This is fine for demos/hackathons. For real usage, swap the `DATABASES` setting in `core/settings.py` for a hosted Postgres database (e.g. Neon, Supabase, Vercel Postgres) using a `DATABASE_URL` environment variable, then run `python manage.py migrate` once against it (locally or via a Vercel build step) before going live.

## AI-powered question generation (Gemini)

TestPortal can auto-generate MCQs using Google's **Gemini API** (default model: `gemini-3.8-flash`).

1. Get a free API key from [Google AI Studio](https://aistudio.google.com/apikey).
2. In the admin panel, click **⚙ AI Settings** (top bar), paste the key, and click **Save**. Use **Test connection** to verify it works.
3. Open any test → **AI Generate** tab. Fill in:
   - Subject and one or more topics/subtopics (tag input).
   - Number of questions, difficulty (easy/medium/hard/mixed), and question style(s).
   - A free-form **brief** — e.g. "JEE Mains level, moderately tough, focus on Laws of Motion, include 2 numericals, avoid pure theory" — the more specific, the better the output.
4. Click **✨ Generate questions**. Gemini returns a preview batch you can edit inline (question text, options, which option is correct, marks/negative marks) and select/deselect per question.
5. Click **Import selected questions** to add them directly to the test — nothing is written to the database until this step.

The API key is stored server-side (`AISettings` model) and never exposed to students; only a masked version is shown in the admin UI. Each generation attempt is logged (`AIGenerationLog`) for troubleshooting.

Relevant endpoints:
- `GET/POST /api/admin/ai/settings/` — read/update the Gemini API key + model
- `POST /api/admin/ai/test-connection/` — verify a key/model works
- `POST /api/admin/tests/<id>/ai/generate/` — generate a preview batch of questions (not saved)
- `POST /api/admin/tests/<id>/ai/import/` — bulk-import a reviewed batch into the test

## API reference

Student:
- `GET /api/tests/` — list published tests
- `GET /api/tests/<id>/` — get a test with questions/options (no answers revealed)
- `POST /api/tests/<id>/submit/` — submit `{student_name, answers: {question_id: option_id}}`, returns score + review

Admin:
- `GET/POST /api/admin/tests/` — list all tests / create a test
- `GET/PATCH/DELETE /api/admin/tests/<id>/` — full detail (with correct answers) / update settings / delete
- `POST /api/admin/tests/<id>/questions/` — add a question with options
- `DELETE /api/admin/tests/<id>/questions/<qid>/` — delete a question
- `GET /api/admin/tests/<id>/results/` — list all attempts/scores for a test

## Notes

- No login/auth is included — the admin console is reachable at `/admin-panel` by URL only. For real deployments, put this behind a password (e.g. Vercel's password-protection, or add Django auth) before going live.
- Scoring supports per-question marks and negative marking, matching common competitive-exam formats.
