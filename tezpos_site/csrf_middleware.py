"""DEBUG rejimida so‘rov Origin/Referer/Host ni CSRF_TRUSTED_ORIGINS ga qo‘shadi."""
from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings


def _trust(origin: str) -> None:
    if not origin:
        return
    trusted = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
    if origin not in trusted:
        settings.CSRF_TRUSTED_ORIGINS = trusted + [origin]


class DynamicCsrfOriginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "DEBUG", False):
            raw = request.META.get("HTTP_ORIGIN") or request.META.get("HTTP_REFERER") or ""
            if raw:
                parts = urlsplit(raw)
                if parts.scheme in ("http", "https") and parts.netloc:
                    _trust(f"{parts.scheme}://{parts.netloc}")
            # Origin yo'q bo'lsa — Host dan yasash (ba'zi brauzerlar)
            host = request.get_host()
            if host:
                scheme = "https" if request.is_secure() else "http"
                _trust(f"{scheme}://{host}")
        return self.get_response(request)
