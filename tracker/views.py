from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from billing import entitlements
from jobs.models import Job

from .forms import ApplicationForm, StatusForm
from .models import Application
from .services import change_status, track_job


def _own(request, pk):
    return get_object_or_404(Application, pk=pk, user=request.user)


@login_required
def board(request):
    apps = Application.objects.filter(user=request.user).select_related("job", "generated_cv")
    status = request.GET.get("status")
    if status in Application.Status.values:
        apps = apps.filter(status=status)
    columns = [(value, label, [a for a in apps if a.status == value]) for value, label in Application.Status.choices]
    plan = entitlements.plan_for(request.user)
    return render(
        request,
        "tracker/board.html",
        {
            "columns": columns,
            "apps": apps,
            "plan": plan,
            "status_filter": status,
            "count": Application.objects.filter(user=request.user).count(),
        },
    )


def _at_limit(user):
    return entitlements.limit_reached(user, "max_applications", Application.objects.filter(user=user).count())


@login_required
def create(request):
    if _at_limit(request.user):
        messages.error(request, "You've reached the application-tracking limit on your plan. Upgrade for more.")
        return redirect("tracker:board")
    form = ApplicationForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        app = form.save(commit=False)
        app.user = request.user
        app.save()
        app.events.create(to_status=app.status, note="Created")
        return redirect("tracker:detail", pk=app.pk)
    return render(request, "tracker/form.html", {"form": form, "title": "Track an application"})


@login_required
@require_POST
def track(request, job_id):
    job = get_object_or_404(Job.objects.public(), pk=job_id)
    existing = Application.objects.filter(user=request.user, job=job).first()
    if existing:
        return redirect("tracker:detail", pk=existing.pk)
    if _at_limit(request.user):
        messages.error(request, "You've reached the application-tracking limit on your plan.")
        return redirect(job.get_absolute_url())
    app, _ = track_job(request.user, job)
    messages.success(request, "Added to your application tracker.")
    return redirect("tracker:detail", pk=app.pk)


@login_required
def detail(request, pk):
    app = _own(request, pk)
    form = ApplicationForm(request.user, request.POST or None, instance=app)
    old_status = app.status
    if request.method == "POST" and form.is_valid():
        updated = form.save(commit=False)
        new_status = updated.status
        updated.status = old_status
        updated.save()
        change_status(updated, new_status)
        messages.success(request, "Saved.")
        return redirect("tracker:detail", pk=app.pk)
    return render(request, "tracker/detail.html", {"app": app, "form": form, "events": app.events.all()[:50]})


@login_required
@require_POST
def update_status(request, pk):
    app = _own(request, pk)
    form = StatusForm(request.POST)
    if form.is_valid():
        change_status(app, form.cleaned_data["status"], form.cleaned_data["note"])
    if request.headers.get("HX-Request"):
        return render(request, "tracker/_card.html", {"a": app})
    return redirect(request.POST.get("next") or "tracker:board")


@login_required
@require_POST
def delete(request, pk):
    _own(request, pk).delete()
    messages.success(request, "Application removed from your tracker.")
    return redirect("tracker:board")
