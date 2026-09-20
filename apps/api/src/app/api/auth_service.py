"""Session bridge between browser cookies and the signed-in Timecue account."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from threading import Lock, RLock

from src.app.domain.contracts import SessionUser, SourceMode
from src.app.integrations.fixture import FixtureTimecueAdapter
from src.app.integrations.timecue import TimecueAuthClient, UpstreamIntegrationError
from src.app.integrations.token_vault import TokenVaultError, UpstreamTokens
from src.app.persistence.store import SessionRecord, Store, new_session_id

REFRESH_LEASE_NAME_PREFIX = "upstream-refresh:"
REFRESH_LEASE_SECONDS = 180
REFRESH_LEASE_WAIT_SECONDS = 15.0


class AuthenticationFailure(RuntimeError):
    """A local or upstream authentication check failed."""

    def __init__(self, code: str, status_code: int = 401) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AuthenticatedSession:
    record: SessionRecord
    user: SessionUser


class AuthService:
    """Create opaque local sessions and revalidate live identity on each request."""

    def __init__(
        self, mode: str, store: Store, fixture: FixtureTimecueAdapter, upstream: TimecueAuthClient
    ) -> None:
        self.mode = mode
        self.store = store
        self.fixture = fixture
        self.upstream = upstream
        self._refresh_locks: dict[str, Lock] = {}
        self._refresh_locks_guard = RLock()

    def login(self, email: str, password: str) -> AuthenticatedSession:
        if self.mode == SourceMode.FIXTURE.value:
            if not email.strip() or not password:
                raise AuthenticationFailure("invalid_credentials")
            user = self.fixture.synthetic_user(email.strip())
            access = secrets.token_urlsafe(32)
            refresh = secrets.token_urlsafe(32)
        else:
            try:
                user, access, refresh = self.upstream.login(email.strip(), password)
            except UpstreamIntegrationError as exc:
                status_code = (
                    401
                    if exc.status_code in (401, 403)
                    else 422
                    if exc.status_code == 422
                    else 502
                )
                raise AuthenticationFailure(
                    str(exc), status_code
                ) from exc
            self._require_verified(user)
        record = SessionRecord(
            session_id=new_session_id(),
            user_id=user.id,
            email=user.email,
            verified=user.email_verified_at is not None,
            organization_ids=user.organization_ids,
            permissions=user.organization_permissions,
            source_mode=user.source_mode.value,
            csrf_token=secrets.token_urlsafe(32),
        )
        if self.mode == SourceMode.LIVE:
            self._store_upstream_tokens(record, access, refresh)
        try:
            self.store.create_session(record)
        except Exception:
            if self.mode == SourceMode.LIVE:
                self.upstream.vault.clear(record.session_id)
            raise
        return AuthenticatedSession(record, user)

    def refresh(
        self, record: SessionRecord, failed_access: str | None = None
    ) -> AuthenticatedSession:
        """Rotate one session, coalescing concurrent refreshes for that session.

        ``failed_access`` is supplied when an upstream request returned 401. If
        another request already rotated that exact cookie while this request
        waited for the lock, revalidate with the rotated cookie instead of
        rotating the refresh session a second time.
        """

        if self.mode == SourceMode.FIXTURE.value:
            return AuthenticatedSession(record, self._user_from_record(record))
        with self._refresh_lock(record.session_id):
            before = self._safe_tokens(record.session_id)
            if self._token_rotated(before, failed_access):
                try:
                    return self._current_live(record)
                except UpstreamIntegrationError as exc:
                    if exc.status_code != 401:
                        raise AuthenticationFailure("upstream_identity_unavailable", 502) from exc

            lease_token = self._acquire_refresh_lease(record, before, failed_access)
            if lease_token is None:
                # A different API/worker process completed the refresh while we
                # waited. Revalidate once with the rotated cookie; never rotate
                # the refresh cookie twice for the same failed access token.
                try:
                    return self._current_live(record)
                except UpstreamIntegrationError as exc:
                    if exc.status_code == 401:
                        raise AuthenticationFailure("upstream_refresh_unavailable", 503) from exc
                    raise AuthenticationFailure("upstream_identity_unavailable", 502) from exc

            try:
                latest = self._safe_tokens(record.session_id)
                if self._token_rotated(latest, failed_access, before=before):
                    return self._current_live(record)
                return self._refresh_unlocked(record)
            finally:
                self.store.release_lease(
                    self._lease_organization(record),
                    self._lease_name(record),
                    lease_token,
                )

    def _refresh_unlocked(self, record: SessionRecord) -> AuthenticatedSession:
        try:
            access, refresh = self.upstream.refresh(record)
            self._store_upstream_tokens(record, access, refresh)
            updated = self.store.get_session(record.session_id)
            if updated is None:
                raise AuthenticationFailure("session_not_found")
            user = self.upstream.me(updated)
            self._require_verified(user)
            updated = (
                self.store.update_session_identity(
                    updated.session_id,
                    user.id,
                    user.email,
                    user.email_verified_at is not None,
                    user.organization_ids,
                    user.organization_permissions,
                )
                or updated
            )
            return AuthenticatedSession(updated, user)
        except (UpstreamIntegrationError, AuthenticationFailure, TokenVaultError) as exc:
            self.upstream.vault.clear(record.session_id)
            self.store.delete_session(record.session_id)
            if isinstance(exc, AuthenticationFailure):
                raise
            if isinstance(exc, TokenVaultError):
                raise AuthenticationFailure("session_storage_unavailable", 503) from exc
            raise AuthenticationFailure("refresh_failed") from exc

    def current(self, record: SessionRecord) -> AuthenticatedSession:
        if self.mode == SourceMode.FIXTURE.value:
            if not record.verified:
                raise AuthenticationFailure("email_not_verified")
            return AuthenticatedSession(record, self._user_from_record(record))
        tokens = self._safe_tokens(record.session_id)
        failed_access = tokens.access if tokens is not None else None
        try:
            return self._current_live(record)
        except UpstreamIntegrationError as exc:
            if exc.status_code != 401:
                raise AuthenticationFailure("upstream_identity_unavailable", 502) from exc
            try:
                return self.refresh(record, failed_access=failed_access)
            except AuthenticationFailure:
                self.upstream.vault.clear(record.session_id)
                self.store.delete_session(record.session_id)
                raise

    def _current_live(self, record: SessionRecord) -> AuthenticatedSession:
        """Revalidate upstream identity and persist only non-secret identity data."""

        user = self.upstream.me(record)
        self._require_verified(user)
        updated = self.store.update_session_identity(
            record.session_id,
            user.id,
            user.email,
            True,
            user.organization_ids,
            user.organization_permissions,
        )
        return AuthenticatedSession(updated or record, user)

    def logout(self, record: SessionRecord) -> None:
        try:
            if self.mode == SourceMode.LIVE:
                self.upstream.logout(record)
        finally:
            self.upstream.vault.clear(record.session_id)
            self.store.delete_session(record.session_id)
            with self._refresh_locks_guard:
                self._refresh_locks.pop(record.session_id, None)

    def refresh_for_upstream(
        self, record: SessionRecord, failed_access: str | None = None
    ) -> None:
        try:
            self.refresh(record, failed_access=failed_access)
        except AuthenticationFailure as exc:
            raise UpstreamIntegrationError("upstream_refresh_failed", exc.status_code) from exc

    def _refresh_lock(self, session_id: str) -> Lock:
        with self._refresh_locks_guard:
            lock = self._refresh_locks.get(session_id)
            if lock is None:
                lock = Lock()
                self._refresh_locks[session_id] = lock
            return lock

    def _safe_tokens(self, session_id: str) -> UpstreamTokens | None:
        """Read the vault without turning ciphertext failures into auth success."""

        try:
            return self.upstream.vault.get(session_id)
        except TokenVaultError as exc:
            raise AuthenticationFailure("session_storage_unavailable", 503) from exc

    def _store_upstream_tokens(self, record: SessionRecord, access: str, refresh: str) -> None:
        """Persist rotated cookies with the non-secret identity binding."""

        organization_id = record.organization_ids[0] if record.organization_ids else None
        self.upstream.vault.put(
            record.session_id,
            access,
            refresh,
            user_id=record.user_id,
            organization_id=organization_id,
        )

    def _acquire_refresh_lease(
        self,
        record: SessionRecord,
        before: UpstreamTokens | None,
        failed_access: str | None,
    ) -> str | None:
        """Acquire a portable short lease, observing another process's rotation."""

        deadline = time.monotonic() + REFRESH_LEASE_WAIT_SECONDS
        organization_id = self._lease_organization(record)
        name = self._lease_name(record)
        while True:
            try:
                lease_token = self.store.acquire_lease(
                    organization_id,
                    name,
                    seconds=REFRESH_LEASE_SECONDS,
                )
            except Exception as exc:
                raise AuthenticationFailure("refresh_lock_unavailable", 503) from exc
            if lease_token is not None:
                return lease_token

            latest = self._safe_tokens(record.session_id)
            if self._token_rotated(latest, failed_access, before=before):
                return None
            if time.monotonic() >= deadline:
                raise AuthenticationFailure("refresh_lock_timeout", 503)
            time.sleep(0.05)

    @staticmethod
    def _token_rotated(
        current: UpstreamTokens | None,
        failed_access: str | None,
        *,
        before: UpstreamTokens | None = None,
    ) -> bool:
        """Return whether another request has already installed newer cookies."""

        if current is None:
            return False
        if failed_access is not None:
            return current.access != failed_access
        return before is not None and current.refresh_generation > before.refresh_generation

    @staticmethod
    def _lease_organization(record: SessionRecord) -> str:
        """Choose a stable tenant key for a session-scoped refresh lease."""

        return record.organization_ids[0] if record.organization_ids else f"user:{record.user_id}"

    @staticmethod
    def _lease_name(record: SessionRecord) -> str:
        """Build a bounded lease name that contains no credential material."""

        return f"{REFRESH_LEASE_NAME_PREFIX}{record.session_id}"

    @staticmethod
    def _require_verified(user: SessionUser) -> None:
        if user.email_verified_at is None:
            raise AuthenticationFailure("email_not_verified", 403)

    @staticmethod
    def _user_from_record(record: SessionRecord) -> SessionUser:
        return SessionUser(
            id=record.user_id,
            email=record.email,
            emailVerifiedAt="2000-01-01T00:00:00Z" if record.verified else None,
            organizationIds=record.organization_ids,
            organizationPermissions=record.permissions,
            appPermissions=[],
            appAdmin=False,
            sourceMode=record.source_mode,
            synthetic=record.source_mode == SourceMode.FIXTURE.value,
        )


__all__ = ["AuthenticatedSession", "AuthenticationFailure", "AuthService"]
