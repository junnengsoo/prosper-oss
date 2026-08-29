from fastapi import APIRouter, Request, Response

from ..auth import (
    SESSION_COOKIE_NAME,
    CurrentUser,
    create_session_token,
    current_user_from_request,
    verify_access_password,
)
from ..config import get_settings
from ..dependencies import DashboardUser
from ..schemas import AuthLoginIn, AuthSessionOut, MeOut

router = APIRouter()


@router.post("/api/auth/login", response_model=AuthSessionOut)
def login(payload: AuthLoginIn, response: Response) -> AuthSessionOut:
    settings = get_settings()
    if not settings.auth_required:
        return AuthSessionOut(authenticated=True, email="dev@local.test")
    verify_access_password(payload.password)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return AuthSessionOut(authenticated=True)


@router.get("/api/auth/session", response_model=AuthSessionOut)
def auth_session(request: Request) -> AuthSessionOut:
    user = current_user_from_request(request)
    return AuthSessionOut(authenticated=True, email=user.email)


@router.post("/api/auth/logout", response_model=AuthSessionOut)
def logout(response: Response) -> AuthSessionOut:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return AuthSessionOut(authenticated=False)


@router.get("/api/me", response_model=MeOut)
def get_me(
    user: CurrentUser = DashboardUser,
) -> MeOut:
    return MeOut(auth_user_id=user.auth_user_id, email=user.email)
