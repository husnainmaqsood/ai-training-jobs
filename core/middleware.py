import logging
import re
import traceback

from django.conf import settings
from django.http import Http404

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware:
    """Adds a Content-Security-Policy and Permissions-Policy to every response."""

    CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self' https://checkout.stripe.com https://billing.stripe.com; "
        "base-uri 'self'; object-src 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/admin/"):
            response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        return response


class UserAccountsGateMiddleware:
    """While USER_ACCOUNTS_ENABLED is off, every account/CV/billing/tracker URL is a plain 404 for everyone."""

    GATED = re.compile(r"^/(accounts|cv|billing|pricing|applications)(/|$)|^/jobs/(saved/|\d+/save/)|^/how-it-works/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.USER_ACCOUNTS_ENABLED and self.GATED.match(request.path):
            raise Http404
        return self.get_response(request)


class ErrorLoggingMiddleware:
    """Persists unhandled exceptions (without request bodies) for the admin system-health page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if getattr(settings, "TESTING", False) and not getattr(settings, "LOG_ERRORS_IN_TESTS", False):
            return None
        try:
            from core.models import SystemErrorLog

            SystemErrorLog.objects.create(
                path=request.path[:500],
                method=request.method[:10],
                exception_type=type(exception).__name__[:200],
                message=str(exception)[:2000],
                traceback="".join(traceback.format_exception(exception))[-8000:],
                user_id=getattr(getattr(request, "user", None), "pk", None),
            )
        except Exception:  # never let error logging break error handling
            logger.exception("Failed to persist SystemErrorLog")
        return None
