from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from core.admin_mixins import AuditedAdminMixin
from core.audit import record
from core.countries import normalize_list
from core.templatetags.ui import rate_label

from . import logos, spreadsheet
from .importers import run_source
from .models import Category, ImportRun, Job, JobReport, JobSource, Platform, SavedJob, Skill

STATUS_COLOURS = {
    Job.Status.PUBLISHED: ("#0e7a4f", "#e6f6ee"),
    Job.Status.DRAFT: ("#6b6b69", "#efefed"),
    Job.Status.CLOSED: ("#9a3412", "#fdeee6"),
    Job.Status.EXPIRED: ("#9a3412", "#fdeee6"),
    Job.Status.ARCHIVED: ("#6b6b69", "#efefed"),
    Job.Status.REJECTED: ("#b91c1c", "#fde8e8"),
}


class ImportUploadForm(forms.Form):
    file = forms.FileField(
        label="Excel or CSV file",
        help_text=f"Excel (.xlsx) or CSV, up to {spreadsheet.MAX_BYTES // (1024 * 1024)} MB / {spreadsheet.MAX_ROWS:,} rows.",
    )
    mode = forms.ChoiceField(
        choices=ImportRun.Mode.choices,
        initial=ImportRun.Mode.UPSERT,
        widget=forms.RadioSelect,
        help_text="“Add & update” never closes anything. “Full snapshot” also closes open listings that aren't in the file — you confirm that on the next page.",
    )


class JobAdminForm(forms.ModelForm):
    eligible_countries = forms.CharField(required=False, help_text="ISO codes, comma separated (e.g. US, GB, PK)")
    excluded_countries = forms.CharField(required=False, help_text="ISO codes, comma separated")
    tags = forms.CharField(required=False, help_text="Comma separated")

    class Meta:
        model = Job
        fields = "__all__"  # noqa: DJ007 — the admin fieldsets control what is shown

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["eligible_countries"] = ", ".join(self.instance.eligible_countries)
            self.initial["excluded_countries"] = ", ".join(self.instance.excluded_countries)
            self.initial["tags"] = ", ".join(self.instance.tags)

    def clean_eligible_countries(self):
        return normalize_list(self.cleaned_data.get("eligible_countries", ""))

    def clean_excluded_countries(self):
        return normalize_list(self.cleaned_data.get("excluded_countries", ""))

    def clean_tags(self):
        return [t.strip()[:40] for t in self.cleaned_data.get("tags", "").split(",") if t.strip()][:15]

    def clean(self):
        data = super().clean()
        if data.get("eligibility_scope") == Job.Eligibility.RESTRICTED and not data.get("eligible_countries"):
            self.add_error("eligible_countries", "Restricted listings must list the eligible countries.")
        if data.get("referral_url"):
            from referrals.models import ReferralProgram

            platform = data.get("platform")
            if not ReferralProgram.objects.filter(platform=platform, status=ReferralProgram.Status.ACTIVE).exists():
                self.add_error("referral_url", "A referral URL needs an ACTIVE, verified referral program for this platform.")
        if data.get("compensation_verified") and not data.get("compensation_currency"):
            self.add_error("compensation_currency", "Verified compensation needs a currency.")
        return data


@admin.register(Job)
class JobAdmin(AuditedAdminMixin, admin.ModelAdmin):
    form = JobAdminForm
    change_list_template = "admin/jobs/job/change_list.html"
    list_display = ("title", "platform", "category", "status_badge", "rate", "published_at", "expires_at", "last_verified_at", "on_site")
    list_display_links = ("title",)
    list_filter = (
        "status",
        "platform",
        "category",
        "published_at",
        "expires_at",
        "last_verified_at",
        "employment_type",
        "eligibility_scope",
        "needs_review",
        "link_broken",
        "is_demo",
        "source",
    )
    list_select_related = ("platform", "category")
    list_per_page = 50
    search_fields = ("title", "company_name", "platform__name", "category__name", "external_id", "application_url", "description")
    autocomplete_fields = ("skills",)
    readonly_fields = ("fingerprint", "canonical_url", "created_at", "updated_at", "link_checked_at", "missed_refreshes", "compensation_hourly_usd")
    date_hierarchy = "published_at"
    actions = ["publish", "close", "make_draft", "mark_verified", "expire", "archive", "reject", "mark_link_broken", "clear_review_flag"]
    fieldsets = (
        (None, {"fields": ("title", "slug", "platform", "company_name", "category", "skills", "tags", "status", "is_demo")}),
        ("Description", {"fields": ("description", "preferred_qualifications")}),
        (
            "Work & eligibility",
            {"fields": ("employment_type", "work_arrangement", "experience_level", "eligibility_scope", "eligible_countries", "excluded_countries")},
        ),
        (
            "Compensation (shown only when verified)",
            {
                "fields": (
                    "compensation_min",
                    "compensation_max",
                    "compensation_currency",
                    "compensation_period",
                    "compensation_verified",
                    "compensation_hourly_usd",
                )
            },
        ),
        ("Links", {"fields": ("application_url", "referral_url", "source_url")}),
        ("Dates", {"fields": ("published_at", "last_verified_at", "application_deadline", "expires_at")}),
        (
            "Source & moderation",
            {
                "fields": (
                    "source",
                    "external_id",
                    "canonical_url",
                    "fingerprint",
                    "needs_review",
                    "review_note",
                    "link_broken",
                    "link_checked_at",
                    "missed_refreshes",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        fg, bg = STATUS_COLOURS.get(obj.status, ("#6b6b69", "#efefed"))
        label = "Open" if obj.status == Job.Status.PUBLISHED else obj.get_status_display()
        return format_html(
            '<span style="padding:2px 8px;border-radius:999px;font-weight:600;font-size:11px;color:{};background:{}">{}</span>', fg, bg, label
        )

    @admin.display(description="Rate", ordering="compensation_hourly_usd")
    def rate(self, obj):
        return rate_label(obj) or "—"

    @admin.display(description="On site", boolean=True)
    def on_site(self, obj):
        return obj.is_public and (not obj.is_demo or getattr(settings, "SHOW_DEMO_JOBS", False))

    # --- Excel / CSV import -------------------------------------------------------------------

    def get_urls(self):
        view = self.admin_site.admin_view
        return [
            path("import/", view(self.import_view), name="jobs_job_import"),
            path("import/<int:run_id>/", view(self.import_preview_view), name="jobs_job_import_preview"),
            path("import/template.<str:fmt>", view(self.import_template_view), name="jobs_job_import_template"),
        ] + super().get_urls()

    def _check_import_permission(self, request):
        if not (self.has_add_permission(request) and self.has_change_permission(request)):
            raise PermissionDenied

    def _context(self, request, **extra):
        return {**self.admin_site.each_context(request), "opts": self.model._meta, **extra}

    def import_view(self, request):
        self._check_import_permission(request)
        form = ImportUploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            upload = form.cleaned_data["file"]
            try:
                rows = spreadsheet.read_rows(upload)
            except spreadsheet.SpreadsheetError as exc:
                form.add_error("file", str(exc))
            else:
                run = spreadsheet.start(rows, filename=upload.name, mode=form.cleaned_data["mode"], user=request.user)
                return redirect("admin:jobs_job_import_preview", run.pk)
        return render(
            request,
            "admin/jobs/job/import.html",
            self._context(
                request,
                title="Import jobs from Excel",
                form=form,
                columns=spreadsheet.COLUMN_HELP,
                recent=ImportRun.objects.filter(source__isnull=True).exclude(status=ImportRun.Status.PENDING)[:5],
            ),
        )

    def import_preview_view(self, request, run_id):
        self._check_import_permission(request)
        run = get_object_or_404(ImportRun, pk=run_id, source__isnull=True)
        if run.status != ImportRun.Status.PENDING:
            return redirect("admin:jobs_importrun_change", run.pk)
        if request.method == "POST":
            if request.POST.get("action") == "cancel":
                run.status, run.finished_at = ImportRun.Status.CANCELLED, timezone.now()
                run.save(update_fields=["status", "finished_at"])
                self.message_user(request, "Import cancelled. Nothing was changed.")
                return redirect("admin:jobs_job_import")
            confirm_close = int(request.POST.get("confirm_close") or -1) if request.POST.get("close_confirmed") else None
            try:
                summary = spreadsheet.apply(run, confirm_close=confirm_close)
            except spreadsheet.ImportBlocked as exc:
                self.message_user(request, str(exc), messages.ERROR)
                run.refresh_from_db()
                if run.status != ImportRun.Status.PENDING:
                    return redirect("admin:jobs_importrun_change", run.pk)
            else:
                record("jobs_imported", actor=request.user, target=run, filename=run.filename, mode=run.mode, **summary)
                closed = f", {summary['closing']} closed" if run.mode == ImportRun.Mode.SNAPSHOT else ""
                self.message_user(
                    request,
                    f"Import complete: {summary['added']} added, {summary['updated']} updated, {summary['skipped']} skipped, "
                    f"{summary['rejected']} rejected{closed}. The public site is already showing the changes.",
                    messages.WARNING if summary["rejected"] else messages.SUCCESS,
                )
                return redirect("admin:jobs_importrun_change", run.pk)
        results, to_close = spreadsheet.analyse(run.rows, run.mode)
        order = {"error": 0, "new": 1, "update": 2, "duplicate": 3, "unchanged": 4}
        shown = sorted(results, key=lambda r: (order[r.status], r.number))
        return render(
            request,
            "admin/jobs/job/import_preview.html",
            self._context(
                request,
                title=f"Preview import: {run.filename}",
                run=run,
                summary=spreadsheet.summarize(results, to_close),
                results=shown[:500],
                hidden_rows=max(0, len(shown) - 500),
                to_close=to_close,
                is_snapshot=run.mode == ImportRun.Mode.SNAPSHOT,
            ),
        )

    def import_template_view(self, request, fmt):
        self._check_import_permission(request)
        if fmt == "csv":
            resp = HttpResponse(spreadsheet.template_csv(), content_type="text/csv; charset=utf-8")
        else:
            resp = HttpResponse(spreadsheet.template_xlsx(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            fmt = "xlsx"
        resp["Content-Disposition"] = f'attachment; filename="job-import-template.{fmt}"'
        return resp

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.refresh_search_vector()  # include skills in the search index

    def _bulk(self, request, queryset, action, **fields):
        self.audit_action(request, queryset, f"job_{action}")
        n = queryset.update(updated_at=timezone.now(), **fields)
        self.message_user(request, f"{n} listing(s) updated: {action}.")

    @admin.action(description="Open (publish) selected listings")
    def publish(self, request, queryset):
        now = timezone.now()
        self.audit_action(request, queryset, "job_publish")
        for job in queryset:
            job.status = Job.Status.PUBLISHED
            job.needs_review = False
            job.published_at = job.published_at or now
            job.save()
        self.message_user(request, f"{queryset.count()} listing(s) published.")

    @admin.action(description="Close selected listings (hide from the site)")
    def close(self, request, queryset):
        self._bulk(request, queryset, "close", status=Job.Status.CLOSED)

    @admin.action(description="Move selected listings to draft (hide from the site)")
    def make_draft(self, request, queryset):
        self._bulk(request, queryset, "draft", status=Job.Status.DRAFT)

    @admin.action(description="Mark as verified now")
    def mark_verified(self, request, queryset):
        self._bulk(request, queryset, "verified", last_verified_at=timezone.now(), needs_review=False)

    @admin.action(description="Expire selected")
    def expire(self, request, queryset):
        self._bulk(request, queryset, "expire", status=Job.Status.EXPIRED)

    @admin.action(description="Reject (suspicious / not legitimate)")
    def reject(self, request, queryset):
        self._bulk(request, queryset, "reject", status=Job.Status.REJECTED, needs_review=False)

    @admin.action(description="Archive selected")
    def archive(self, request, queryset):
        self._bulk(request, queryset, "archive", status=Job.Status.ARCHIVED)

    @admin.action(description="Disable: mark application link broken")
    def mark_link_broken(self, request, queryset):
        self._bulk(request, queryset, "link_broken", link_broken=True)

    @admin.action(description="Clear review flag")
    def clear_review_flag(self, request, queryset):
        self._bulk(request, queryset, "review_cleared", needs_review=False, review_note="")


class OpenJobCountMixin:
    """Adds an "Open jobs" column counted with the same rules as the public site."""

    def get_queryset(self, request):
        public = Job.objects.public().values("pk")
        return super().get_queryset(request).annotate(open_jobs=Count("jobs", filter=Q(jobs__in=public), distinct=True))

    @admin.display(description="Open jobs", ordering="open_jobs")
    def open_jobs(self, obj):
        url = reverse("admin:jobs_job_changelist") + f"?{self.model._meta.model_name}__id__exact={obj.pk}&status__exact=published"
        return format_html('<a href="{}">{}</a>', url, obj.open_jobs)


class HasLogoFilter(admin.SimpleListFilter):
    title = "logo"
    parameter_name = "has_logo"

    def lookups(self, request, model_admin):
        return [("yes", "Has a logo"), ("no", "No logo")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(logo_version="")
        if self.value() == "no":
            return queryset.filter(logo_version="")
        return queryset


class PlatformAdminForm(forms.ModelForm):
    logo_upload = forms.FileField(
        label="Upload logo",
        required=False,
        help_text="PNG, JPG or WebP, at least 32×32 px; a square icon works best. It's trimmed and resized to 256 px automatically.",
    )
    fetch_logo = forms.BooleanField(
        label="Fetch logo from website", required=False, help_text="Downloads the official icon the website above publishes."
    )
    remove_logo = forms.BooleanField(label="Remove logo", required=False, help_text="Show the coloured initials instead.")

    class Meta:
        model = Platform
        fields = "__all__"  # noqa: DJ007 — the admin fieldsets control what is shown

    def clean_logo_upload(self):
        upload = self.cleaned_data.get("logo_upload")
        if upload:
            if upload.size > logos.MAX_DOWNLOAD:
                raise forms.ValidationError("The file is larger than 3 MB.")
            try:
                self.logo_png, _ = logos.normalise(upload.read())
            except logos.LogoError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return upload


@admin.register(Platform)
class PlatformAdmin(OpenJobCountMixin, AuditedAdminMixin, admin.ModelAdmin):
    form = PlatformAdminForm
    list_display = ("logo_thumb", "name", "open_jobs", "website", "is_active", "last_reviewed_at")
    list_display_links = ("logo_thumb", "name")
    list_filter = ("is_active", HasLogoFilter)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("logo_preview",)
    actions = ["fetch_logos"]
    fieldsets = (
        (None, {"fields": ("name", "slug", "website", "careers_url", "is_active")}),
        ("Logo", {"fields": ("logo_preview", "logo_upload", "fetch_logo", "remove_logo")}),
        ("About", {"fields": ("description", "work_types", "last_reviewed_at")}),
    )

    def _img(self, obj, size):
        if obj and obj.logo_version:
            return format_html(
                '<img src="{}" alt="" style="width:{}px;height:{}px;object-fit:contain;background:#fff;border:1px solid #e6eaf0;border-radius:8px;padding:3px">',
                obj.logo_url,
                size,
                size,
            )
        return format_html('<span style="color:#98a3b3">{}</span>', "No logo" if size > 40 else "—")

    @admin.display(description="Logo")
    def logo_thumb(self, obj):
        return self._img(obj, 28)

    @admin.display(description="Current logo")
    def logo_preview(self, obj):
        return self._img(obj, 96)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        data = form.cleaned_data
        if data.get("remove_logo"):
            logos.remove_logo(obj)
            self.message_user(request, f"Logo removed from {obj.name}.")
        elif getattr(form, "logo_png", None):
            logos.save_logo(obj, form.logo_png, source=f"upload: {data['logo_upload'].name}")
            self.message_user(request, f"New logo saved for {obj.name}.")
        elif data.get("fetch_logo"):
            try:
                logos.fetch_and_save(obj)
            except logos.LogoError as exc:
                self.message_user(request, str(exc), messages.WARNING)
            else:
                self.message_user(request, f"Fetched {obj.name}'s logo from {obj.website}.")

    @admin.action(description="Fetch logos from their websites")
    def fetch_logos(self, request, queryset):
        done, failed = [], []
        for platform in queryset:
            try:
                logos.fetch_and_save(platform)
                done.append(platform.name)
            except logos.LogoError as exc:
                failed.append(str(exc))
        if done:
            self.message_user(request, f"Logo fetched for {', '.join(done)}.")
        for msg in failed:
            self.message_user(request, msg, messages.WARNING)


@admin.register(Category)
class CategoryAdmin(OpenJobCountMixin, AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "open_jobs", "slug", "sort_order")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name", "slug")


@admin.register(JobSource)
class JobSourceAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "platform", "kind", "is_active", "permission_confirmed", "last_run_at", "last_success_at", "actions_column")
    list_filter = ("kind", "is_active", "permission_confirmed")
    actions = ["refresh_now"]

    def get_urls(self):
        return [
            path("<int:pk>/refresh/", self.admin_site.admin_view(self.refresh_view), name="jobs_jobsource_refresh"),
        ] + super().get_urls()

    @admin.display(description="Run")
    def actions_column(self, obj):
        if obj.is_automated:
            return format_html('<a href="{}">Refresh now</a>', reverse("admin:jobs_jobsource_refresh", args=[obj.pk]))
        return format_html('<a href="{}">Upload a spreadsheet</a>', reverse("admin:jobs_job_import"))

    def refresh_view(self, request, pk):
        source = get_object_or_404(JobSource, pk=pk)
        run = run_source(source, user=request.user)
        self.audit_action(request, [source], "source_refresh")
        level = messages.SUCCESS if run.status == ImportRun.Status.SUCCESS else messages.WARNING
        self.message_user(
            request,
            f"{source}: {run.status} — {run.created_count} new, {run.updated_count} updated, "
            f"{run.duplicate_count} duplicates, {run.expired_count} expired, {run.failed_count} failed.",
            level,
        )
        return redirect(reverse("admin:jobs_importrun_change", args=[run.pk]))

    @admin.action(description="Refresh selected automated sources now")
    def refresh_now(self, request, queryset):
        for source in queryset:
            run = run_source(source, user=request.user)
            self.message_user(request, f"{source}: {run.status}")
        self.audit_action(request, queryset, "source_refresh")


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    """Import history: every spreadsheet upload and feed refresh, with its row-level error report."""

    list_display = (
        "started_at",
        "what",
        "mode",
        "status",
        "created_count",
        "updated_count",
        "duplicate_count",
        "failed_count",
        "expired_count",
        "triggered_by",
    )
    list_filter = ("status", "mode", ("source", admin.EmptyFieldListFilter), "source")
    search_fields = ("filename",)
    fields = (
        "what",
        "mode",
        "status",
        "triggered_by",
        "started_at",
        "finished_at",
        "created_count",
        "updated_count",
        "duplicate_count",
        "failed_count",
        "expired_count",
        "error_report",
        "next_step",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Import")
    def what(self, obj):
        return str(obj.source) if obj.source else f"Spreadsheet: {obj.filename}"

    @admin.display(description="Errors by row")
    def error_report(self, obj):
        if not obj.errors:
            return "None"
        rows = format_html_join(
            "",
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>",
            ((e.get("row") or "—", e.get("title", ""), "; ".join(e.get("errors", []))) for e in obj.errors if isinstance(e, dict)),
        )
        plain = format_html_join("", "<li>{}</li>", ((e,) for e in obj.errors if not isinstance(e, dict)))
        return format_html(
            "<table><thead><tr><th>Row</th><th>Title</th><th>Problem</th></tr></thead><tbody>{}</tbody></table><ul>{}</ul>", rows, plain
        )

    @admin.display(description="")
    def next_step(self, obj):
        if obj.status == ImportRun.Status.PENDING and obj.source is None:
            return format_html(
                '<a class="button" href="{}">Review and confirm this import</a>', reverse("admin:jobs_job_import_preview", args=[obj.pk])
            )
        return format_html('<a href="{}">Upload another file</a>', reverse("admin:jobs_job_import"))


@admin.register(JobReport)
class JobReportAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "job", "reason", "status")
    list_filter = ("status", "reason")
    list_editable = ("status",)
    raw_id_fields = ("job", "reporter")
    actions = ["reject_jobs"]

    @admin.action(description="Reject the reported job(s) and resolve reports")
    def reject_jobs(self, request, queryset):
        jobs = Job.objects.filter(reports__in=queryset).distinct()
        self.audit_action(request, jobs, "job_reject_from_report")
        jobs.update(status=Job.Status.REJECTED)
        queryset.update(status=JobReport.Status.RESOLVED)


@admin.register(SavedJob)
class SavedJobAdmin(admin.ModelAdmin):
    list_display = ("user", "job", "created_at")
    raw_id_fields = ("user", "job")
