"""FastAPI dependencies for session, CSRF, and explicit all-permission checks."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from src.app.api.auth_service import AuthenticatedSession, AuthenticationFailure

SESSION_COOKIE = "intelliq_session"
CSRF_COOKIE = "intelliq_csrf"
CSRF_HEADER = "X-CSRF-Token"


def current_session(request: Request) -> AuthenticatedSession:
    """Resolve and revalidate the opaque local session."""

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required"
        )
    record = request.app.state.store.get_session(token)
    if record is None:
        return _missing_session()
    try:
        return request.app.state.auth.current(record)
    except AuthenticationFailure as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def optional_session(request: Request) -> AuthenticatedSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    record = request.app.state.store.get_session(token)
    if record is None:
        return None
    try:
        return request.app.state.auth.current(record)
    except AuthenticationFailure:
        return None


def require_org_permissions(
    *permissions: str, write: bool = False
) -> Callable[..., AuthenticatedSession]:
    """Build a dependency that checks every requested permission, never OR semantics."""

    def dependency(
        request: Request, session: AuthenticatedSession = Depends(current_session)
    ) -> AuthenticatedSession:
        organization_id = request.path_params.get("organization_id")
        if not isinstance(organization_id, str) or (
            not session.user.app_admin and organization_id not in session.user.organization_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="organization_access_denied"
            )
        granted = set(session.user.organization_permissions.get(organization_id, []))
        missing = sorted(set(permissions).difference(granted))
        if missing and not session.user.app_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "missing_permissions", "permissions": missing},
            )
        if write:
            _check_csrf(request, session)
        return session

    return dependency


def _check_csrf(request: Request, session: AuthenticatedSession) -> None:
    validate_csrf(request, session.record.csrf_token)


def validate_csrf(request: Request, expected_token: str) -> None:
    """Validate origin and double-submit token for cookie-authenticated mutations."""

    validate_origin(request)
    if (
        request.headers.get(CSRF_HEADER) != expected_token
        or request.cookies.get(CSRF_COOKIE) != expected_token
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_validation_failed")


def validate_origin(request: Request) -> None:
    """Reject cross-origin browser requests before login or session mutation."""

    origin = request.headers.get("origin")
    allowlist = request.app.state.settings.origin_allowlist
    if origin and origin.rstrip("/") not in allowlist:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin_not_allowed")


def _missing_session() -> AuthenticatedSession:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_not_found")


__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "current_session",
    "optional_session",
    "require_org_permissions",
    "validate_csrf",
]
