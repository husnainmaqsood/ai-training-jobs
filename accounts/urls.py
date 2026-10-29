from django.contrib.auth import forms as auth_forms
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from core.ratelimit import ratelimit

from . import views
from .forms import StyledMixin

app_name = "accounts"


class StyledPasswordResetForm(StyledMixin, auth_forms.PasswordResetForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


class StyledSetPasswordForm(StyledMixin, auth_forms.SetPasswordForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


class StyledPasswordChangeForm(StyledMixin, auth_forms.PasswordChangeForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("verify/<uidb64>/<token>/", views.verify_email, name="verify_email"),
    path("verify/resend/", views.resend_verification, name="resend_verification"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("profile/", views.profile, name="profile"),
    path("usage/", views.usage, name="usage"),
    path("delete/", views.delete_account, name="delete"),
    path(
        "password/change/",
        auth_views.PasswordChangeView.as_view(
            template_name="accounts/password_form.html",
            form_class=StyledPasswordChangeForm,
            success_url=reverse_lazy("accounts:profile"),
            extra_context={"title": "Change password"},
        ),
        name="password_change",
    ),
    path(
        "password/reset/",
        ratelimit("pwreset", limit=5, window_seconds=3600)(
            auth_views.PasswordResetView.as_view(
                template_name="accounts/password_form.html",
                form_class=StyledPasswordResetForm,
                email_template_name="emails/password_reset.txt",
                subject_template_name="emails/password_reset_subject.txt",
                success_url=reverse_lazy("accounts:password_reset_done"),
                extra_context={"title": "Reset your password"},
            )
        ),
        name="password_reset",
    ),
    path(
        "password/reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_form.html",
            form_class=StyledSetPasswordForm,
            success_url=reverse_lazy("accounts:login"),
            extra_context={"title": "Choose a new password"},
        ),
        name="password_reset_confirm",
    ),
]
