from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


SessionRole = Literal["viewer", "admin"]
ROLE_COOKIE_NAME = "sqllab_role"


@dataclass(frozen=True)
class SessionContext:
    role: SessionRole = "viewer"
    authenticated: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _admin_username() -> str:
    return os.environ.get("APP_ADMIN_USERNAME", "admin")


def _admin_password() -> str:
    return os.environ.get("APP_ADMIN_PASSWORD", "admin")


def session_payload(context: SessionContext) -> dict[str, object]:
    return asdict(context)


def get_session_context(request: Request) -> SessionContext:
    role = request.cookies.get(ROLE_COOKIE_NAME)
    if role == "admin":
        return SessionContext(role="admin", authenticated=True)
    return SessionContext()


def require_admin_session(context: SessionContext = Depends(get_session_context)) -> SessionContext:
    if context.role != "admin":
        raise HTTPException(status_code=403, detail={"error": {"code": "admin_required", "message": "Admin access required"}})
    return context


@router.post("/login")
def login(request: Request, payload: LoginRequest) -> JSONResponse:
    if payload.username != _admin_username() or payload.password != _admin_password():
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "invalid_credentials", "message": "Invalid username or password"}},
        )

    response = JSONResponse({"data": session_payload(SessionContext(role="admin", authenticated=True))})
    response.set_cookie(ROLE_COOKIE_NAME, "admin", httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    response = JSONResponse({"data": session_payload(SessionContext())})
    response.delete_cookie(ROLE_COOKIE_NAME)
    return response


@router.get("/session")
def current_session(request: Request) -> dict[str, dict[str, object]]:
    return {"data": session_payload(get_session_context(request))}
