"""Security middleware: Rate limiting, Security Headers, Request Size Limits"""

import time
from collections import defaultdict
from typing import Callable, Optional

from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter for auth endpoints."""
    
    def __init__(self, app, settings=None):
        super().__init__(app)
        self.settings = settings or get_settings()
        self.requests = defaultdict(list)
        self.cleanup_interval = 60  # seconds
        self.last_cleanup = time.time()
        
        # Rate limit config: (max_requests, window_seconds)
        self.limits = {
            "/api/users/login": (5, 60),           # 5 req/min
            "/api/users/register": (3, 60),        # 3 req/min
            "/admin/login": (5, 60),                # 5 req/min
            "/api/cards/": (30, 60),               # 30 req/min for card activation
            "/api/admin/cards/create-with-user": (10, 60),  # 10 req/min
            "default": (100, 60),                   # 100 req/min default
        }
    
    def _get_limit(self, path: str) -> tuple[int, int]:
        for pattern, limit in self.limits.items():
            if path.startswith(pattern):
                return limit
        return self.limits["default"]
    
    def _cleanup(self):
        now = time.time()
        if now - self.last_cleanup > self.cleanup_interval:
            cutoff = now - max(window for _, window in self.limits.values())
            for key in list(self.requests.keys()):
                self.requests[key] = [t for t in self.requests[key] if t > cutoff]
                if not self.requests[key]:
                    del self.requests[key]
            self.last_cleanup = now
    
    def _get_client_id(self, request: Request) -> str:
        # Use IP + User-Agent for identification
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else request.client.host
        ua = request.headers.get("User-Agent", "")[:50]
        return f"{ip}:{ua}"
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        self._cleanup()
        
        client_id = self._get_client_id(request)
        path = request.url.path
        max_requests, window = self._get_limit(path)
        
        now = time.time()
        cutoff = now - window
        
        # Filter recent requests
        recent = [t for t in self.requests[client_id] if t > cutoff]
        
        if len(recent) >= max_requests:
            # Rate limited
            retry_after = int(window - (now - recent[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "retry_after": retry_after},
                headers={"Retry-After": str(retry_after)}
            )
        
        recent.append(now)
        self.requests[client_id] = recent
        
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(max(0, max_requests - len(recent)))
        response.headers["X-RateLimit-Reset"] = str(int(now + window))
        
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    def __init__(self, app, settings=None):
        super().__init__(app)
        self.settings = settings or get_settings()
        self.is_production = self.settings.ALLOWED_ORIGIN.startswith("https://")
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # HSTS (only in production with HTTPS)
        if self.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # XSS protection (legacy but still useful)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Permissions policy (restrict dangerous features)
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )
        
        # Cross-Origin policies
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        
        # Content Security Policy
        csp = self._build_csp(request)
        response.headers["Content-Security-Policy"] = csp
        
        # Remove server header
        if "Server" in response.headers:
            del response.headers["Server"]
        
        return response
    
    def _build_csp(self, request: Request) -> str:
        """Build Content Security Policy."""
        # Allow inline styles/scripts for HTMX and dev tools
        # Note: 'unsafe-inline' required for inline <style> tags in templates
        style_src = "'self' 'unsafe-inline'"
        script_src = "'self' 'unsafe-inline' 'unsafe-eval'" if not self.is_production else "'self'"
        
        # Allow images from self and data URIs
        img_src = "'self' data: https:"
        
        # Connect to self and Stripe
        connect_src = "'self' https://api.stripe.com https://js.stripe.com"
        
        # Frame ancestors - none
        frame_ancestors = "'none'"
        
        return (
            f"default-src 'self'; "
            f"script-src {script_src}; "
            f"style-src {style_src}; "
            f"img-src {img_src}; "
            f"font-src 'self' data:; "
            f"connect-src {connect_src}; "
            f"frame-ancestors {frame_ancestors}; "
            f"form-action 'self'; "
            f"base-uri 'self'; "
            f"object-src 'none';"
        )


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Limit request body size to prevent DoS."""
    
    def __init__(self, app, max_size: int = 10 * 1024 * 1024):  # 10MB default
        super().__init__(app)
        self.max_size = max_size
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length = request.headers.get("Content-Length")
        if content_length and int(content_length) > self.max_size:
            return JSONResponse(
                status_code=413,
                content={"error": "request_too_large", "max_size": self.max_size}
            )
        return await call_next(request)