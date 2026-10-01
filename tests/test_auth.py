"""Protocol evidence uses generated test-only signing keys, never real credentials."""

import asyncio
import base64
import hashlib
import json
import secrets
import stat
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from edgar.auth import (
    DISCOVERY_URL,
    ISSUER,
    JWKS_URL,
    RESOURCE,
    REVOKE_URL,
    SCOPES,
    TOKEN_URL,
    AuthError,
    AuthManager,
)

CALLBACK = "http://127.0.0.1:19371/api/edgar/auth/callback"
CLIENT = "oaiapp_unit_test"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Provider:
    def __init__(self, key):
        self.key = key
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        self.jwk.update(kid="test-key", use="sig", alg="RS256")
        self.requests = []
        self.token_reply = None
        self.token_status = 200
        self.revocation_status = 200
        self.token_delay = 0

    def token(self, audience, *, subject="subject-a", overrides=None, key=None):
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "sub": subject,
            "aud": audience,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        claims.update(overrides or {})
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": "test-key"})

    def credentials(
        self,
        nonce=None,
        *,
        subject="subject-a",
        client=CLIENT,
        scope=SCOPES,
        id_overrides=None,
        access_overrides=None,
        refresh="refresh-initial",
    ):
        id_claims = {"nonce": nonce, "email": "same-email@example.test"}
        id_claims.update(id_overrides or {})
        access_claims = {"client_id": client, "scope": scope}
        access_claims.update(access_overrides or {})
        return {
            "id_token": self.token(client, subject=subject, overrides=id_claims),
            "access_token": self.token(RESOURCE, subject=subject, overrides=access_claims),
            "refresh_token": refresh,
            "scope": scope,
            "token_type": "Bearer",
            "expires_in": 3600,
            "earliest_refresh_at": 0,
        }

    async def handler(self, request):
        self.requests.append(request)
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": JWKS_URL,
                    "revocation_endpoint": REVOKE_URL,
                },
            )
        if str(request.url) == JWKS_URL:
            return httpx.Response(200, json={"keys": [self.jwk]})
        if str(request.url) == TOKEN_URL:
            await asyncio.sleep(self.token_delay)
            return httpx.Response(
                self.token_status, json=self.token_reply, headers={"x-request-id": "req_test"}
            )
        if str(request.url) == REVOKE_URL:
            return httpx.Response(self.revocation_status)
        raise AssertionError(f"Unexpected endpoint: {request.url}")


@pytest.fixture
async def auth(tmp_path, signing_key):
    provider = Provider(signing_key)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as client:
        manager = AuthManager(tmp_path / "private", CALLBACK, client)
        try:
            yield manager, provider
        finally:
            await manager.aclose()


def browser():
    return secrets.token_urlsafe(32)


async def sign_in(auth, owner, *, subject="subject-a", client=CLIENT, **credential_args):
    manager, provider = auth
    params = parse_qs(urlsplit(await manager.begin(owner)).query)
    provider.token_reply = provider.credentials(
        params["nonce"][0], subject=subject, client=client, **credential_args
    )
    status = await manager.complete(
        owner,
        {
            "state": params["state"][0],
            "code": "test-code",
            "client_id": client,
        },
    )
    return params, status


def token_requests(provider):
    return [r for r in provider.requests if str(r.url) == TOKEN_URL]


async def test_complete_pkce_storage_and_status_are_browser_isolated(auth):
    manager, provider = auth
    owner = browser()
    params, status = await sign_in(auth, owner)
    assert params["client_id"] == ["dynamic_agent_client"]
    assert params["agent_name_hint"] == ["Chat with EDGAR"]
    assert params["ext_agent_host_id"] == [manager.host_id]
    assert params["redirect_uri"] == [CALLBACK]
    assert params["scope"] == [SCOPES]
    assert params["resource"] == [RESOURCE]
    form = parse_qs(token_requests(provider)[0].content.decode())
    expected = (
        base64.urlsafe_b64encode(
            hashlib.sha256(form["code_verifier"][0].encode()).digest(),
        )
        .decode()
        .rstrip("=")
    )
    assert params["code_challenge"] == [expected]
    assert params["code_challenge_method"] == ["S256"]
    assert form["client_id"] == [CLIENT]
    assert form["redirect_uri"] == [CALLBACK]
    assert "client_secret" not in form
    assert status["authenticated"] and status["inference_enabled"]
    assert status["account"]["subject"] == "subject-a"
    assert not manager.status(browser())["authenticated"]
    safe = json.dumps(status)
    for token in provider.token_reply.values():
        if isinstance(token, str) and len(token) > 150:
            assert token not in safe
    assert "refresh-initial" not in safe
    assert stat.S_IMODE(manager.runtime_dir.stat().st_mode) == 0o700
    saved = manager.runtime_dir / "credentials.json"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert owner not in saved.read_text()
    assert await manager.get_access_token(owner) == provider.token_reply["access_token"]


async def test_state_cannot_be_replayed_or_consumed_by_another_browser(auth):
    manager, provider = auth
    owner = browser()
    query = parse_qs(urlsplit(await manager.begin(owner)).query)
    callback = {"state": query["state"][0], "code": "code", "client_id": CLIENT}
    with pytest.raises(AuthError, match="verified"):
        await manager.complete(browser(), callback)
    assert not token_requests(provider)
    provider.token_reply = provider.credentials(query["nonce"][0])
    await manager.complete(owner, callback)
    with pytest.raises(AuthError) as caught:
        await manager.complete(owner, callback)
    assert caught.value.code == "invalid_state"
    assert len(token_requests(provider)) == 1


async def test_declined_expired_missing_and_superseded_state_do_not_exchange(auth):
    manager, provider = auth
    owner = browser()
    first = parse_qs(urlsplit(await manager.begin(owner)).query)
    second = parse_qs(urlsplit(await manager.begin(owner)).query)
    for params in [{}, {"state": first["state"][0]}]:
        with pytest.raises(AuthError):
            await manager.complete(owner, params)
    with pytest.raises(AuthError) as caught:
        await manager.complete(owner, {"state": second["state"][0], "error": "access_denied"})
    assert caught.value.code == "consent_declined"
    third = parse_qs(urlsplit(await manager.begin(owner)).query)
    manager._pending[third["state"][0]].created_at -= 601
    with pytest.raises(AuthError) as caught:
        await manager.complete(owner, {"state": third["state"][0], "code": "code"})
    assert caught.value.code == "expired_state"
    assert not token_requests(provider)


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"iss": "https://attacker.test"}, "invalid_identity"),
        ({"aud": "oaiapp_other"}, "invalid_identity"),
        ({"exp": int(time.time()) - 60}, "invalid_identity"),
        ({"iat": int(time.time()) + 600}, "invalid_identity"),
        ({"nonce": "different-nonce"}, "invalid_nonce"),
        ({"sub": ""}, "invalid_identity"),
        ({"aud": [CLIENT, "other"], "azp": "other"}, "invalid_identity"),
    ],
)
async def test_id_claim_validation_is_fail_closed(auth, overrides, expected):
    manager, _ = auth
    owner = browser()
    with pytest.raises(AuthError) as caught:
        await sign_in(auth, owner, id_overrides=overrides)
    assert caught.value.code == expected
    assert not manager.status(owner)["authenticated"]


async def test_unsigned_forged_and_foreign_signatures_rejected(auth):
    manager, provider = auth
    for token in [
        jwt.encode({"sub": "attacker"}, key="", algorithm="none"),
        provider.token(CLIENT, key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        "not-a-token",
    ]:
        owner = browser()
        params = parse_qs(urlsplit(await manager.begin(owner)).query)
        provider.token_reply = provider.credentials(params["nonce"][0])
        provider.token_reply["id_token"] = token
        with pytest.raises(AuthError) as caught:
            await manager.complete(
                owner,
                {
                    "state": params["state"][0],
                    "code": "code",
                    "client_id": CLIENT,
                },
            )
        assert caught.value.code == "invalid_identity"
        assert not manager.status(owner)["authenticated"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "oaiapp_other"},
        {"sub": "other-subject"},
        {"aud": "https://attacker.test"},
    ],
)
async def test_access_jwt_must_match_verified_registration(auth, overrides):
    with pytest.raises(AuthError):
        await sign_in(auth, browser(), access_overrides=overrides)


@pytest.mark.parametrize(
    "scope,access_overrides",
    [
        ("openid email profile", {}),
        (SCOPES, {"scope": "openid email profile resource.invoke"}),
        ("openid email chatgpt.tokens.use.direct", {}),
    ],
)
async def test_identity_without_both_granted_permissions_never_returns_inference_token(
    auth,
    scope,
    access_overrides,
):
    manager, provider = auth
    owner = browser()
    _, result = await sign_in(auth, owner, scope=scope, access_overrides=access_overrides)
    assert result["authenticated"] and not result["inference_enabled"]
    with pytest.raises(AuthError) as caught:
        await manager.get_access_token(owner)
    assert caught.value.code == "plan_permission_required"
    assert len(token_requests(provider)) == 1


async def test_identity_only_result_is_retained_without_access_token(auth):
    manager, provider = auth
    owner = browser()
    query = parse_qs(urlsplit(await manager.begin(owner)).query)
    provider.token_reply = {
        "id_token": provider.token(CLIENT, overrides={"nonce": query["nonce"][0]}),
        "scope": "openid profile email",
    }
    status = await manager.complete(
        owner,
        {
            "code": "code",
            "client_id": CLIENT,
            "state": query["state"][0],
        },
    )
    assert status["authenticated"] and not status["inference_enabled"]


async def test_returning_identity_and_callback_client_must_match_and_keep_original(auth):
    manager, provider = auth
    owner = browser()
    _, original = await sign_in(auth, owner)
    registration = original["active_registration"]
    for different_client, different_subject in [
        ("oaiapp_other", "subject-a"),
        (CLIENT, "subject-b"),
    ]:
        query = parse_qs(urlsplit(await manager.begin(owner, registration)).query)
        assert query["client_id"] == [CLIENT]
        assert "agent_name_hint" not in query and "id_token_hint" not in query
        provider.token_reply = provider.credentials(query["nonce"][0], subject=different_subject)
        with pytest.raises(AuthError):
            await manager.complete(
                owner,
                {
                    "state": query["state"][0],
                    "code": "code",
                    "client_id": different_client,
                },
            )
        assert manager.status(owner)["account"]["subject"] == "subject-a"
    query = parse_qs(urlsplit(await manager.begin(owner, registration, reconsent=True)).query)
    assert query["prompt"] == ["consent"]
    provider.token_reply = provider.credentials(query["nonce"][0])
    result = await manager.complete(owner, {"state": query["state"][0], "code": "code"})
    assert result["active_registration"] == registration


async def test_registrations_with_same_email_are_separate_and_browser_scoped(auth):
    manager, _ = auth
    owner, stranger = browser(), browser()
    _, first = await sign_in(auth, owner)
    _, second = await sign_in(auth, owner, subject="subject-b", client="oaiapp_second")
    assert len(second["registrations"]) == 2
    assert len({account["label"] for account in second["registrations"]}) == 2
    selected = await manager.select(owner, first["active_registration"])
    assert selected["account"]["subject"] == "subject-a"
    with pytest.raises(AuthError):
        await manager.select(stranger, first["active_registration"])
    with pytest.raises(AuthError):
        await manager.begin(stranger, first["active_registration"])
    with pytest.raises(AuthError):
        await manager.get_access_token(stranger)


async def test_refresh_is_serialized_rotates_atomically_and_omits_scope(auth):
    manager, provider = auth
    owner = browser()
    _, status = await sign_in(auth, owner)
    record = manager._browser(manager._owner(owner))["registrations"][status["active_registration"]]
    record["credentials"]["expires_at"] = time.time() - 10
    provider.token_reply = provider.credentials(refresh="refresh-rotated")
    provider.token_delay = 0.02
    results = await asyncio.gather(*(manager.get_access_token(owner) for _ in range(5)))
    assert len(set(results)) == 1
    assert len(token_requests(provider)) == 2
    form = parse_qs(token_requests(provider)[-1].content.decode())
    assert form == {
        "grant_type": ["refresh_token"],
        "client_id": [CLIENT],
        "refresh_token": ["refresh-initial"],
        "resource": [RESOURCE],
    }
    saved = json.loads((manager.runtime_dir / "credentials.json").read_text())
    credentials = saved["browsers"][manager._owner(owner)]["registrations"][
        status["active_registration"]
    ]["credentials"]
    assert credentials["refresh_token"] == "refresh-rotated"
    assert credentials["access_token"] == results[0]


@pytest.mark.parametrize(
    "code",
    [
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    ],
)
async def test_terminal_refresh_clears_tokens_but_retains_registration(auth, code):
    manager, provider = auth
    owner = browser()
    _, original = await sign_in(auth, owner)
    provider.token_status = 400
    provider.token_reply = {"error": code, "error_description": "secret must never surface"}
    with pytest.raises(AuthError) as caught:
        await manager.get_access_token(owner, force_refresh=True)
    assert caught.value.code == code
    assert "secret" not in json.dumps(caught.value.public())
    status = manager.status(owner)
    assert not status["authenticated"]
    assert status["active_registration"] == original["active_registration"]
    assert "refresh-initial" not in (manager.runtime_dir / "credentials.json").read_text()
    query = parse_qs(urlsplit(await manager.begin(owner, status["active_registration"])).query)
    assert query["client_id"] == [CLIENT]


@pytest.mark.parametrize(
    "status,body",
    [
        (503, {"detail": "private diagnostic"}),
        (429, {"error": "rate_limit"}),
        (400, {"error": "invalid_client"}),
    ],
)
async def test_temporary_and_configuration_errors_preserve_credentials(auth, status, body):
    manager, provider = auth
    owner = browser()
    await sign_in(auth, owner)
    provider.token_status, provider.token_reply = status, body
    with pytest.raises(AuthError) as caught:
        await manager.get_access_token(owner, force_refresh=True)
    assert caught.value.status == status
    assert caught.value.request_id == "req_test"
    assert manager.status(owner)["authenticated"]
    assert "private diagnostic" not in json.dumps(caught.value.public())


async def test_invalid_grant_registration_can_be_retried_with_issued_client(auth):
    manager, provider = auth
    owner = browser()
    query = parse_qs(urlsplit(await manager.begin(owner)).query)
    provider.token_status, provider.token_reply = 400, {"error": "invalid_grant"}
    with pytest.raises(AuthError):
        await manager.complete(
            owner,
            {
                "state": query["state"][0],
                "code": "code",
                "client_id": CLIENT,
            },
        )
    status = manager.status(owner)
    assert not status["authenticated"]
    registration = status["registrations"][0]
    query = parse_qs(urlsplit(await manager.begin(owner, registration["registration_id"])).query)
    assert query["client_id"] == [CLIENT]
    provider.token_status, provider.token_reply = 200, provider.credentials(query["nonce"][0])
    result = await manager.complete(owner, {"state": query["state"][0], "code": "new-code"})
    assert result["authenticated"]


async def test_logout_revokes_only_selected_registration_and_reuses_client(auth):
    manager, provider = auth
    owner = browser()
    _, original = await sign_in(auth, owner)
    await sign_in(auth, owner, subject="subject-b", client="oaiapp_second")
    await manager.select(owner, original["active_registration"])
    pending = parse_qs(urlsplit(await manager.begin(owner)).query)
    result = await manager.logout(owner)
    assert result["revocation_confirmed"] and not result["authenticated"]
    assert result["registrations"][1]["authenticated"]
    revoke = next(r for r in provider.requests if str(r.url) == REVOKE_URL)
    assert parse_qs(revoke.content.decode()) == {
        "token": ["refresh-initial"],
        "token_type_hint": ["refresh_token"],
        "client_id": [CLIENT],
    }
    with pytest.raises(AuthError):
        await manager.complete(owner, {"state": pending["state"][0], "code": "code"})
    query = parse_qs(urlsplit(await manager.begin(owner, original["active_registration"])).query)
    assert query["client_id"] == [CLIENT]


async def test_logout_reports_unconfirmed_remote_revoke_after_bounded_retry(auth):
    manager, provider = auth
    owner = browser()
    await sign_in(auth, owner)
    provider.revocation_status = 503
    result = await manager.logout(owner)
    assert not result["authenticated"] and not result["revocation_confirmed"]
    assert "not confirmed" in result["message"]
    assert sum(str(r.url) == REVOKE_URL for r in provider.requests) == 2
    with pytest.raises(AuthError):
        await manager.get_access_token(owner)


async def test_runtime_survives_restart_and_excludes_second_owner(auth):
    manager, provider = auth
    owner = browser()
    _, original = await sign_in(auth, owner)
    with pytest.raises(RuntimeError, match="already owns"):
        AuthManager(manager.runtime_dir, CALLBACK, manager.client)
    host_id = manager.host_id
    await manager.aclose()
    reopened = AuthManager(manager.runtime_dir, CALLBACK.replace("19371", "19472"), manager.client)
    try:
        assert reopened.host_id == host_id
        assert reopened.status(owner) == original
        assert await reopened.get_access_token(owner) == provider.token_reply["access_token"]
    finally:
        await reopened.aclose()
    with pytest.raises(ValueError, match="callback path"):
        AuthManager(manager.runtime_dir, "http://127.0.0.1:19371/different", manager.client)


@pytest.mark.parametrize(
    "callback",
    [
        "https://evanoman.com/auth/callback",
        "http://localhost:19371/auth/callback",
        "http://127.0.0.1/auth/callback",
        "http://user@127.0.0.1:19371/auth/callback",
        "http://127.0.0.1:19371/auth/callback?next=evil",
        "http://127.0.0.1:19371/auth/callback#x",
    ],
)
def test_loopback_redirect_is_mandatory(tmp_path, callback):
    with pytest.raises(ValueError):
        AuthManager(tmp_path / "auth", callback)


async def test_untrusted_jwks_or_revocation_locations_are_not_followed(tmp_path):
    requested = []

    async def handler(request):
        requested.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "issuer": ISSUER,
                "jwks_uri": "http://169.254.169.254/secrets",
                "revocation_endpoint": REVOKE_URL,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manager = AuthManager(tmp_path / "auth", CALLBACK, client)
        try:
            with pytest.raises(AuthError) as caught:
                await manager._verify("not-a-token", CLIENT)
            assert caught.value.code == "invalid_discovery"
            assert requested == [DISCOVERY_URL]
        finally:
            await manager.aclose()
