"""
Authentication API endpoints.
"""

from fastapi import APIRouter, Depends, Request, Response, HTTPException, status
from sqlalchemy.orm import Session
from app.models.user import User
from app.web.config import web_settings
from app.web.schemas.common import APIResponse
from app.web.schemas.auth import LoginRequest, UserDTO
from app.web.services.auth_service import AuthService
from app.web.security.csrf import csrf_manager
from app.web.dependencies import get_current_user, verify_csrf, get_db

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.get("/csrf", response_model=APIResponse[dict])
def get_csrf_token(response: Response):
    """Issues a fresh signed CSRF token and sets the cookie."""
    token = csrf_manager.generate_token()
    response.set_cookie(
        key=web_settings.WEB_CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # Accessible to client JS for custom header transmission
        secure=web_settings.WEB_COOKIE_SECURE,
        samesite=web_settings.WEB_COOKIE_SAMESITE,
        path="/"
    )
    return APIResponse.ok({"csrf_token": token})


@router.post("/login", response_model=APIResponse[dict])
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db)
):
    """
    Authenticates user, rotates session token, and sets secure HttpOnly cookie.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("User-Agent")
    old_token = request.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)

    success, message, raw_token, user_info = AuthService.authenticate(
        db=db,
        username=payload.username,
        password=payload.password,
        ip_address=client_ip,
        user_agent=user_agent,
        old_token=old_token
    )

    if not success:
        if "locked" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=message
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=message
        )

    # Set secure HttpOnly session cookie
    response.set_cookie(
        key=web_settings.WEB_SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=web_settings.WEB_COOKIE_SECURE,
        samesite=web_settings.WEB_COOKIE_SAMESITE,
        max_age=web_settings.WEB_SESSION_ABSOLUTE_HOURS * 3600,
        path="/"
    )

    # Issue fresh CSRF token
    csrf_token = csrf_manager.generate_token()
    response.set_cookie(
        key=web_settings.WEB_CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=web_settings.WEB_COOKIE_SECURE,
        samesite=web_settings.WEB_COOKIE_SAMESITE,
        path="/"
    )

    return APIResponse.ok(user_info, meta={"message": message, "csrf_token": csrf_token})


@router.post("/logout", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Invalidates the active session and clears the session cookie."""
    raw_token = request.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)
    AuthService.logout(db, raw_token, current_user)

    response.delete_cookie(key=web_settings.WEB_SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(key=web_settings.WEB_CSRF_COOKIE_NAME, path="/")

    return APIResponse.ok({"logged_out": True})


@router.get("/me", response_model=APIResponse[UserDTO])
def get_me(current_user: User = Depends(get_current_user)):
    """Returns profile and RBAC role for the currently authenticated user."""
    return APIResponse.ok(UserDTO(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        role=current_user.role,
        is_active=current_user.is_active,
        last_login_at=current_user.last_login_at.isoformat() if current_user.last_login_at else None
    ))
