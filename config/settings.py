"""
Django settings for the Orexia AI platform.

All environment-specific values are read from environment variables (see
`.env.example`). Nothing secret is hard-coded here.
"""

import json
import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
_RUNNING_TESTS = "pytest" in sys.modules or (len(sys.argv) > 1 and sys.argv[1] == "test")
if not _RUNNING_TESTS:
    load_dotenv(BASE_DIR / ".env")  # tests stay hermetic: never read a developer's .env


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", False)
TESTING = env_bool("TESTING", False) or _RUNNING_TESTS

SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-development-only-key-do-not-use-in-production"
    else:
        raise RuntimeError("SECRET_KEY must be set when DEBUG is off.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
SITE_NAME = env("SITE_NAME", "Orexia AI")
# Demo listings from `seed_demo_jobs` stay out of the directory and its stats unless this is on.
SHOW_DEMO_JOBS = env_bool("SHOW_DEMO_JOBS", False)
# Public sign-up/login, profiles, the CV builder, billing, saved jobs and the application tracker. Off for the
# directory-only release: those URLs return 404 and the UI hides them. Django Admin login is unaffected.
USER_ACCOUNTS_ENABLED = env_bool("USER_ACCOUNTS_ENABLED", False)

INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",  # runserver serves static via WhiteNoise too (same headers as production)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "django.contrib.postgres",
    "django.contrib.humanize",
    "core",
    "accounts",
    "jobs",
    "referrals",
    "ai",
    "billing",
    "cvs",
    "tracker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "core.middleware.UserAccountsGateMiddleware",
    "core.middleware.ErrorLoggingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", "postgres:///aitrainjobs"),
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:profile"
LOGOUT_REDIRECT_URL = "core:home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG or TESTING else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Uploaded CVs are private. They are stored outside STATIC/MEDIA URL space and
# only ever served through ownership-checked views.
# In development, make browsers re-check CSS/JS on every load so a rebuilt stylesheet is never stale.
if DEBUG:
    WHITENOISE_MAX_AGE = 0

PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "private_media")))
MEDIA_ROOT = PRIVATE_MEDIA_ROOT
MEDIA_URL = "/no-public-media/"

CV_MAX_UPLOAD_BYTES = env_int("CV_MAX_UPLOAD_BYTES", 5 * 1024 * 1024)
CV_MAX_TEXT_CHARS = env_int("CV_MAX_TEXT_CHARS", 30000)
JOB_DESCRIPTION_MAX_CHARS = env_int("JOB_DESCRIPTION_MAX_CHARS", 20000)
DATA_UPLOAD_MAX_MEMORY_SIZE = CV_MAX_UPLOAD_BYTES + 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440

# --- Security -----------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 24 * 14)
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

if not DEBUG and not TESTING:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)

# --- Cache (rate limiting, short-lived data) ----------------------------------
REDIS_URL = env("REDIS_URL", "")
if REDIS_URL and not TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Email --------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Orexia AI <no-reply@localhost>")
CONTACT_EMAIL = env("CONTACT_EMAIL", "support@localhost")

# --- Celery -------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", not bool(REDIS_URL)) or TESTING
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TIMEZONE = "UTC"

# --- AI provider --------------------------------------------------------------
AI_PROVIDER = env("AI_PROVIDER", "openai")  # "openai" or "mock" (mock only allowed when DEBUG/TESTING)
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "") or None
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4.1-mini")
AI_TIMEOUT_SECONDS = env_int("AI_TIMEOUT_SECONDS", 90)
AI_MAX_RETRIES = env_int("AI_MAX_RETRIES", 2)
AI_MAX_OUTPUT_TOKENS = env_int("AI_MAX_OUTPUT_TOKENS", 6000)
AI_GLOBAL_DAILY_GENERATION_LIMIT = env_int("AI_GLOBAL_DAILY_GENERATION_LIMIT", 2000)
AI_GLOBAL_DAILY_COST_LIMIT_USD = float(env("AI_GLOBAL_DAILY_COST_LIMIT_USD", "50"))
AI_USER_HOURLY_REQUEST_LIMIT = env_int("AI_USER_HOURLY_REQUEST_LIMIT", 12)
# Per-model pricing in USD per 1M tokens. Override with JSON, e.g.
# AI_MODEL_PRICING='{"gpt-4.1-mini": {"input": 0.40, "output": 1.60}}'
AI_MODEL_PRICING = json.loads(env("AI_MODEL_PRICING", "{}") or "{}")

# --- Billing ------------------------------------------------------------------
BILLING_PROVIDER = env("BILLING_PROVIDER", "stripe")
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", "")
BILLING_PAST_DUE_GRACE_DAYS = env_int("BILLING_PAST_DUE_GRACE_DAYS", 3)

# CV-builder credits: one credit = one generated CV. Packs are sold as one-time Stripe Checkout payments
# (no Stripe price IDs needed). Override with JSON, e.g.
# CREDIT_PACKS='[{"code": "starter", "credits": 3, "price_cents": 500, "label": "Starter"}]'
CREDIT_PACKS = json.loads(
    env("CREDIT_PACKS", "")
    or json.dumps(
        [
            {"code": "single", "credits": 1, "price_cents": 200, "label": "Single CV"},
            {"code": "starter", "credits": 5, "price_cents": 800, "label": "Starter"},
            {"code": "pro", "credits": 15, "price_cents": 2000, "label": "Job hunt"},
        ]
    )
)
CREDIT_CURRENCY = env("CREDIT_CURRENCY", "usd")
# Without Stripe keys, test mode lets signed-in users add credits for free so the flow can be exercised.
# It is never enabled implicitly in production.
BILLING_TEST_MODE = env_bool("BILLING_TEST_MODE", DEBUG)

# --- Data retention (days) — used by `manage.py purge_expired_data` -------------
RETENTION_DAYS = {
    "clicks": env_int("RETENTION_CLICKS_DAYS", 400),
    "errors": env_int("RETENTION_ERRORS_DAYS", 90),
    "webhooks": env_int("RETENTION_WEBHOOK_PAYLOAD_DAYS", 400),
    "unconfirmed_newsletter": 30,
    "failed_generations": 30,
}

# --- Referral tracking --------------------------------------------------------
CLICK_HASH_SALT = env("CLICK_HASH_SALT", SECRET_KEY)

# --- Logging ------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": "core.logging.RedactSensitiveFilter"}},
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain", "filters": ["redact"]},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
    },
}
