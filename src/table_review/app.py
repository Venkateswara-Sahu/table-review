from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from table_review.accounts.api import router
from table_review.accounts.service import (
    AccountService,
    AuthenticationError,
    InvalidAccount,
    SignInThrottled,
)
from table_review.api_types import ErrorEnvelope
from table_review.checks.persistence import RevisionChecker
from table_review.documents.api import router as documents_router
from table_review.documents.service import DocumentError, DocumentService
from table_review.documents.upload_limit import UploadSizeLimit
from table_review.exports.api import router as exports_router
from table_review.exports.service import ExportService
from table_review.jobs.api import router as jobs_router
from table_review.jobs.queue import QueueFull, TaskConflict, TaskQueue
from table_review.lifecycle.deletion import DeletionService
from table_review.lifecycle.journal import JournalUnavailable, LifecycleJournal
from table_review.reviews.api import router as reviews_router
from table_review.reviews.service import ReviewIncomplete, ReviewInputError, ReviewService
from table_review.settings import Settings
from table_review.storage.blobs import PrivateBlobStore
from table_review.storage.db import create_session_factory
from table_review.storage.repository import NotFound, Repository, RevisionConflict, UploadConflict
from table_review.tables.api import router as tables_router
from table_review.tables.service import TableInputError, TableService


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
    if settings.restore_pending_path.exists():
        raise JournalUnavailable("Restored service is awaiting verified reconciliation")
    factory = create_session_factory(settings.database_url, schema=settings.database_schema)

    @asynccontextmanager
    async def lifespan(app):
        yield
        factory.kw["bind"].dispose()

    app = FastAPI(
        title="Table Review",
        lifespan=lifespan,
        responses={
            status: {"model": ErrorEnvelope}
            for status in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503)
        },
    )
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.journal = LifecycleJournal(
        settings.lifecycle_journal_path, create=settings.initialize_journal
    )
    app.state.accounts = AccountService(factory, app.state.journal)
    app.state.repository = Repository(factory)
    app.state.repository.journal = app.state.journal
    app.state.blobs = PrivateBlobStore(settings.blob_root)
    app.state.documents = DocumentService(app.state.repository, app.state.blobs, settings)
    app.state.queue = TaskQueue(app.state.repository, settings, app.state.documents)
    app.state.documents.preparation_queue = app.state.queue
    app.state.reviews = ReviewService(app.state.repository)
    app.state.reviews.checker = RevisionChecker()
    app.state.tables = TableService(app.state.repository, app.state.documents, app.state.reviews)
    app.state.exports = ExportService(
        app.state.repository, app.state.reviews, app.state.queue, app.state.blobs
    )
    app.state.deletion = DeletionService(
        app.state.repository, app.state.blobs, app.state.journal, settings
    )
    app.state.queue.deletion = app.state.deletion
    app.add_middleware(UploadSizeLimit, max_pdf_bytes=settings.max_pdf_bytes)

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

    @app.exception_handler(JournalUnavailable)
    async def journal_unavailable(request, error):
        return error_response(
            request, 503, "lifecycle_unavailable", "Private storage verification is unavailable"
        )

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
        if getattr(request.state, "upload_size_exceeded", False):
            return error_response(
                request, 413, "byte_limit", "Upload exceeds the configured size limit"
            )
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
    app.include_router(documents_router)
    app.include_router(jobs_router)
    app.include_router(tables_router)
    app.include_router(reviews_router)
    app.include_router(exports_router)

    @app.exception_handler(ReviewIncomplete)
    async def review_incomplete(request, error):
        return error_response(request, 409, "review_incomplete", str(error))

    @app.exception_handler(ReviewInputError)
    @app.exception_handler(TableInputError)
    async def table_input_error(request, error):
        return error_response(request, 422, "invalid_table_operation", str(error))

    @app.exception_handler(QueueFull)
    async def queue_full(request, error):
        return error_response(
            request, 429, "queue_full", "Finish or cancel a queued task before submitting another"
        )

    @app.exception_handler(TaskConflict)
    async def task_conflict(request, error):
        return error_response(
            request, 409, "task_conflict", "Task retry key differs from saved request"
        )

    @app.exception_handler(DocumentError)
    async def document_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(UploadConflict)
    async def upload_conflict(request, error):
        return error_response(
            request, 409, "upload_conflict", "This upload retry key was used for a different file"
        )

    return app
