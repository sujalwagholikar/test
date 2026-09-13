import base64
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Test, Question, Option, Attempt, Answer, AISettings, AIGenerationLog
from . import aisupport

MAX_UPLOAD_BYTES = 3 * 1024 * 1024  # 3MB safety cap for question/option images


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


# ---------- STUDENT-FACING ----------

@require_http_methods(["GET"])
def list_tests(request):
    tests = Test.objects.filter(is_published=True).order_by("-created_at")
    data = [{
        "id": t.id,
        "title": t.title,
        "subject": t.subject,
        "duration_minutes": t.duration_minutes,
        "total_questions": t.questions.count(),
        "instructions": t.instructions,
    } for t in tests]
    return JsonResponse({"tests": data})


@require_http_methods(["GET"])
def get_test(request, test_id):
    """Return test with questions/options but WITHOUT revealing is_correct."""
    t = get_object_or_404(Test, id=test_id, is_published=True)
    questions = []
    for q in t.questions.all():
        questions.append({
            "id": q.id,
            "text": q.text,
            "image": q.image,
            "marks": q.marks,
            "negative_marks": q.negative_marks,
            "options": [{"id": o.id, "text": o.text, "image": o.image} for o in q.options.all()],
        })
    return JsonResponse({
        "id": t.id,
        "title": t.title,
        "subject": t.subject,
        "duration_minutes": t.duration_minutes,
        "instructions": t.instructions,
        "questions": questions,
    })


@csrf_exempt
@require_http_methods(["POST"])
def submit_test(request, test_id):
    t = get_object_or_404(Test, id=test_id)
    body = _body(request)
    student_name = (body.get("student_name") or "Anonymous").strip()[:150]
    answers = body.get("answers", {})  # {question_id: option_id or null}

    attempt = Attempt.objects.create(
        test=t, student_name=student_name, submitted_at=timezone.now()
    )

    score = 0.0
    correct_count = 0
    wrong_count = 0
    unattempted = 0
    questions = list(t.questions.all())

    for q in questions:
        selected_id = answers.get(str(q.id))
        selected_option = None
        if selected_id:
            selected_option = q.options.filter(id=selected_id).first()
        Answer.objects.create(attempt=attempt, question=q, selected_option=selected_option)

        if selected_option is None:
            unattempted += 1
        elif selected_option.is_correct:
            correct_count += 1
            score += q.marks
        else:
            wrong_count += 1
            score -= q.negative_marks

    attempt.score = score
    attempt.total_questions = len(questions)
    attempt.correct_count = correct_count
    attempt.wrong_count = wrong_count
    attempt.unattempted_count = unattempted
    attempt.save()

    # Build a review payload showing correct answers now that it's submitted
    review = []
    for q in questions:
        ans = attempt.answers.get(question=q)
        correct_opt = q.options.filter(is_correct=True).first()
        review.append({
            "question": q.text,
            "selected": ans.selected_option.text if ans.selected_option else None,
            "correct": correct_opt.text if correct_opt else None,
            "is_correct": bool(ans.selected_option and ans.selected_option.is_correct),
        })

    return JsonResponse({
        "attempt_id": attempt.id,
        "score": score,
        "total_marks": sum(q.marks for q in questions),
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "unattempted_count": unattempted,
        "total_questions": len(questions),
        "review": review,
    })


@csrf_exempt
@require_http_methods(["POST"])
def admin_upload_image(request):
    """
    Accepts a single multipart file (field name 'file') and returns a
    data: URL the admin editor can save straight onto a question/option
    image field. Keeps the stack storage-free (works fine on Vercel).
    """
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "No file uploaded."}, status=400)
    if f.size > MAX_UPLOAD_BYTES:
        return JsonResponse({"error": "Image too large (max 3MB)."}, status=400)
    content_type = f.content_type or "image/png"
    if not content_type.startswith("image/"):
        return JsonResponse({"error": "Only image files are supported."}, status=400)
    encoded = base64.b64encode(f.read()).decode("ascii")
    data_url = f"data:{content_type};base64,{encoded}"
    return JsonResponse({"url": data_url})


# ---------- ADMIN-FACING ----------

@csrf_exempt
@require_http_methods(["GET", "POST"])
def admin_tests(request):
    if request.method == "GET":
        tests = Test.objects.all().order_by("-created_at")
        data = [{
            "id": t.id,
            "title": t.title,
            "subject": t.subject,
            "duration_minutes": t.duration_minutes,
            "is_published": t.is_published,
            "total_questions": t.questions.count(),
            "attempts": t.attempts.count(),
        } for t in tests]
        return JsonResponse({"tests": data})

    body = _body(request)
    t = Test.objects.create(
        title=body.get("title", "Untitled Test"),
        subject=body.get("subject", ""),
        duration_minutes=int(body.get("duration_minutes", 30)),
        instructions=body.get("instructions", ""),
        is_published=bool(body.get("is_published", True)),
    )
    return JsonResponse({"id": t.id, "title": t.title})


@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
def admin_test_detail(request, test_id):
    t = get_object_or_404(Test, id=test_id)

    if request.method == "DELETE":
        t.delete()
        return JsonResponse({"deleted": True})

    if request.method == "PATCH":
        body = _body(request)
        for field in ["title", "subject", "instructions"]:
            if field in body:
                setattr(t, field, body[field])
        if "duration_minutes" in body:
            t.duration_minutes = int(body["duration_minutes"])
        if "is_published" in body:
            t.is_published = bool(body["is_published"])
        t.save()
        return JsonResponse({"updated": True})

    questions = []
    for q in t.questions.all():
        questions.append({
            "id": q.id,
            "text": q.text,
            "image": q.image,
            "marks": q.marks,
            "negative_marks": q.negative_marks,
            "options": [
                {"id": o.id, "text": o.text, "image": o.image, "is_correct": o.is_correct}
                for o in q.options.all()
            ],
        })
    return JsonResponse({
        "id": t.id, "title": t.title, "subject": t.subject,
        "duration_minutes": t.duration_minutes, "instructions": t.instructions,
        "is_published": t.is_published, "admin_code": t.admin_code,
        "questions": questions,
    })


@csrf_exempt
@require_http_methods(["POST"])
def admin_add_question(request, test_id):
    t = get_object_or_404(Test, id=test_id)
    body = _body(request)
    q = Question.objects.create(
        test=t,
        text=body.get("text", ""),
        image=body.get("image", "") or "",
        order=t.questions.count(),
        marks=int(body.get("marks", 4)),
        negative_marks=float(body.get("negative_marks", 1)),
    )
    options = body.get("options", [])
    for i, opt in enumerate(options):
        Option.objects.create(
            question=q,
            text=opt.get("text", ""),
            image=opt.get("image", "") or "",
            is_correct=bool(opt.get("is_correct", False)),
            order=i,
        )
    return JsonResponse({"id": q.id})


@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
def admin_question_detail(request, test_id, question_id):
    q = get_object_or_404(Question, id=question_id, test_id=test_id)

    if request.method == "DELETE":
        q.delete()
        return JsonResponse({"deleted": True})

    if request.method == "PATCH":
        body = _body(request)
        if "text" in body:
            q.text = body["text"]
        if "image" in body:
            q.image = body["image"] or ""
        if "marks" in body:
            q.marks = int(body["marks"])
        if "negative_marks" in body:
            q.negative_marks = float(body["negative_marks"])
        q.save()

        if "options" in body and isinstance(body["options"], list):
            q.options.all().delete()
            for i, opt in enumerate(body["options"]):
                Option.objects.create(
                    question=q,
                    text=opt.get("text", ""),
                    image=opt.get("image", "") or "",
                    is_correct=bool(opt.get("is_correct", False)),
                    order=i,
                )
        return JsonResponse({"updated": True})

    return JsonResponse({
        "id": q.id,
        "text": q.text,
        "image": q.image,
        "marks": q.marks,
        "negative_marks": q.negative_marks,
        "options": [
            {"id": o.id, "text": o.text, "image": o.image, "is_correct": o.is_correct}
            for o in q.options.all()
        ],
    })


# Backwards-compatible alias for the plain DELETE route used elsewhere.
@csrf_exempt
@require_http_methods(["DELETE"])
def admin_delete_question(request, test_id, question_id):
    q = get_object_or_404(Question, id=question_id, test_id=test_id)
    q.delete()
    return JsonResponse({"deleted": True})


@require_http_methods(["GET"])
def admin_test_results(request, test_id):
    t = get_object_or_404(Test, id=test_id)
    attempts = t.attempts.order_by("-submitted_at")
    data = [{
        "id": a.id,
        "student_name": a.student_name,
        "score": a.score,
        "total_questions": a.total_questions,
        "correct_count": a.correct_count,
        "wrong_count": a.wrong_count,
        "unattempted_count": a.unattempted_count,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
    } for a in attempts]
    return JsonResponse({"results": data})


# ---------- AI SUPPORT (Gemini-powered question generation) ----------

def _mask_key(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


@csrf_exempt
@require_http_methods(["GET", "POST"])
def ai_settings(request):
    """
    GET: return current AI settings (API key masked).
    POST: update the Gemini API key and/or default model.
    Body: { "gemini_api_key": "...", "gemini_model": "gemini-3.8-flash" }
    """
    settings_obj = AISettings.get_solo()

    if request.method == "GET":
        return JsonResponse({
            "gemini_api_key_masked": _mask_key(settings_obj.gemini_api_key),
            "has_api_key": bool(settings_obj.gemini_api_key),
            "gemini_model": settings_obj.gemini_model,
            "default_model": aisupport.GEMINI_MODEL_DEFAULT,
        })

    body = _body(request)
    if "gemini_api_key" in body and body["gemini_api_key"] is not None:
        new_key = body["gemini_api_key"].strip()
        # Allow clearing the key explicitly by sending an empty string.
        settings_obj.gemini_api_key = new_key
    if "gemini_model" in body and body["gemini_model"]:
        settings_obj.gemini_model = body["gemini_model"].strip()
    settings_obj.save()

    return JsonResponse({
        "saved": True,
        "gemini_api_key_masked": _mask_key(settings_obj.gemini_api_key),
        "has_api_key": bool(settings_obj.gemini_api_key),
        "gemini_model": settings_obj.gemini_model,
    })


@csrf_exempt
@require_http_methods(["POST"])
def ai_test_connection(request):
    """
    Verify the configured (or an ad-hoc, unsaved) API key/model actually works,
    without generating real questions. Used by the 'Test connection' button.
    Body (optional overrides): { "gemini_api_key": "...", "gemini_model": "..." }
    """
    body = _body(request)
    settings_obj = AISettings.get_solo()
    api_key = (body.get("gemini_api_key") or settings_obj.gemini_api_key or "").strip()
    model = (body.get("gemini_model") or settings_obj.gemini_model or aisupport.GEMINI_MODEL_DEFAULT).strip()

    ok, message = aisupport.test_api_key(api_key, model)
    return JsonResponse({"ok": ok, "message": message}, status=200 if ok else 400)


@csrf_exempt
@require_http_methods(["POST"])
def ai_generate_questions(request, test_id):
    """
    Generate questions with Gemini for a given test, based on teacher-provided
    subject/topics/count/brief. Does NOT save to the DB — returns a preview
    batch the teacher can review and then import via ai_import_questions.

    Body:
    {
      "subject": "Physics",
      "topics": ["Kinematics", "Laws of Motion"],
      "num_questions": 10,
      "difficulty": "mixed",              // easy|medium|hard|mixed
      "question_types": ["single_correct_mcq"],
      "brief": "Free-form teacher notes: toughness, subtopics, style, exam pattern...",
      "default_marks": 4,
      "default_negative": 1,
      "avoid_duplicates": true             // if true, sends existing question texts to avoid repeats
    }
    """
    t = get_object_or_404(Test, id=test_id)
    body = _body(request)

    settings_obj = AISettings.get_solo()
    api_key = settings_obj.gemini_api_key
    model = settings_obj.gemini_model or aisupport.GEMINI_MODEL_DEFAULT

    subject = body.get("subject") or t.subject
    topics = body.get("topics") or []
    if isinstance(topics, str):
        topics = [x.strip() for x in topics.split(",") if x.strip()]
    num_questions = int(body.get("num_questions", 5))
    difficulty = body.get("difficulty", "mixed")
    question_types = body.get("question_types") or []
    brief = body.get("brief", "")
    default_marks = float(body.get("default_marks", 4))
    default_negative = float(body.get("default_negative", 1))

    existing_texts = None
    if body.get("avoid_duplicates", True):
        existing_texts = list(t.questions.values_list("text", flat=True))

    log = AIGenerationLog.objects.create(
        test=t, subject=subject, topics=", ".join(topics), num_questions=num_questions,
        brief=brief, model_used=model, status="pending",
    )

    try:
        questions = aisupport.generate_questions(
            api_key=api_key,
            subject=subject,
            topics=topics,
            num_questions=num_questions,
            difficulty=difficulty,
            question_types=question_types,
            brief=brief,
            default_marks=default_marks,
            default_negative=default_negative,
            model=model,
            existing_question_texts=existing_texts,
        )
    except aisupport.AISupportError as e:
        log.status = "error"
        log.error_message = str(e)
        log.save()
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        log.status = "error"
        log.error_message = f"Unexpected error: {e}"
        log.save()
        return JsonResponse({"error": "An unexpected error occurred while generating questions."}, status=500)

    log.status = "success"
    log.raw_response_excerpt = json.dumps(questions[:3])[:2000]
    log.save()

    return JsonResponse({
        "generated_count": len(questions),
        "questions": questions,
        "model_used": model,
        "log_id": log.id,
    })


@csrf_exempt
@require_http_methods(["POST"])
def ai_import_questions(request, test_id):
    """
    Bulk-import a reviewed/edited batch of AI-generated (or any) questions
    directly into the test, in one call — the 'direct import' option.

    Body: { "questions": [ {text, marks, negative_marks, options:[{text,is_correct}]}, ... ] }
    """
    t = get_object_or_404(Test, id=test_id)
    body = _body(request)
    questions_in = body.get("questions") or []

    if not isinstance(questions_in, list) or not questions_in:
        return JsonResponse({"error": "No questions provided to import."}, status=400)

    created_ids = []
    start_order = t.questions.count()
    skipped = 0

    for i, q in enumerate(questions_in):
        text = (q.get("text") or "").strip()
        options_in = q.get("options") or []
        options_in = [
            o for o in options_in
            if (o.get("text") or "").strip() or (o.get("image") or "").strip()
        ]
        if not text or len(options_in) < 2 or not any(o.get("is_correct") for o in options_in):
            skipped += 1
            continue

        question = Question.objects.create(
            test=t,
            text=text,
            image=(q.get("image") or "").strip(),
            order=start_order + len(created_ids),
            marks=q.get("marks", 4) or 4,
            negative_marks=q.get("negative_marks", 1) or 1,
        )
        for j, opt in enumerate(options_in):
            Option.objects.create(
                question=question,
                text=opt.get("text", "").strip(),
                image=(opt.get("image") or "").strip(),
                is_correct=bool(opt.get("is_correct", False)),
                order=j,
            )
        created_ids.append(question.id)

    return JsonResponse({
        "imported_count": len(created_ids),
        "skipped_count": skipped,
        "question_ids": created_ids,
    })
