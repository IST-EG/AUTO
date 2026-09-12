"""
FastAPI dependencies for Web Control Center.

Provides database session injection, session token extraction,
current user resolution, RBAC role enforcement, and CSRF token validation.
"""

from typing import Optional, Callable, List, Generator
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.user import User, UserRole
from app.models.user_session import UserSession
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a SQLAlchemy database session.
    Automatically closes session after request handling.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Extracts and validates the session cookie.
    Returns the User if valid, or None if missing/invalid/expired.
    """
    raw_token = request.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)
    if not raw_token:
        return None

    result = session_manager.get_user_from_token(db, raw_token)
    if not result:
        return None

    user, _ = result
    return user


def get_current_user(
    user: Optional[User] = Depends(get_current_user_optional)
) -> User:
    """
    Guarantees the caller is an authenticated user.
    Raises HTTP 401 if unauthenticated.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required."
        )
    return user


def require_roles(*allowed_roles: UserRole) -> Callable[[User], User]:
    """
    Factory dependency enforcing RBAC role requirements.

    Example:
        @router.get("/admin", dependencies=[Depends(require_roles(UserRole.OWNER, UserRole.ADMIN))])
    """
    role_values = [r.value if isinstance(r, UserRole) else str(r) for r in allowed_roles]

    def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in role_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required one of: {role_values}"
            )
        return user

    return role_checker


def require_owner(user: User = Depends(get_current_user)) -> User:
    """Dependency enforcing that caller has OWNER role."""
    if user.role != UserRole.OWNER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation restricted to system OWNER."
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency enforcing that caller has at least ADMIN role (ADMIN or OWNER)."""
    if user.role not in (UserRole.ADMIN.value, UserRole.OWNER.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation restricted to ADMIN or OWNER."
        )
    return user


def require_operator(user: User = Depends(get_current_user)) -> User:
    """Dependency enforcing that caller has at least OPERATOR role (OPERATOR, ADMIN, or OWNER)."""
    if user.role not in (UserRole.OPERATOR.value, UserRole.ADMIN.value, UserRole.OWNER.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation restricted to OPERATOR, ADMIN, or OWNER."
        )
    return user


def verify_csrf(request: Request) -> None:
    """
    Enforces double-submit CSRF verification on state-changing methods (POST, PUT, PATCH, DELETE).
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        cookie_token = request.cookies.get(web_settings.WEB_CSRF_COOKIE_NAME)
        header_token = request.headers.get("X-CSRF-Token")

        valid, reason = csrf_manager.validate_double_submit(cookie_token, header_token)
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"CSRF validation failed: {reason}"
            )
