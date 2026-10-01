"""HTTP boundary tests, distinct from real-provider smoke evidence."""

import json
from types import SimpleNamespace

import httpx
import pytest

from edgar.server import create_app

ORIGIN = "http://127.0.0.1:19371"


class Auth:
    authenticated = True

    def status(self, sid):
        return {
            "authenticated": self.authenticated,
            "inference_enabled": self.authenticated,
            "active_registration": "reg-" + sid,
        }

    async def get_access_token(self, sid):
        if not self.authenticated:
            from edgar.auth import AuthError

            raise AuthError("sign_in_required", "Sign in first")
        return "test-app-specific-oauth"

    async def aclose(self):
        pass

    async def logout(self, sid):
        self.authenticated = False
        return self.status(sid)


async def catalog(token):
    assert token == "test-app-specific-oauth"
    return [{"slug": "eligible-model", "display_name": "Eligible model"}]


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "MUST-NOT-BE-USED")
    calls = []
    auth = Auth()

    async def infer(adapter, token, model, history, question, *, tools, emit):
        calls.append({"token": token, "history": list(history), "question": question})
        await emit({"type": "text_delta", "text": "partial"})
        if question == "fail":
            from edgar.transport import InferenceError

            raise InferenceError("interrupted_stream", "Interrupted stream")
        return SimpleNamespace(
            text="done",
            history=history + [{"role": "user", "content": question}],
            citations=[],
            usage=None,
            telemetry={"latency_ms": 10},
        )

    async def tool(name, args):
        return {"ok": True, "data": {"name": name, "arguments": args}}

    app = create_app(
        runtime_dir=tmp_path, auth=auth, inference=infer, retrieval=tool, model_catalog=catalog
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as client:
        await client.get("/api/edgar/status")
        yield client, app, calls, auth


def chat_payload(**kwargs):
    return {"adapter": "direct", "model": "eligible-model", "message": "hello", **kwargs}


def completed(response):
    records = [part for part in response.text.split("\n\n") if part.startswith("event: completed")]
    assert len(records) == 1
    return json.loads(records[0].split("data: ")[1])


async def test_browser_boundary_and_no_cors(harness):
    client, _, calls, _ = harness
    assert (
        await client.post(
            "/api/edgar/chat", json=chat_payload(), headers={"Origin": "https://attacker.example"}
        )
    ).status_code == 403
    assert (
        await client.get("/api/edgar/status", headers={"Host": "attacker.example"})
    ).status_code == 403
    response = await client.get("/api/edgar/status")
    assert "access-control-allow-origin" not in response.headers
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    assert not calls


async def test_absent_auth_never_uses_environment_key(harness):
    client, _, calls, auth = harness
    auth.authenticated = False
    response = await client.post("/api/edgar/chat", json=chat_payload())
    assert response.status_code == 401
    assert not calls
    assert "MUST-NOT-BE-USED" not in response.text


async def test_cross_browser_history_is_unavailable_and_does_not_lock(harness):
    client, app, calls, _ = harness
    first = completed(await client.post("/api/edgar/chat", json=chat_payload()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as stranger:
        await stranger.get("/api/edgar/status")
        response = await stranger.post(
            "/api/edgar/chat", json=chat_payload(conversation_id=first["conversation_id"])
        )
        assert response.status_code == 404
        completed(await stranger.post("/api/edgar/chat", json=chat_payload()))
    assert len(calls) == 2
    assert calls[1]["history"] == []


async def test_failed_stream_has_no_completed_and_does_not_commit_history(harness):
    client, _, calls, _ = harness
    first = completed(await client.post("/api/edgar/chat", json=chat_payload()))
    failed = await client.post(
        "/api/edgar/chat",
        json=chat_payload(message="fail", conversation_id=first["conversation_id"]),
    )
    assert "event: token" in failed.text
    assert "event: error" in failed.text
    assert "event: completed" not in failed.text
    completed(
        await client.post(
            "/api/edgar/chat",
            json=chat_payload(message="next", conversation_id=first["conversation_id"]),
        )
    )
    assert calls[-1]["history"] == [{"role": "user", "content": "hello"}]


async def test_input_size_and_model_allowlist(harness):
    client, _, calls, _ = harness
    assert (await client.post("/api/edgar/chat", content="x" * 17000)).status_code == 413
    assert (
        await client.post("/api/edgar/chat", json=chat_payload(model="not-eligible"))
    ).status_code == 401
    assert not calls


async def test_cancel_before_producer_starts_terminates_stream_and_releases_lock(
    harness, monkeypatch
):
    import asyncio

    client, _, calls, _ = harness
    original = asyncio.create_task

    def cancel_producer(coro, *args, **kwargs):
        task = original(coro, *args, **kwargs)
        if coro.cr_code.co_name == "produce":
            task.cancel()
        return task

    monkeypatch.setattr(asyncio, "create_task", cancel_producer)
    response = await asyncio.wait_for(client.post("/api/edgar/chat", json=chat_payload()), 1)
    assert "event: error" in response.text
    assert "event: completed" not in response.text
    assert calls == []
    monkeypatch.setattr(asyncio, "create_task", original)
    completed(await client.post("/api/edgar/chat", json=chat_payload()))


async def test_logout_cancels_inference_before_next_request(tmp_path):
    import asyncio

    started = asyncio.Event()
    cancelled = asyncio.Event()
    auth = Auth()

    async def waiting(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    app = create_app(runtime_dir=tmp_path, auth=auth, inference=waiting, model_catalog=catalog)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as client:
        await client.get("/api/edgar/status")
        chat = asyncio.create_task(client.post("/api/edgar/chat", json=chat_payload()))
        await asyncio.wait_for(started.wait(), 1)
        logout = await asyncio.wait_for(client.post("/api/edgar/auth/logout"), 1)
        assert logout.status_code == 200
        await asyncio.wait_for(cancelled.wait(), 1)
        response = await asyncio.wait_for(chat, 1)
        assert "cancelled" in response.text
        assert "event: completed" not in response.text
        assert (await client.post("/api/edgar/chat", json=chat_payload())).status_code == 401


async def test_callback_without_bound_browser_clears_code_url(harness):
    _, app, calls, _ = harness
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as stranger:
        response = await stranger.get("/api/edgar/auth/callback?code=synthetic-code&state=unknown")
        assert response.status_code == 303
        assert response.headers["location"] == "/chat-with-edgar/?auth_error=session_expired"
        assert "synthetic-code" not in response.text
        assert response.headers["referrer-policy"] == "no-referrer"
        assert not calls
