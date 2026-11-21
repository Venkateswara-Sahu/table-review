import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from table_review.accounts.service import AuthenticationError, SetupUnavailable
from table_review.accounts.types import Actor, SessionGrant
from table_review.api_types import (
    RuntimeCapabilitiesView,
    SessionCreated,
    SessionView,
    SetupStatusView,
)
from table_review.runtime import models_available

router = APIRouter(prefix="/api/v1")


class SignInInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024, repr=False)


def _is_loopback_request(request: Request) -> bool:
    if any(
        request.headers.get(header)
        for header in ("x-forwarded-for", "forwarded", "x-real-ip")
    ):
        return False
    host = (request.client.host if request.client else "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "testclient"}:
        return True
    host = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _apply_session_cookies(response: Response, grant: SessionGrant, settings) -> dict:
    response.set_cookie(
        "tr_session",
        grant.token,
        max_age=7 * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        "tr_csrf",
        grant.csrf_token,
        max_age=7 * 86400,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )
    return {
        "actor": grant.actor.model_dump(mode="json"),
        "csrf_token": grant.csrf_token,
        "expires_at": grant.expires_at,
    }


def require_actor(request: Request) -> Actor:
    return request.app.state.accounts.resolve_session(request.cookies.get("tr_session", ""))


@router.get("/setup/status", response_model=SetupStatusView)
def setup_status(request: Request):
    settings = request.app.state.settings
    if settings.deployment_mode != "local" or not _is_loopback_request(request):
        return {
            "setup_required": False,
            "deployment_mode": settings.deployment_mode,
            "local_onboarding_enabled": False,
        }
    return {
        "setup_required": not request.app.state.accounts.has_accounts(),
        "deployment_mode": "local",
        "local_onboarding_enabled": True,
    }


@router.post("/setup", status_code=201, response_model=SessionCreated)
def setup_first_account(payload: SignInInput, request: Request, response: Response):
    settings = request.app.state.settings
    if settings.deployment_mode != "local" or not _is_loopback_request(request):
        raise SetupUnavailable("Local first-run setup is disabled")
    grant = request.app.state.accounts.bootstrap_first_account(payload.username, payload.password)
    return _apply_session_cookies(response, grant, settings)


@router.post("/session", response_model=SessionCreated)
def sign_in(payload: SignInInput, request: Request, response: Response):
    grant = request.app.state.accounts.sign_in(
        payload.username, payload.password, request.client.host if request.client else "unknown"
    )
    return _apply_session_cookies(response, grant, request.app.state.settings)


@router.get("/session", response_model=SessionView)
def session_state(request: Request, response: Response):
    settings = request.app.state.settings
    token = request.cookies.get("tr_session", "")
    try:
        actor = request.app.state.accounts.resolve_session(token)
    except AuthenticationError:
        if (
            settings.deployment_mode == "local"
            and settings.local_auto_login
            and _is_loopback_request(request)
        ):
            grant = request.app.state.accounts.ensure_local_session()
            _apply_session_cookies(response, grant, settings)
            return {
                "actor": grant.actor.model_dump(mode="json"),
                "csrf_token": grant.csrf_token,
            }
        raise
    csrf = request.cookies.get("tr_csrf", "")
    if csrf:
        try:
            request.app.state.accounts.validate_csrf(token, csrf)
        except AuthenticationError:
            csrf = ""
    return {"actor": actor.model_dump(mode="json"), "csrf_token": csrf}


@router.get("/capabilities", response_model=RuntimeCapabilitiesView)
def runtime_capabilities(
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
):
    settings = request.app.state.settings
    vlm_ready = bool(
        settings.vlm_base_url
        and settings.vlm_model
        and (settings.deployment_mode != "hosted" or settings.allow_vlm_in_hosted)
    )
    return {
        "deployment_mode": settings.deployment_mode,
        "local_auto_login": settings.deployment_mode == "local" and settings.local_auto_login,
        "retention_days": settings.retention_days,
        "default_extraction_backend": settings.default_extraction_backend,
        "max_selected_pages": settings.max_selected_pages,
        "available_backends": {
            "native_fast": True,
            "docling": models_available(settings.model_path),
            "vlm": vlm_ready,
        },
    }


@router.delete("/session", status_code=204)
def sign_out(request: Request, response: Response, actor: Annotated[Actor, Depends(require_actor)]):
    request.app.state.accounts.sign_out(request.cookies.get("tr_session", ""))
    response.delete_cookie(
        "tr_session",
        secure=request.app.state.settings.secure_cookies,
        samesite="strict",
        httponly=True,
    )
    response.delete_cookie(
        "tr_csrf", secure=request.app.state.settings.secure_cookies, samesite="strict"
    )
