"""Small cache-backed fixed-window rate limiter (works with Redis or local memory cache)."""

import functools
import hashlib
import time

from django.core.cache import cache
from django.http import HttpResponse


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def hit(key, limit, window_seconds):
    """Register one hit. Returns True if the caller is still within the limit."""
    bucket = int(time.time() // window_seconds)
    cache_key = "rl:" + hashlib.sha256(f"{key}:{bucket}".encode()).hexdigest()
    added = cache.add(cache_key, 1, timeout=window_seconds + 5)
    if added:
        return True
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds + 5)
        count = 1
    return count <= limit


def ratelimit(scope, limit, window_seconds, methods=("POST",), by="ip"):
    """Decorator. `by` is "ip" or "user" (falls back to ip for anonymous users)."""

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method in methods:
                if by == "user" and request.user.is_authenticated:
                    ident = f"u{request.user.pk}"
                else:
                    ident = f"ip{client_ip(request)}"
                if not hit(f"{scope}:{ident}", limit, window_seconds):
                    return HttpResponse("Too many requests. Please wait a moment and try again.", status=429, content_type="text/plain")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
