from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    from jobs.categories import infer_category_slug

    Job = apps.get_model("jobs", "Job")
    Category = apps.get_model("jobs", "Category")
    # Jobs reopened by an older staff action never got a published_at.
    Job.objects.filter(status="published", published_at__isnull=True).update(published_at=F("updated_at"))
    categories = {c.slug: c for c in Category.objects.all()}
    for job in Job.objects.filter(category__isnull=True).only("pk", "title", "description"):
        category = categories.get(infer_category_slug(job.title, job.description))
        if category:
            Job.objects.filter(pk=job.pk).update(category=category)


class Migration(migrations.Migration):
    dependencies = [("jobs", "0002_job_rate_text_alter_job_description_alter_job_status")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
