import secrets
from django.db import models


def gen_code():
    return secrets.token_hex(3).upper()


class Test(models.Model):
    title = models.CharField(max_length=200)
    subject = models.CharField(max_length=100, blank=True, default="")
    duration_minutes = models.PositiveIntegerField(default=30)
    instructions = models.TextField(blank=True, default="")
    is_published = models.BooleanField(default=True)
    admin_code = models.CharField(max_length=8, default=gen_code, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    def total_marks(self):
        return self.questions.count()


class Question(models.Model):
    test = models.ForeignKey(Test, related_name="questions", on_delete=models.CASCADE)
    text = models.TextField()
    # Data-URL (base64) or absolute URL to an image shown with the question
    # (diagrams, circuit figures, graphs, etc.) — optional.
    image = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    marks = models.PositiveIntegerField(default=4)
    negative_marks = models.FloatField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:50]


class Option(models.Model):
    question = models.ForeignKey(Question, related_name="options", on_delete=models.CASCADE)
    text = models.CharField(max_length=500, blank=True, default="")
    # Optional image-only or image+text option (e.g. "which figure shows...")
    image = models.TextField(blank=True, default="")
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]


class Attempt(models.Model):
    test = models.ForeignKey(Test, related_name="attempts", on_delete=models.CASCADE)
    student_name = models.CharField(max_length=150)
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.FloatField(default=0)
    total_questions = models.PositiveIntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)
    unattempted_count = models.PositiveIntegerField(default=0)


class Answer(models.Model):
    attempt = models.ForeignKey(Attempt, related_name="answers", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_option = models.ForeignKey(Option, null=True, blank=True, on_delete=models.SET_NULL)


class AISettings(models.Model):
    """
    Singleton row holding the admin-configured Gemini API key + default model.
    Accessed via AISettings.get_solo().
    """
    gemini_api_key = models.CharField(max_length=200, blank=True, default="")
    gemini_model = models.CharField(max_length=100, blank=True, default="gemini-3.8-flash")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        if not obj.gemini_model:
            obj.gemini_model = "gemini-3.8-flash"
            obj.save(update_fields=["gemini_model"])
        return obj

    def __str__(self):
        return "AI Settings"


class AIGenerationLog(models.Model):
    """Optional audit trail of AI generation requests, useful for debugging teacher prompts."""
    test = models.ForeignKey(Test, null=True, blank=True, related_name="ai_logs", on_delete=models.SET_NULL)
    subject = models.CharField(max_length=150, blank=True, default="")
    topics = models.TextField(blank=True, default="")
    num_questions = models.PositiveIntegerField(default=0)
    brief = models.TextField(blank=True, default="")
    model_used = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, default="pending")  # pending/success/error
    error_message = models.TextField(blank=True, default="")
    raw_response_excerpt = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
