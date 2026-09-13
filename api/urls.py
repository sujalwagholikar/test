from django.urls import path
from . import views

urlpatterns = [
    # student
    path("tests/", views.list_tests),
    path("tests/<int:test_id>/", views.get_test),
    path("tests/<int:test_id>/submit/", views.submit_test),

    # admin
    path("admin/tests/", views.admin_tests),
    path("admin/tests/<int:test_id>/", views.admin_test_detail),
    path("admin/tests/<int:test_id>/questions/", views.admin_add_question),
    path("admin/tests/<int:test_id>/questions/<int:question_id>/", views.admin_question_detail),
    path("admin/tests/<int:test_id>/results/", views.admin_test_results),
    path("admin/upload-image/", views.admin_upload_image),

    # AI support (Gemini)
    path("admin/ai/settings/", views.ai_settings),
    path("admin/ai/test-connection/", views.ai_test_connection),
    path("admin/tests/<int:test_id>/ai/generate/", views.ai_generate_questions),
    path("admin/tests/<int:test_id>/ai/import/", views.ai_import_questions),
]
