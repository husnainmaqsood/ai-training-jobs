from django.db import migrations


def backfill(apps, schema_editor):
    from jobs.models import canonical_url

    Job = apps.get_model("jobs", "Job")
    for job in Job.objects.only("pk", "application_url").iterator():
        Job.objects.filter(pk=job.pk).update(canonical_url=canonical_url(job.application_url))


class Migration(migrations.Migration):
    dependencies = [("jobs", "0004_upload_imports")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
