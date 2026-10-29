from django.urls import path

from . import builder_views, views

app_name = "cvs"

urlpatterns = [
    path("builder/", builder_views.builder_home, name="builder"),
    path("builder/generate/", builder_views.builder_generate, name="builder_generate"),
    path("builder/result/", builder_views.builder_result, name="builder_result"),
    path("builder/result/download/<str:fmt>/", builder_views.builder_download, name="builder_download"),
    path("builder/result/discard/", builder_views.builder_discard, name="builder_discard"),
    path("builder/credits/done/", builder_views.purchase_done, name="builder_purchase_done"),
    path("builder/credits/<slug:code>/", builder_views.buy_credits, name="buy_credits"),
    path("", views.workspace, name="workspace"),
    path("upload/", views.upload, name="upload"),
    path("master/<uuid:public_id>/", views.master_detail, name="master_detail"),
    path("master/<uuid:public_id>/primary/", views.master_make_primary, name="master_primary"),
    path("master/<uuid:public_id>/delete/", views.master_delete, name="master_delete"),
    path("master/<uuid:public_id>/download/", views.master_download, name="master_download"),
    path("tailor/", views.tailor, name="tailor"),
    path("generation/<uuid:public_id>/", views.generation_detail, name="generation_detail"),
    path("generation/<uuid:public_id>/status/", views.generation_status, name="generation_status"),
    path("generation/<uuid:public_id>/retry/", views.generation_retry, name="generation_retry"),
    path("history/", views.history, name="history"),
    path("tailored/<uuid:public_id>/", views.generated_detail, name="generated_detail"),
    path("tailored/<uuid:public_id>/edit/", views.generated_edit, name="generated_edit"),
    path("tailored/<uuid:public_id>/regenerate/", views.generated_regenerate, name="generated_regenerate"),
    path("tailored/<uuid:public_id>/download/<str:fmt>/", views.generated_download, name="generated_download"),
    path("tailored/<uuid:public_id>/delete/", views.generated_delete, name="generated_delete"),
]
