from django.urls import path

from . import views

app_name = "referrals"

urlpatterns = [
    path("job/<int:pk>/", views.go_job, name="go_job"),
    path("platform/<slug:slug>/", views.go_platform, name="go_platform"),
]
