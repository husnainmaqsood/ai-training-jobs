import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from billing import entitlements
from billing.models import Subscription
from core.audit import record
from core.models import AuditEvent
from core.ratelimit import ratelimit

from .forms import DeleteAccountForm, LoginForm, ProfileForm, RegistrationForm, work_experience_formset
from .models import User
from .tokens import email_verification_token

logger = logging.getLogger(__name__)


def send_verification_email(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    url = settings.SITE_URL + reverse("accounts:verify_email", args=[uid, token])
    body = render_to_string("emails/verify_email.txt", {"user": user, "url": url, "SITE_NAME": settings.SITE_NAME})
    send_mail(f"Verify your email for {settings.SITE_NAME}", body, settings.DEFAULT_FROM_EMAIL, [user.email])


@ratelimit("register", limit=5, window_seconds=3600)
def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:profile")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        try:
            send_verification_email(request, user)
        except Exception:
            logger.exception("Failed to send verification email")
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome! We've sent a verification link to your email. Add your details below — the CV builder reuses them.")
        return redirect("accounts:profile")
    return render(request, "accounts/register.html", {"form": form})


@method_decorator(ratelimit("login", limit=10, window_seconds=300), name="dispatch")
class LoginView(auth_views.LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


def verify_email(request, uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None
    if user is not None and email_verification_token.check_token(user, token):
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        messages.success(request, "Your email address is verified.")
        return redirect("accounts:profile" if request.user.is_authenticated else "accounts:login")
    messages.error(request, "This verification link is invalid or has expired.")
    return redirect("core:home")


@login_required
@require_POST
@ratelimit("resend_verify", limit=3, window_seconds=3600, by="user")
def resend_verification(request):
    if request.user.email_verified:
        messages.info(request, "Your email is already verified.")
    else:
        send_verification_email(request, request.user)
        messages.success(request, "Verification email sent.")
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse("accounts:profile")
    return redirect(next_url)


@login_required
def dashboard(request):
    user = request.user
    quota = entitlements.quota_status(user)
    context = {
        "quota": quota,
        "subscription": entitlements.active_subscription(user),
        "master_cvs": user.master_cvs.all()[:3],
        "recent_generations": user.generation_requests.select_related("job").all()[:5],
        "applications": user.applications.select_related("job").all()[:5],
        "saved_jobs": user.saved_jobs.select_related("job", "job__platform").all()[:5],
        "profile": user.profile,
    }
    return render(request, "accounts/dashboard.html", context)


@login_required
def profile(request):
    from billing import credits

    user = request.user
    form = ProfileForm(request.POST or None, instance=user.profile)
    formset = work_experience_formset(user, request.POST or None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            form.save()
            formset.save(commit=False)  # applies cleaned data to instances and collects deletions
            for obj in formset.deleted_objects:
                obj.delete()
            kept = [f for f in formset.forms if f not in formset.deleted_forms and (f.instance.pk or f.has_changed())]
            for order, f in enumerate(kept):
                f.instance.user, f.instance.sort_order = user, order
                f.instance.save()
        messages.success(request, "Profile saved.")
        if request.POST.get("next") == reverse("cvs:builder"):
            return redirect("cvs:builder")
        return redirect("accounts:profile")
    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "formset": formset,
            "credits": credits.balance(user),
            "saved_count": user.saved_jobs.count(),
            "applications_count": user.applications.count(),
        },
    )


@login_required
def usage(request):
    from ai.models import AIUsageRecord
    from billing.models import UsageLedgerEntry

    return render(
        request,
        "accounts/usage.html",
        {
            "quota": entitlements.quota_status(request.user),
            "ledger": UsageLedgerEntry.objects.filter(user=request.user).select_related("generation")[:50],
            "ai_calls": AIUsageRecord.objects.filter(user=request.user).count(),
        },
    )


@login_required
def delete_account(request):
    form = DeleteAccountForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = request.user
        live = Subscription.objects.filter(user=user).exclude(status__in=[Subscription.Status.CANCELED, Subscription.Status.INCOMPLETE_EXPIRED])
        if live.exists():
            from billing.providers import BillingNotConfigured, BillingProviderError, get_provider

            try:
                provider = get_provider()
                for sub in live:
                    provider.cancel_immediately(sub)
            except (BillingNotConfigured, BillingProviderError):
                messages.error(
                    request, "We couldn't cancel your subscription automatically. Please cancel it in Billing first, then delete your account."
                )
                return redirect("billing:settings")
        user_id = user.pk
        with transaction.atomic():
            for cv in user.master_cvs.all():
                cv.delete()  # removes stored files too
            logout(request)
            user.delete()
            record("account_deleted", category=AuditEvent.Category.DATA, user_id=user_id)
        messages.success(request, "Your account and all associated CVs and data have been deleted.")
        return redirect("core:home")
    return render(request, "accounts/delete.html", {"form": form})
