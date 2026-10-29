from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("jobs/", views.job_list, name="list"),
    path("jobs/saved/", views.saved_jobs, name="saved"),
    path("jobs/category/<slug:slug>/", views.category_jobs, name="category"),
    path("jobs/skill/<slug:slug>/", views.skill_jobs, name="skill"),
    path("jobs/open-to/<str:code>/", views.country_jobs, name="country"),
    path("jobs/<int:pk>/save/", views.toggle_save, name="toggle_save"),
    path("jobs/<int:pk>/report/", views.report_job, name="report"),
    path("jobs/<int:pk>/<slug:slug>/", views.job_detail, name="detail"),
    path("jobs/<int:pk>/", views.job_detail, name="detail_short"),
    path("platforms/", views.platform_list, name="platform_list"),
    path("platforms/<slug:slug>/", views.platform_detail, name="platform_detail"),
    path("platforms/<slug:slug>/logo.png", views.platform_logo, name="platform_logo"),
]
