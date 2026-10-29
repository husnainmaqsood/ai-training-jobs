from django.conf import settings

ASSETS = ("css/app.css", "js/app.js")


def _asset_version():
    """In development, '?v=<last change>' so browsers never reuse an old stylesheet. Production URLs are content-hashed."""
    if not settings.DEBUG:
        return ""
    mtimes = [(settings.BASE_DIR / "static" / name).stat().st_mtime for name in ASSETS if (settings.BASE_DIR / "static" / name).exists()]
    return f"?v={int(max(mtimes))}" if mtimes else ""


def site(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_URL": settings.SITE_URL,
        "CONTACT_EMAIL": settings.CONTACT_EMAIL,
        "DEBUG": settings.DEBUG,
        "AI_IS_MOCK": settings.USER_ACCOUNTS_ENABLED and settings.AI_PROVIDER == "mock",
        "USER_ACCOUNTS_ENABLED": settings.USER_ACCOUNTS_ENABLED,
        "ASSET_VERSION": _asset_version(),
    }
