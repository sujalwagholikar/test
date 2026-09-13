from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="index"),
    path("admin-panel", TemplateView.as_view(template_name="admin.html"), name="admin-panel"),
    path("admin-panel/", TemplateView.as_view(template_name="admin.html")),
    path("api/", include("api.urls")),
]
