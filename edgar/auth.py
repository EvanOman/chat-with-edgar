"""App-specific, loopback-only Sign in with ChatGPT credentials.

No API key or coding-harness credential is read by this module. The HTTP layer must
supply an unguessable HttpOnly cookie identifier and enforce origin/host checks.
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
REVOKE_URL = f"{ISSUER}/api/accounts/oauth/revoke"
RESOURCE = "https://api.openai.com/v1"
USAGE_URL = "https://chatgpt.com/settings/usage"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
INFERENCE_SCOPE = "chatgpt.tokens.use.direct"
REQUIRED_INFERENCE_SCOPES = {INFERENCE_SCOPE, "resource.invoke"}
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
}
CLIENT_ID = re.compile(r"oaiapp_[A-Za-z0-9_-]{1,240}\Z")
BROWSER_ID = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")


class AuthError(Exception):
    """A safe, explicitly authored error suitable for a browser response."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 401,
        *,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.request_id = request_id

    def public(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "retryable": self.retryable,
            "request_id": self.request_id,
        }


@dataclass(repr=False)
class _Transaction:
    owner: str
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    created_at: float
    registration_id: str | None = None
    client_id: str | None = None
    subject: str | None = None


@dataclass(repr=False)
class AuthManager:
    """One process owns a runtime; browser-owned registrations survive restarts.

    All returned dictionaries are safe metadata. Only get_access_token returns a
    credential, and its caller must keep that value strictly server-side.
    """

    runtime_dir: Path
    redirect_uri: str
    client: httpx.AsyncClient | None = field(default=None, repr=False)
    app_name: str = "Chat with EDGAR"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.redirect_uri)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
            or parsed.path == "/"
        ):
            raise ValueError("SIWC requires an exact HTTP 127.0.0.1 loopback callback with a port.")
        self.runtime_dir = Path(self.runtime_dir)
        if self.runtime_dir.is_symlink():
            raise ValueError("Credential storage must not be a symlink.")
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.runtime_dir.stat().st_uid != os.getuid():
            raise ValueError("Credential storage must be owned by the current user.")
        self.runtime_dir.chmod(0o700)
        self._lock_fd = os.open(
            self.runtime_dir / "owner.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(self._lock_fd, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock_fd)
            raise RuntimeError(
                "Another app process already owns this credential runtime."
            ) from None
        self._owned_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)
        self._pending: dict[str, _Transaction] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._jwks: dict[str, Any] | None = None
        self._jwks_loaded = 0.0
        self._discovery: dict[str, Any] | None = None
        self._closed = False
        try:
            self._data = self._load()
            if self._data.get("callback_path") != parsed.path:
                raise ValueError(
                    "This host's registered callback path cannot change; only port may."
                )
            self.host_id = self._data["host_id"]
        except BaseException:
            os.close(self._lock_fd)
            raise

    def _load(self) -> dict[str, Any]:
        path = self.runtime_dir / "credentials.json"
        if path.exists() or path.is_symlink():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r") as stream:
                info = os.fstat(stream.fileno())
                if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
                    raise ValueError("Credential file must be a regular, owned file.")
                os.fchmod(stream.fileno(), 0o600)
                data = json.load(stream)
            if (
                data.get("version") != 1
                or not isinstance(data.get("browsers"), dict)
                or not isinstance(data.get("host_id"), str)
                or not data["host_id"].startswith("urn:uuid:")
            ):
                raise ValueError("Credential runtime format is invalid.")
            uuid.UUID(data["host_id"][9:])
            return data
        self._data = {
            "version": 1,
            "host_id": f"urn:uuid:{uuid.uuid4()}",
            "callback_path": urlsplit(self.redirect_uri).path,
            "browsers": {},
        }
        self._save()
        return self._data

    def _save(self) -> None:
        name = self.runtime_dir / f".credentials-{secrets.token_hex(12)}.tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self._data, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.runtime_dir / "credentials.json")
            directory = os.open(self.runtime_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            name.unlink(missing_ok=True)

    def _owner(self, browser_id: str) -> str:
        if self._closed:
            raise RuntimeError("Auth manager is closed.")
        if not isinstance(browser_id, str) or not BROWSER_ID.fullmatch(browser_id):
            raise AuthError("invalid_session", "Start a new browser session.")
        return hashlib.sha256(browser_id.encode()).hexdigest()

    def _lock(self, owner: str) -> asyncio.Lock:
        return self._locks.setdefault(owner, asyncio.Lock())

    def _browser(self, owner: str) -> dict[str, Any]:
        return self._data["browsers"].setdefault(owner, {"active": None, "registrations": {}})

    @staticmethod
    def _safe_account(registration_id: str, record: dict[str, Any]) -> dict[str, Any]:
        credentials = record.get("credentials") or {}
        return {
            "registration_id": registration_id,
            "subject": record.get("subject"),
            "email": record.get("email"),
            "client_id": record["client_id"],
            "label": f"{record.get('email') or 'ChatGPT account'} · {registration_id[:8]}",
            "authenticated": bool(credentials.get("id_token")),
            "inference_enabled": bool(credentials.get("inference_enabled")),
        }

    def status(self, browser_id: str) -> dict[str, Any]:
        browser = self._browser(self._owner(browser_id))
        active = browser["active"]
        account = None
        if active in browser["registrations"]:
            account = self._safe_account(active, browser["registrations"][active])
        return {
            "authenticated": bool(account and account["authenticated"]),
            "inference_enabled": bool(account and account["inference_enabled"]),
            "active_registration": active,
            "account": account,
            "registrations": [
                self._safe_account(key, record) for key, record in browser["registrations"].items()
            ],
            "usage_url": USAGE_URL,
        }

    async def select(self, browser_id: str, registration_id: str) -> dict[str, Any]:
        owner = self._owner(browser_id)
        async with self._lock(owner):
            browser = self._browser(owner)
            record = browser["registrations"].get(registration_id)
            if not record or not record.get("subject"):
                raise AuthError(
                    "unknown_registration", "That account is not saved in this browser."
                )
            browser["active"] = registration_id
            self._save()
            return self.status(browser_id)

    async def begin(
        self,
        browser_id: str,
        registration_id: str | None = None,
        reconsent: bool = False,
    ) -> str:
        owner = self._owner(browser_id)
        async with self._lock(owner):
            browser = self._browser(owner)
            record = None
            if registration_id:
                record = browser["registrations"].get(registration_id)
                if record is None:
                    raise AuthError("unknown_registration", "That account is not saved here.")
            now = time.time()
            self._pending = {
                key: value
                for key, value in self._pending.items()
                if value.created_at + 600 > now and value.owner != owner
            }
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            client_id = record["client_id"] if record else None
            self._pending[state] = _Transaction(
                owner,
                state,
                nonce,
                verifier,
                self.redirect_uri,
                now,
                registration_id,
                client_id,
                record.get("subject") if record else None,
            )
            params = {
                "client_id": client_id or "dynamic_agent_client",
                "response_type": "code",
                "ext_agent_host_id": self.host_id,
                "redirect_uri": self.redirect_uri,
                "scope": SCOPES,
                "resource": RESOURCE,
                "state": state,
                "nonce": nonce,
                "code_challenge_method": "S256",
                "code_challenge": challenge.decode().rstrip("="),
            }
            if not record:
                params["agent_name_hint"] = self.app_name
            elif record.get("email"):
                params["login_hint"] = record["email"]
            # Omit the optional ID-token URL hint: account selection is safer than
            # placing even the documented hint in browser history or diagnostics.
            if reconsent:
                params["prompt"] = "consent"
            return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def complete(self, browser_id: str, params: Mapping[str, str]) -> dict[str, Any]:
        owner = self._owner(browser_id)
        async with self._lock(owner):
            state = params.get("state", "")
            pending = self._pending.get(state)
            if pending is None or not secrets.compare_digest(pending.owner, owner):
                raise AuthError("invalid_state", "Sign-in could not be verified. Start again.")
            self._pending.pop(state)
            if pending.created_at + 600 <= time.time():
                raise AuthError("expired_state", "Sign-in expired. Start again.")
            if params.get("error"):
                raise AuthError("consent_declined", "Sign-in was not authorized. No inference ran.")
            code = params.get("code")
            client_id = params.get("client_id") or pending.client_id
            if not code or not client_id or not CLIENT_ID.fullmatch(client_id):
                raise AuthError(
                    "incomplete_registration", "Sign-in returned an incomplete registration."
                )
            if pending.client_id and client_id != pending.client_id:
                raise AuthError("client_mismatch", "Sign-in returned a different app registration.")
            browser = self._browser(owner)
            registration_id = pending.registration_id or str(uuid.uuid4())
            try:
                response = await self._token_request(
                    {
                        "grant_type": "authorization_code",
                        "client_id": client_id,
                        "code": code,
                        "code_verifier": pending.verifier,
                        "redirect_uri": pending.redirect_uri,
                        "resource": RESOURCE,
                    }
                )
            except AuthError as error:
                if error.code == "invalid_grant" and not pending.registration_id:
                    browser["registrations"][registration_id] = {
                        "client_id": client_id,
                        "subject": None,
                        "email": None,
                        "credentials": {},
                    }
                    self._save()
                raise
            record = await self._validated_record(
                response,
                client_id,
                pending.subject,
                nonce=pending.nonce,
            )
            browser["registrations"][registration_id] = record
            browser["active"] = registration_id
            self._save()
            return self.status(browser_id)

    async def _http(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        assert self.client is not None
        try:
            return await self.client.request(method, url, follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise AuthError(
                "auth_unavailable",
                "ChatGPT sign-in is temporarily unreachable. Try again later.",
                503,
                retryable=True,
            ) from None

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            if len(response.content) > 1_000_000:
                raise ValueError
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError):
            raise AuthError(
                "invalid_auth_response", "ChatGPT returned an invalid auth response.", 502
            )

    async def _token_request(self, body: dict[str, str]) -> dict[str, Any]:
        response = await self._http(
            "POST", TOKEN_URL, data=body, headers={"accept": "application/json"}
        )
        if response.status_code == 200:
            return self._json(response)
        request_id = response.headers.get("x-request-id")
        if request_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", request_id):
            request_id = None
        code = "auth_rejected"
        try:
            error = self._json(response).get("error")
            candidate = error.get("code") if isinstance(error, dict) else error
            if candidate in TERMINAL_REFRESH_ERRORS | {"invalid_client", "invalid_grant"}:
                code = candidate
        except AuthError:
            pass
        retryable = response.status_code >= 500 or response.status_code == 429
        message = "ChatGPT could not renew sign-in. Sign in again."
        if retryable:
            message = "ChatGPT sign-in is temporarily unavailable. Try again later."
        elif code == "invalid_client":
            message = "The app registration was rejected. Its client configuration needs attention."
        raise AuthError(
            code, message, response.status_code, retryable=retryable, request_id=request_id
        )

    async def _configuration(self) -> dict[str, Any]:
        if self._discovery is None:
            response = await self._http("GET", DISCOVERY_URL)
            if response.status_code != 200:
                raise AuthError(
                    "auth_unavailable", "Could not verify OpenAI's auth configuration.", 503
                )
            data = self._json(response)
            if (
                data.get("issuer") != ISSUER
                or data.get("jwks_uri") != JWKS_URL
                or data.get("revocation_endpoint") != REVOKE_URL
            ):
                raise AuthError(
                    "invalid_discovery", "OpenAI auth configuration changed. Stop sign-in.", 502
                )
            self._discovery = data
        return self._discovery

    async def _verify(self, token: str, audience: str) -> dict[str, Any]:
        await self._configuration()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise jwt.InvalidTokenError
            # Refresh once on an unfamiliar key; never follow a token's jku/x5u URL.
            for attempt in range(2):
                if self._jwks is None or time.time() - self._jwks_loaded > 3600 or attempt:
                    response = await self._http("GET", JWKS_URL)
                    if response.status_code != 200:
                        raise AuthError(
                            "auth_unavailable", "Could not verify OpenAI signing keys.", 503
                        )
                    self._jwks = self._json(response)
                    self._jwks_loaded = time.time()
                keys = self._jwks.get("keys", [])
                key = next((item for item in keys if item.get("kid") == header["kid"]), None)
                if key:
                    break
            if not key or key.get("kty") != "RSA" or key.get("use", "sig") != "sig":
                raise jwt.InvalidTokenError
            claims = jwt.decode(
                token,
                jwt.PyJWK.from_dict(key).key,
                algorithms=["RS256"],
                audience=audience,
                issuer=ISSUER,
                leeway=5,
                options={"require": ["sub", "exp", "iat", "iss", "aud"]},
            )
            if not isinstance(claims.get("sub"), str) or not claims["sub"]:
                raise jwt.InvalidTokenError
            if isinstance(claims.get("aud"), list) and len(claims["aud"]) > 1:
                if claims.get("azp") != audience:
                    raise jwt.InvalidTokenError
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
            raise AuthError(
                "invalid_identity", "ChatGPT returned credentials that could not be verified."
            ) from None

    async def _validated_record(
        self,
        response: dict[str, Any],
        client_id: str,
        expected_subject: str | None,
        *,
        nonce: str | None,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous_credentials = (previous or {}).get("credentials", {})
        id_token = response.get("id_token")
        if not id_token and previous:
            identity = {"sub": previous["subject"], "email": previous.get("email")}
            id_token = previous_credentials.get("id_token")
        else:
            if not isinstance(id_token, str):
                raise AuthError("invalid_identity", "Sign-in returned no verifiable identity.")
            identity = await self._verify(id_token, client_id)
            if nonce is not None and identity.get("nonce") != nonce:
                raise AuthError("invalid_nonce", "Sign-in could not be verified. Start again.")
        subject = identity["sub"]
        if expected_subject is not None and subject != expected_subject:
            raise AuthError("identity_mismatch", "Sign-in returned a different ChatGPT account.")
        raw_scope = response.get("scope")
        if raw_scope is None and previous:
            scopes = previous_credentials.get("scopes", [])
        elif isinstance(raw_scope, str):
            scopes = sorted(set(raw_scope.split()))
        else:
            raise AuthError("missing_scopes", "Sign-in did not confirm its granted permissions.")
        inference_enabled = REQUIRED_INFERENCE_SCOPES.issubset(scopes)
        access_token = response.get("access_token")
        access_expiry = None
        if access_token:
            if (
                not isinstance(access_token, str)
                or response.get("token_type", "").lower() != "bearer"
            ):
                raise AuthError("invalid_token", "Sign-in returned an unsupported credential type.")
            access = await self._verify(access_token, RESOURCE)
            if access.get("client_id") != client_id or access["sub"] != subject:
                raise AuthError(
                    "token_mismatch", "The access credential belongs to another registration."
                )
            if not isinstance(access.get("scope"), str):
                raise AuthError("missing_scopes", "The signed credential has no permission grant.")
            inference_enabled = inference_enabled and REQUIRED_INFERENCE_SCOPES.issubset(
                access["scope"].split(),
            )
            access_expiry = float(access["exp"])
        elif inference_enabled:
            raise AuthError(
                "missing_access_token", "Sign-in granted permission but returned no credential."
            )
        refresh_token = response.get("refresh_token")
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise AuthError("invalid_token", "Sign-in returned an invalid renewal credential.")
        if previous and not refresh_token:
            raise AuthError("invalid_token", "Renewal returned no replacement refresh credential.")
        now = time.time()
        expires_in = response.get("expires_in", 0)
        if not isinstance(expires_in, (int, float)) or expires_in < 0:
            raise AuthError("invalid_token", "Sign-in returned an invalid credential lifetime.")
        expires_at = min(now + expires_in, access_expiry) if access_expiry else now
        earliest = response.get("earliest_refresh_at", 0)
        if not isinstance(earliest, (int, float)):
            raise AuthError("invalid_token", "Sign-in returned an invalid renewal time.")
        return {
            "client_id": client_id,
            "subject": subject,
            "issuer": ISSUER,
            "email": identity.get("email") if isinstance(identity.get("email"), str) else None,
            "credentials": {
                "id_token": id_token,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "scopes": scopes,
                "inference_enabled": inference_enabled,
                "expires_at": expires_at,
                "earliest_refresh_at": earliest,
                "saved_at": now,
            },
        }

    async def get_access_token(self, browser_id: str, force_refresh: bool = False) -> str:
        owner = self._owner(browser_id)
        async with self._lock(owner):
            browser = self._browser(owner)
            active = browser["active"]
            record = browser["registrations"].get(active)
            credentials = record.get("credentials", {}) if record else {}
            if not credentials.get("id_token"):
                raise AuthError(
                    "sign_in_required", "Continue with ChatGPT before asking a question."
                )
            if not credentials.get("inference_enabled"):
                raise AuthError(
                    "plan_permission_required", "Enable ChatGPT plan usage to ask a question.", 403
                )
            if force_refresh or credentials["expires_at"] <= time.time() + 60:
                if not credentials.get("refresh_token"):
                    raise AuthError(
                        "sign_in_required", "Sign in again to renew ChatGPT plan access."
                    )
                if credentials.get("earliest_refresh_at", 0) > time.time():
                    if credentials["expires_at"] > time.time() + 5 and not force_refresh:
                        return credentials["access_token"]
                    raise AuthError(
                        "refresh_not_ready", "ChatGPT credentials cannot be renewed yet.", 503
                    )
                try:
                    response = await self._token_request(
                        {
                            "grant_type": "refresh_token",
                            "client_id": record["client_id"],
                            "refresh_token": credentials["refresh_token"],
                            "resource": RESOURCE,
                        }
                    )
                    replacement = await self._validated_record(
                        response,
                        record["client_id"],
                        record["subject"],
                        nonce=None,
                        previous=record,
                    )
                except AuthError as error:
                    if error.code in TERMINAL_REFRESH_ERRORS:
                        record["credentials"] = {}
                        self._save()
                    raise
                browser["registrations"][active] = replacement
                self._save()
                credentials = replacement["credentials"]
                if not credentials.get("inference_enabled"):
                    raise AuthError(
                        "plan_permission_required",
                        "ChatGPT plan permission is no longer granted.",
                        403,
                    )
            return credentials["access_token"]

    async def logout(self, browser_id: str) -> dict[str, Any]:
        owner = self._owner(browser_id)
        async with self._lock(owner):
            browser = self._browser(owner)
            record = browser["registrations"].get(browser["active"])
            credentials = record.get("credentials", {}) if record else {}
            refresh_token = credentials.get("refresh_token")
            confirmed = False
            # Stop locally before awaiting revocation, including any pending callback.
            self._pending = {key: tx for key, tx in self._pending.items() if tx.owner != owner}
            if record:
                record["credentials"] = {}
                self._save()
            if refresh_token:
                try:
                    config = await self._configuration()
                    for attempt in range(2):
                        try:
                            response = await self._http(
                                "POST",
                                config["revocation_endpoint"],
                                data={
                                    "token": refresh_token,
                                    "token_type_hint": "refresh_token",
                                    "client_id": record["client_id"],
                                },
                            )
                            confirmed = response.status_code == 200
                            if confirmed or response.status_code < 500:
                                break
                        except AuthError:
                            pass
                        if attempt == 0:
                            await asyncio.sleep(0.2)
                except AuthError:
                    pass
            result = self.status(browser_id)
            result["revocation_confirmed"] = confirmed
            result["message"] = (
                "Signed out. The renewable ChatGPT session was revoked."
                if confirmed
                else "Signed out locally. Remote revocation was not confirmed; "
                "disconnect the app in ChatGPT Settings."
            )
            return result

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending.clear()
        if self._owned_client and self.client:
            await self.client.aclose()
        os.close(self._lock_fd)
