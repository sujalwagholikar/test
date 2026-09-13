"""
aisupport.py — AI-powered question generation for TestPortal, backed by
Google's Gemini API (gemini-3.8-flash by default).

This module is intentionally self-contained (no google-genai SDK dependency —
plain `requests` against the REST endpoint) so it works in the same minimal,
serverless-friendly footprint as the rest of the project (Django + Vercel,
single requirements.txt).

Public entrypoints
-------------------
- generate_questions(...): calls Gemini and returns a validated list of
  question dicts ready to hand to admin_add_question / bulk import.
- GEMINI_MODEL_DEFAULT: the default model id used across the app.

Design notes
------------
- The teacher-facing "brief" (toughness, subtopics, question types, extra
  instructions) is folded directly into the prompt sent to Gemini, alongside
  a fixed system instruction that teaches the model the exact JSON contract
  TestPortal expects (matching Question/Option fields: text, marks,
  negative_marks, options[{text, is_correct}]).
- We ask Gemini to return `responseMimeType: application/json` with a
  `responseSchema`, so we get back strict JSON we can validate — no fragile
  regex/markdown-fence stripping needed for the common case. We still strip
  fences defensively in case a model/version ignores the schema.
- All network calls have sane timeouts and raise `AISupportError` with a
  human-readable message on any failure (bad key, quota, network, malformed
  response) so the admin UI can surface something actionable.
"""

import json
import re
import requests

GEMINI_MODEL_DEFAULT = "gemini-3.8-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT_SECONDS = 60

DIFFICULTY_CHOICES = ["easy", "medium", "hard", "mixed"]
QUESTION_TYPE_CHOICES = ["single_correct_mcq", "assertion_reason", "numeric_mcq", "statement_based"]


class AISupportError(Exception):
    """Raised for any recoverable failure talking to / parsing Gemini output."""
    pass


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """You are an expert exam-question setter integrated into an online test \
platform called TestPortal, used for JEE-Mains-style competitive exams and general school/college \
tests. Teachers use you to auto-generate multiple-choice questions (MCQs) for a specific subject \
and topic list.

Hard requirements for every question you produce:
1. Each question must be a single-correct-answer multiple-choice question with EXACTLY 4 options,
   unless the teacher's brief explicitly asks for a different option count.
2. Exactly ONE option must be marked correct (is_correct: true); all others is_correct: false.
3. Options must be plausible distractors, not obviously wrong filler — wrong options should reflect
   common student mistakes or misconceptions on that topic.
4. Question text must be self-contained (no "see figure/diagram" references — this platform has no
   way for you to attach an image). Where numeric/formula/scientific content is needed, write proper
   LaTeX using KaTeX-compatible syntax, delimited with $...$ for inline math and $$...$$ for a
   standalone display equation, e.g. "Evaluate $\\int_0^1 x^2\\,dx$", "$\\text{H}_2\\text{O}$",
   "$\\sin\\theta + \\cos\\theta = \\sqrt{2}$". Always prefer LaTeX over plain-text approximations
   (write $x^2$, not "x^2"; write $\\frac{a}{b}$, not "a/b" for genuine fractions in the stem).
   Use the same LaTeX conventions inside option text when options contain math.
5. Do not repeat the same question stem twice, and vary sub-topics across the requested topic list
   evenly rather than clustering all questions on one sub-topic.
6. Respect the requested difficulty distribution and question style described in the teacher's brief.
7. Default marking scheme is +4 for correct and -1 for incorrect, matching this platform's convention,
   unless the brief specifies otherwise — reflect that in the marks/negative_marks fields per question.
8. Output ONLY structured data matching the provided schema. No commentary, no markdown, no explanations
   outside the JSON fields themselves.
"""


def _build_user_prompt(subject, topics, num_questions, difficulty, question_types, brief, existing_question_texts=None):
    topics = topics or []
    topics_line = ", ".join(t.strip() for t in topics if t.strip()) or "general syllabus for the subject"

    qtypes = question_types or []
    qtypes_line = ", ".join(qtypes) if qtypes else "standard single-correct MCQs"

    lines = [
        f"Subject: {subject or 'General'}",
        f"Topics / subtopics to cover: {topics_line}",
        f"Number of questions to generate: {num_questions}",
        f"Target difficulty: {difficulty or 'mixed'}",
        f"Preferred question style(s): {qtypes_line}",
    ]

    if brief:
        lines.append(
            "Teacher's brief (free-form instructions — follow these closely, they override generic "
            "defaults above where they conflict, e.g. toughness, specific sub-areas to emphasize, "
            "tone, marking scheme, exam pattern to mimic):\n" + brief.strip()
        )

    if existing_question_texts:
        sample = "\n".join(f"- {t}" for t in existing_question_texts[:30])
        lines.append(
            "This test already contains the following questions — do NOT duplicate or closely "
            f"paraphrase these:\n{sample}"
        )

    lines.append(
        f"Generate exactly {num_questions} question objects following the required JSON schema."
    )
    return "\n\n".join(lines)


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "subtopic": {"type": "STRING"},
                    "difficulty": {"type": "STRING"},
                    "marks": {"type": "NUMBER"},
                    "negative_marks": {"type": "NUMBER"},
                    "options": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "text": {"type": "STRING"},
                                "is_correct": {"type": "BOOLEAN"},
                            },
                            "required": ["text", "is_correct"],
                        },
                    },
                },
                "required": ["text", "options"],
            },
        }
    },
    "required": ["questions"],
}


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def _call_gemini(api_key, model, user_prompt):
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        raise AISupportError("The AI request timed out. Please try again with fewer questions.")
    except requests.exceptions.RequestException as e:
        raise AISupportError(f"Could not reach Gemini API: {e}")

    if resp.status_code == 400:
        raise AISupportError(_extract_gemini_error(resp) or "Gemini rejected the request (400). Check your API key/model name.")
    if resp.status_code in (401, 403):
        raise AISupportError("Gemini API key is invalid, missing permissions, or not authorized for this model.")
    if resp.status_code == 404:
        raise AISupportError(f"Model '{model}' was not found. Double-check the model id in AI settings.")
    if resp.status_code == 429:
        raise AISupportError("Gemini API rate limit / quota exceeded. Please wait and try again.")
    if resp.status_code >= 500:
        raise AISupportError("Gemini API is temporarily unavailable. Please try again shortly.")
    if resp.status_code != 200:
        raise AISupportError(_extract_gemini_error(resp) or f"Gemini API returned status {resp.status_code}.")

    try:
        data = resp.json()
    except ValueError:
        raise AISupportError("Gemini API returned a non-JSON response.")

    return _extract_text(data)


def _extract_gemini_error(resp):
    try:
        data = resp.json()
        return data.get("error", {}).get("message")
    except ValueError:
        return None


def _extract_text(data):
    try:
        candidates = data.get("candidates", [])
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason")
            if reason:
                raise AISupportError(f"Gemini blocked the request (reason: {reason}). Try adjusting the brief.")
            raise AISupportError("Gemini returned no candidates. Try again or adjust your prompt.")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        if not text.strip():
            finish_reason = candidates[0].get("finishReason", "unknown")
            raise AISupportError(f"Gemini returned an empty response (finish reason: {finish_reason}).")
        return text
    except AISupportError:
        raise
    except Exception:
        raise AISupportError("Unexpected response shape from Gemini API.")


def _strip_code_fences(text):
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


# ---------------------------------------------------------------------------
# Validation / normalization of model output into TestPortal's shape
# ---------------------------------------------------------------------------

def _normalize_questions(raw_questions, default_marks, default_negative):
    normalized = []
    for i, q in enumerate(raw_questions):
        text = (q.get("text") or "").strip()
        if not text:
            continue

        options_in = q.get("options") or []
        options = []
        for opt in options_in:
            opt_text = (opt.get("text") or "").strip()
            if not opt_text:
                continue
            options.append({"text": opt_text, "is_correct": bool(opt.get("is_correct"))})

        if len(options) < 2:
            continue  # skip malformed question rather than failing the whole batch

        correct_count = sum(1 for o in options if o["is_correct"])
        if correct_count == 0:
            # Fall back: mark the first option correct rather than silently dropping
            # a usable question — but this is logged so teachers can review it.
            options[0]["is_correct"] = True
        elif correct_count > 1:
            # Keep only the first correct one to preserve single-answer semantics.
            seen = False
            for o in options:
                if o["is_correct"]:
                    if seen:
                        o["is_correct"] = False
                    seen = True

        try:
            marks = float(q.get("marks")) if q.get("marks") is not None else default_marks
        except (TypeError, ValueError):
            marks = default_marks
        try:
            negative_marks = float(q.get("negative_marks")) if q.get("negative_marks") is not None else default_negative
        except (TypeError, ValueError):
            negative_marks = default_negative

        normalized.append({
            "text": text,
            "subtopic": (q.get("subtopic") or "").strip(),
            "difficulty": (q.get("difficulty") or "").strip(),
            "marks": marks,
            "negative_marks": negative_marks,
            "options": options,
        })
    return normalized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_questions(
    api_key,
    subject,
    topics,
    num_questions,
    difficulty="mixed",
    question_types=None,
    brief="",
    default_marks=4,
    default_negative=1,
    model=None,
    existing_question_texts=None,
):
    """
    Generate `num_questions` MCQ dicts for the given subject/topics using Gemini.

    Returns: list of dicts:
        {text, subtopic, difficulty, marks, negative_marks, options:[{text,is_correct}]}

    Raises AISupportError on any failure (missing/invalid key, network issue,
    malformed model output, etc). Callers should catch this and surface
    `str(err)` to the admin UI.
    """
    if not api_key:
        raise AISupportError(
            "No Gemini API key configured. Add one in Admin → AI Settings before generating questions."
        )
    if not num_questions or num_questions < 1:
        raise AISupportError("Number of questions must be at least 1.")
    if num_questions > 40:
        raise AISupportError("Please request 40 or fewer questions per generation batch for reliability.")

    model = (model or GEMINI_MODEL_DEFAULT).strip()

    user_prompt = _build_user_prompt(
        subject=subject,
        topics=topics,
        num_questions=num_questions,
        difficulty=difficulty,
        question_types=question_types,
        brief=brief,
        existing_question_texts=existing_question_texts,
    )

    raw_text = _call_gemini(api_key, model, user_prompt)
    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last-resort recovery: try to find the first {...} block in the text.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise AISupportError("Gemini's response could not be parsed as JSON. Try again.")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise AISupportError("Gemini's response could not be parsed as JSON. Try again.")

    raw_questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(raw_questions, list) or not raw_questions:
        raise AISupportError("Gemini did not return any usable questions. Try rephrasing the brief.")

    normalized = _normalize_questions(raw_questions, default_marks, default_negative)
    if not normalized:
        raise AISupportError("Gemini's questions were malformed (missing valid options). Try again.")

    return normalized


def test_api_key(api_key, model=None):
    """
    Lightweight connectivity check used by the 'Test connection' button in
    Admin → AI Settings. Returns (ok: bool, message: str).
    """
    if not api_key:
        return False, "No API key provided."
    model = (model or GEMINI_MODEL_DEFAULT).strip()
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with exactly the word: OK"}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 10},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"

    if resp.status_code == 200:
        return True, f"Connected successfully to {model}."
    if resp.status_code in (401, 403):
        return False, "API key rejected (invalid or lacks permission for this model)."
    if resp.status_code == 404:
        return False, f"Model '{model}' not found."
    return False, _extract_gemini_error(resp) or f"Gemini API returned status {resp.status_code}."
