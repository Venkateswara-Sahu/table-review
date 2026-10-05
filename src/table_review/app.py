from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from table_review.accounts.api import require_actor, router
from table_review.accounts.service import (
    AccountService,
    AuthenticationError,
    InvalidAccount,
    SignInThrottled,
)
from table_review.accounts.types import Actor
from table_review.settings import Settings
from table_review.storage.blobs import PrivateBlobStore
from table_review.storage.db import create_session_factory
from table_review.storage.repository import NotFound, Repository, RevisionConflict


def error_response(request, status, code, message, details=None):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        },
        headers={
            "Cache-Control": "no-store",
            "X-Request-ID": request_id,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        },
    )


def create_app(settings: Settings) -> FastAPI:
    factory = create_session_factory(settings.database_url, schema=settings.database_schema)

    @asynccontextmanager
    async def lifespan(app):
        yield
        factory.kw["bind"].dispose()

    app = FastAPI(title="Table Review", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.accounts = AccountService(factory)
    app.state.repository = Repository(factory)
    app.state.blobs = PrivateBlobStore(settings.blob_root)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request.state.request_id = str(uuid4())
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("origin") != settings.public_origin:
                return error_response(request, 403, "origin_rejected", "Request origin not allowed")
            if request.url.path != "/api/v1/session" or request.method != "POST":
                try:
                    await run_in_threadpool(
                        app.state.accounts.validate_csrf,
                        request.cookies.get("tr_session", ""),
                        request.headers.get("x-csrf-token", ""),
                    )
                except AuthenticationError as error:
                    status = 403 if str(error) == "CSRF token invalid" else 401
                    return error_response(
                        request,
                        status,
                        "csrf_rejected" if status == 403 else "unauthenticated",
                        "Request verification failed" if status == 403 else "Sign in required",
                    )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(AuthenticationError)
    async def auth_error(request, error):
        if isinstance(error, SignInThrottled):
            response = error_response(
                request, 429, "sign_in_throttled", "Try again after the sign-in window expires"
            )
            response.headers["Retry-After"] = "300"
            return response
        return error_response(
            request,
            401,
            "invalid_credentials"
            if request.method == "POST" and request.url.path == "/api/v1/session"
            else "unauthenticated",
            "Invalid credentials"
            if request.method == "POST" and request.url.path == "/api/v1/session"
            else "Sign in required",
        )

    @app.exception_handler(NotFound)
    async def not_found(request, error):
        return error_response(request, 404, "not_found", "Resource not found")

    @app.exception_handler(RevisionConflict)
    async def conflict(request, error):
        return error_response(
            request,
            409,
            "revision_conflict",
            "The table changed; reload before saving",
            {
                "current_revision_id": str(error.current_revision_id)
                if error.current_revision_id
                else None
            },
        )

    @app.exception_handler(InvalidAccount)
    async def invalid_account(request, error):
        return error_response(request, 400, "invalid_account", str(error))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        details = {
            "fields": [
                {"location": list(item["loc"]), "message": item["msg"]} for item in error.errors()
            ]
        }
        return error_response(request, 422, "invalid_request", "Check the request fields", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(
            request,
            error.status_code,
            "not_found" if error.status_code == 404 else "http_error",
            "Resource not found" if error.status_code == 404 else "Request could not be completed",
        )

    @app.exception_handler(Exception)
    async def internal_error(request, error):
        # Keep source text, session values and driver exception details out of HTTP responses.
        return error_response(request, 500, "internal_error", "Request could not be completed")

    app.include_router(router)

    @app.get("/api/v1/documents/{document_id}")
    def owned_document(document_id: UUID, actor: Annotated[Actor, Depends(require_actor)]):
        return app.state.repository.get_document(actor.id, document_id)

    return app
