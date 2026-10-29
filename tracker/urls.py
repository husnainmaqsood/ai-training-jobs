from django.urls import path

from . import views

app_name = "tracker"

urlpatterns = [
    path("", views.board, name="board"),
    path("new/", views.create, name="create"),
    path("track/<int:job_id>/", views.track, name="track"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/status/", views.update_status, name="status"),
    path("<int:pk>/delete/", views.delete, name="delete"),
]
