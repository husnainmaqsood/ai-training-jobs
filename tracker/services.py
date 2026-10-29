from django.db import IntegrityError, transaction

from .models import Application, ApplicationEvent

EARLY = {Application.Status.SAVED}


def change_status(app, new_status, note=""):
    if app.status == new_status:
        return app
    ApplicationEvent.objects.create(application=app, from_status=app.status, to_status=new_status, note=note[:500])
    app.status = new_status
    app.save(update_fields=["status", "updated_at"])
    return app


def track_job(user, job, status=Application.Status.SAVED):
    try:
        with transaction.atomic():
            app, created = Application.objects.get_or_create(
                user=user,
                job=job,
                defaults={"title": job.title, "company": job.display_company, "external_url": job.application_url, "status": status},
            )
    except IntegrityError:
        app, created = Application.objects.get(user=user, job=job), False
    if created:
        ApplicationEvent.objects.create(application=app, to_status=status, note="Started tracking")
    return app, created


def mark_cv_tailored(user, job, generated_cv):
    app = Application.objects.filter(user=user, job=job).first()
    if app is None:
        return None  # don't auto-create: the user decides what to track (and plan limits apply)
    if generated_cv and not app.generated_cv_id:
        app.generated_cv = generated_cv
        app.save(update_fields=["generated_cv", "updated_at"])
    if app.status in EARLY:
        change_status(app, Application.Status.CV_TAILORED, "Tailored CV generated")
    return app
