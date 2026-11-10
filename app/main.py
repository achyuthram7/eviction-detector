"""FastAPI application factory. Run with: python manage.py run"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.routes import admin, public, staff
from app.templating import APP_DIR, render
from core.settings import get_settings

# Everything is served from this origin: no CDNs, fonts or analytics (hard rule 5: offline).
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Eviction Settlement Opportunity Detector (prototype)",
        docs_url=None,  # the built-in API docs load assets from a CDN
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["Referrer-Policy"] = "no-referrer"  # tokens live in URLs
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="esod_session",
        same_site="lax",
        max_age=8 * 3600,
    )
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.include_router(public.router)
    app.include_router(auth.router)
    app.include_router(staff.router)
    app.include_router(admin.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code in (302, 303) and exc.headers and "Location" in exc.headers:
            from fastapi.responses import RedirectResponse

            return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            detail=exc.detail if exc.status_code != 404 else "Not found",
        )

    return app


app = create_app()
