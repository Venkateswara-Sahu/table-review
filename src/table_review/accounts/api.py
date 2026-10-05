from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from table_review.accounts.service import AuthenticationError
from table_review.accounts.types import Actor

router = APIRouter(prefix="/api/v1")


class SignInInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024, repr=False)


def require_actor(request: Request) -> Actor:
    return request.app.state.accounts.resolve_session(request.cookies.get("tr_session", ""))


@router.post("/session")
def sign_in(payload: SignInInput, request: Request, response: Response):
    grant = request.app.state.accounts.sign_in(
        payload.username, payload.password, request.client.host if request.client else "unknown"
    )
    settings = request.app.state.settings
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


@router.get("/session")
def session_state(request: Request, actor: Annotated[Actor, Depends(require_actor)]):
    csrf = request.cookies.get("tr_csrf", "")
    if csrf:
        try:
            request.app.state.accounts.validate_csrf(request.cookies.get("tr_session", ""), csrf)
        except AuthenticationError:
            csrf = ""
    return {"actor": actor.model_dump(mode="json"), "csrf_token": csrf}


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
