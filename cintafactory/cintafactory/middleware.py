# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations
import uuid
import re
from typing import Callable
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.http.request import validate_host

from .logging.logging_utils import bind_request_context, clear_request_context
from .rate_limit import load_limit_config
from .operations.slo_baseline import emit_web_request_baseline
from time import perf_counter


_SENSITIVE_ENDPOINT_PATTERNS = (
    re.compile(r"^/diagrams/[^/]+/(import|export|save|thumbnail)/?$"),
    re.compile(r"^/diagrams/likec4/(import|export|metadata)/?$"),
    re.compile(r"^/diagrams/likec4/editor(?:/.*)?$"),
    re.compile(r"^/dat/my/[^/]+/export(?:/.*)?$"),
    re.compile(r"^/dat/my/[^/]+/sections/[^/]+/attachments/upload/?$"),
    re.compile(r"^/dat/manage/dats/import/?$"),
)


class AppSecurityHeadersMiddleware:
    """
    Apply security response headers consistently across the app.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        public_media_url = getattr(settings, "SEAWEEDFS_PUBLIC_URL_PP", "").strip()
        public_media_parts = urlsplit(public_media_url)
        if public_media_parts.scheme and public_media_parts.netloc:
            public_media_origin = f"{public_media_parts.scheme}://{public_media_parts.netloc}"
        else:
            public_media_origin = public_media_url
        img_sources = "'self' data: blob:"
        if public_media_origin:
            img_sources = f"{img_sources} {public_media_origin}"
        if request.path.startswith("/diagrams/likec4/editor"):
            csp_policy = (
                "default-src 'self' data: blob: http: https:; "
                "frame-ancestors 'self'; "
                "img-src 'self' data: blob: http: https:; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: "
                "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
                "script-src-elem 'self' 'unsafe-inline' blob: "
                "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com "
                "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
                "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
                "frame-src 'self' data: blob: http: https:; "
                "worker-src 'self' data: blob:; "
                "connect-src 'self' data: blob: http: https: ws: wss:"
            )
        else:
            csp_policy = (
                "default-src 'self'; frame-ancestors 'self'; "
                f"img-src {img_sources}; "
                "script-src 'self' https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                f"frame-src 'self' {settings.DRAWIO_PUBLIC_ORIGIN}; "
                f"connect-src 'self' {settings.DRAWIO_PUBLIC_ORIGIN}"
            )
        response.setdefault("Content-Security-Policy", csp_policy)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", settings.SECURE_REFERRER_POLICY)
        response.setdefault("X-Frame-Options", settings.X_FRAME_OPTIONS)
        response.setdefault("Cross-Origin-Opener-Policy", settings.SECURE_CROSS_ORIGIN_OPENER_POLICY)
        return response


class LoggingContextMiddleware:
    """
    Populate thread-local logging context for each request.

    Extracts correlation identifiers from headers when provided and falls
    back to a generated UUID. The context is cleared once the response cycle
    has completed to avoid leaking data between requests.
    """

    header_names = ("X-Request-ID", "X-Correlation-ID")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = self._extract_request_id(request)
        user = getattr(request, "user", None)
        bind_request_context(
            request_id=request_id,
            path=request.path,
            method=request.method,
            user_id=getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None,
            username=getattr(user, "username", None) if getattr(user, "is_authenticated", False) else None,
        )
        request.request_id = request_id  # type: ignore[attr-defined]
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            status_code = getattr(response, "status_code", None)
            if status_code is not None:
                bind_request_context(status_code=status_code)
            return response
        finally:
            clear_request_context()

    def _extract_request_id(self, request: HttpRequest) -> str:
        for header in self.header_names:
            if header in request.headers:
                value = request.headers.get(header)
                if value:
                    return value
        return uuid.uuid4().hex


class SLOBaselineMiddleware:
    """
    Emit request latency and status signals used by baseline SLO tracking.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started_at = perf_counter()
        try:
            response = self.get_response(request)
        except Exception as exc:
            duration_ms = (perf_counter() - started_at) * 1000.0
            emit_web_request_baseline(
                request,
                duration_ms=duration_ms,
                status_code=500,
                success=False,
                error_type=exc.__class__.__name__,
            )
            raise
        duration_ms = (perf_counter() - started_at) * 1000.0
        status_code = int(getattr(response, "status_code", 0) or 0)
        emit_web_request_baseline(
            request,
            duration_ms=duration_ms,
            status_code=status_code,
            success=status_code < 500,
        )
        return response


class DynamicCsrfTrustedOriginsMiddleware:
    """
    Ensure CSRF trusted origins keep pace with allowed hosts at runtime.

    The middleware inspects the incoming Origin header and, when it points to
    a host already permitted by ALLOWED_HOSTS, automatically appends trusted
    origins. Strict HTTP security keeps production CSRF origins HTTPS-only.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        origin = request.META.get("HTTP_ORIGIN")
        if origin:
            self._ensure_origin_allowed(origin)
        return self.get_response(request)

    def _ensure_origin_allowed(self, origin: str) -> None:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return
        if not validate_host(parsed.hostname, settings.ALLOWED_HOSTS):
            return

        origins = set(settings.CSRF_TRUSTED_ORIGINS)
        netloc = parsed.netloc
        if getattr(settings, "STRICT_HTTP_SECURITY", False):
            if parsed.scheme != "https":
                return
            origins.add(f"https://{netloc}")
            settings.CSRF_TRUSTED_ORIGINS = sorted(origins)
            return

        for scheme in ("http", "https"):
            candidate = f"{scheme}://{netloc}"
            origins.add(candidate)
        settings.CSRF_TRUSTED_ORIGINS = sorted(origins)


class HideDisabledApiBrowserPagesMiddleware:
    """Hide REST pages from browser navigation when API access is disabled."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path_info
        is_api_path = path == "/api" or path.startswith("/api/")
        user = getattr(request, "user", None)
        has_api_access = bool(
            getattr(user, "is_authenticated", False)
            and getattr(user, "can_use_api", False)
        )
        if is_api_path and not has_api_access and self._is_browser_request(request):
            raise Http404
        return self.get_response(request)

    @staticmethod
    def _is_browser_request(request: HttpRequest) -> bool:
        if request.headers.get("Sec-Fetch-Mode", "").lower() == "navigate":
            return True
        accept_header = request.headers.get("Accept", "")
        explicitly_accepts_html = any(
            media_type.split(";", 1)[0].strip().lower() == "text/html"
            for media_type in accept_header.split(",")
        )
        return explicitly_accepts_html and request.accepts("text/html")


class RateLimitMiddleware:
    """
    Simple request throttling for both app pages and API endpoints.

    Uses the Django cache backend to track per-minute counters. The limits and
    exclusions are loaded from conf/limit.json.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        config = load_limit_config()
        if self._is_excluded(request, config):
            return self.get_response(request)

        sensitive_limit = self._sensitive_endpoint_limit(request)
        if sensitive_limit:
            client_ip = self._get_client_ip(request)
            if self._is_over_limit("sensitive", "ip", client_ip, sensitive_limit, 60):
                return self._rate_limited_response(is_api=request.path.startswith("/api/"))

        is_api = request.path.startswith("/api/")
        window_seconds = 60
        client_ip = self._get_client_ip(request)

        if is_api:
            api_limit = config["api"]["limit_per_ip_per_minute"]
            if self._is_over_limit("api", "ip", client_ip, api_limit, window_seconds):
                return self._rate_limited_response(is_api=True)
            return self.get_response(request)

        app_ip_limit = config["app"]["limit_per_ip_per_minute"]
        if self._is_over_limit("app", "ip", client_ip, app_ip_limit, window_seconds):
            return self._rate_limited_response(is_api=False)

        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            app_user_limit = config["app"]["limit_per_user_per_minute"]
            user_key = f"{user.pk}"
            if self._is_over_limit("app", "user", user_key, app_user_limit, window_seconds):
                return self._rate_limited_response(is_api=False)

        return self.get_response(request)

    def _is_excluded(self, request: HttpRequest, config: dict) -> bool:
        path = request.path
        if config.get("is_static_exluded") and settings.STATIC_URL:
            if path.startswith(settings.STATIC_URL):
                return True
        if config.get("is_admin_exluded"):
            if path.startswith("/admin/"):
                return True
        return False

    def _sensitive_endpoint_limit(self, request: HttpRequest) -> int:
        path = request.path or ""
        if not any(pattern.match(path) for pattern in _SENSITIVE_ENDPOINT_PATTERNS):
            return 0
        try:
            return int(getattr(settings, "ENDPOINT_RATE_LIMIT_PER_IP_PER_MINUTE", 30))
        except (TypeError, ValueError):
            return 30

    def _get_client_ip(self, request: HttpRequest) -> str:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")

    def _is_over_limit(
        self,
        scope: str,
        bucket: str,
        key_part: str,
        limit: int,
        window_seconds: int,
    ) -> bool:
        if not limit or limit <= 0:
            return False
        cache_key = f"rate:{scope}:{bucket}:{key_part}"
        count = self._increment(cache_key, window_seconds)
        return count > limit

    def _increment(self, cache_key: str, window_seconds: int) -> int:
        try:
            added = cache.add(cache_key, 1, timeout=window_seconds)
            if added:
                return 1
            return cache.incr(cache_key)
        except Exception:
            current = cache.get(cache_key) or 0
            try:
                current = int(current) + 1
            except (TypeError, ValueError):
                current = 1
            cache.set(cache_key, current, timeout=window_seconds)
            return current

    def _rate_limited_response(self, is_api: bool) -> HttpResponse:
        if is_api:
            return JsonResponse({"detail": "Rate limit exceeded."}, status=429)
        return HttpResponse("Rate limit exceeded.", status=429)
