"""Loopback-only application host. No shared/operator inference credentials exist here."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
COOKIE = "edgar_session"
ADAPTERS = [
    {"id": "direct", "label": "Direct Responses", "available": True},
    {"id": "pydantic-ai", "label": "Pydantic AI", "available": True},
    {"id": "openai-agents", "label": "OpenAI Agents SDK", "available": True},
]


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: str
    model: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=6000)
    conversation_id: str | None = Field(default=None, max_length=80)


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=80)
    arguments: dict[str, Any]


def safe_error(error: Exception) -> dict[str, Any]:
    from edgar.auth import AuthError
    from edgar.transport import InferenceError

    if isinstance(error, (AuthError, InferenceError)):
        return {"code": error.code, "message": error.message}
    if isinstance(error, TimeoutError):
        return {"code": "request_timeout", "message": "The request timed out. Start a new turn."}
    return {"code": "request_failed", "message": "The request failed. No answer was completed."}


def create_app(
    *,
    port: int = 19371,
    runtime_dir: Path | None = None,
    auth: Any = None,
    inference: Any = None,
    retrieval: Any = None,
    model_catalog: Any = None,
) -> FastAPI:
    from edgar.adapters import run
    from edgar.auth import AuthManager
    from edgar.retrieval import SECClient, dispatch
    from edgar.transport import list_models

    origin = f"http://127.0.0.1:{port}"
    runtime = runtime_dir or ROOT / ".runtime"
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.chmod(0o700)
    key_path = runtime / "session.key"
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if key_path.is_symlink() or key_path.stat().st_mode & 0o077:
            raise RuntimeError("Session key must be a private regular file")
    else:
        with os.fdopen(fd, "wb") as file:
            file.write(secrets.token_bytes(32))
    key = key_path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("Invalid session key")
    manager = auth or AuthManager(runtime / "auth", f"{origin}/api/edgar/auth/callback")
    runner = inference or run
    sec = SECClient() if retrieval is None else None

    async def real_retrieval(name, arguments):
        return await dispatch(name, arguments, client=sec)

    tool_dispatch = retrieval or real_retrieval
    get_models = model_catalog or list_models
    conversations: dict[str, dict[str, Any]] = {}
    session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    catalogs: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}
    active_tasks: dict[str, asyncio.Task] = {}
    epochs: dict[str, int] = defaultdict(int)

    def stop_session(sid: str):
        epochs[sid] += 1
        if task := active_tasks.get(sid):
            task.cancel()
        purge_session(sid)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await manager.aclose()
        if sec is not None:
            await sec.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def sign(value: str) -> str:
        return base64.urlsafe_b64encode(hmac.digest(key, value.encode(), hashlib.sha256)).decode()

    def read_session(request: Request) -> str | None:
        raw = request.cookies.get(COOKIE, "")
        try:
            sid, timestamp, signature = raw.split(".")
            if len(sid) != 43 or not 0 <= time.time() - int(timestamp) < 86400:
                return None
            if hmac.compare_digest(signature, sign(f"{sid}.{timestamp}")):
                return sid
        except (ValueError, TypeError):
            pass
        return None

    def require_session(request: Request) -> str:
        session = read_session(request)
        if not session:
            raise ValueError("session_required")
        return session

    def purge_session(session: str) -> None:
        for cid in [c for c, v in conversations.items() if v["session"] == session]:
            conversations.pop(cid, None)
        for entry in [k for k in catalogs if k[0] == session]:
            catalogs.pop(entry, None)

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        # Exact Host closes DNS rebinding; only this explicit loopback origin may mutate.
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return JSONResponse({"error": {"code": "invalid_host"}}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get("origin") != origin:
                return JSONResponse({"error": {"code": "invalid_origin"}}, status_code=403)
            total = bytearray()
            async for chunk in request.stream():
                total.extend(chunk)
                if len(total) > 16384:
                    return JSONResponse({"error": {"code": "request_too_large"}}, status_code=413)
            request._body = bytes(total)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid_request(_request: Request, _error: ValueError):
        return JSONResponse(
            {"error": {"code": "invalid_request", "message": "Reload the app and try again."}},
            status_code=400,
        )

    @app.get("/api/edgar/status")
    async def status(request: Request):
        sid = read_session(request) or secrets.token_urlsafe(32)
        result = {"deployment": "local", "auth_available": True, "adapters": ADAPTERS}
        result.update(manager.status(sid))
        response = JSONResponse(result)
        stamp = str(int(time.time()))
        value = f"{sid}.{stamp}"
        response.set_cookie(
            COOKIE, f"{value}.{sign(value)}", httponly=True, samesite="lax", max_age=86400
        )
        return response

    @app.post("/api/edgar/auth/start")
    async def auth_start(request: Request):
        sid = require_session(request)
        args = await request.json()
        async with session_locks[sid]:
            try:
                url = await manager.begin(
                    sid, args.get("registration_id"), args.get("reconsent", False)
                )
                return {"url": url}
            except Exception as error:
                return JSONResponse({"error": safe_error(error)}, status_code=400)

    @app.get("/api/edgar/auth/callback")
    async def callback(request: Request):
        sid = read_session(request)
        if not sid:
            return RedirectResponse("/chat-with-edgar/?auth_error=session_expired", status_code=303)
        async with session_locks[sid]:
            try:
                await manager.complete(sid, dict(request.query_params))
                purge_session(sid)
            except Exception as error:
                # Clear code/state immediately. Only a safe error code survives the redirect.
                code = safe_error(error)["code"]
                if not isinstance(code, str) or not code.replace("_", "").isalnum():
                    code = "auth_failed"
                return RedirectResponse(f"/chat-with-edgar/?auth_error={code}", status_code=303)
        return RedirectResponse("/chat-with-edgar/", status_code=303)

    @app.post("/api/edgar/auth/select")
    async def select(request: Request):
        sid = require_session(request)
        stop_session(sid)
        async with session_locks[sid]:
            try:
                args = await request.json()
                result = await manager.select(sid, args["registration_id"])
                purge_session(sid)
                return result
            except Exception as error:
                return JSONResponse({"error": safe_error(error)}, status_code=400)

    @app.post("/api/edgar/auth/logout")
    async def logout(request: Request):
        sid = require_session(request)
        stop_session(sid)
        result = await manager.logout(sid)
        purge_session(sid)
        return result

    async def eligible_models(sid: str, token: str) -> list[dict[str, str]]:
        reg = manager.status(sid).get("active_registration", "")
        cache_key = (sid, reg)
        cached = catalogs.get(cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        models = await get_models(token)
        catalogs[cache_key] = (time.monotonic(), models)
        return models

    @app.get("/api/edgar/models")
    async def models(request: Request):
        sid = require_session(request)
        try:
            token = await manager.get_access_token(sid)
            found = await eligible_models(sid, token)
            return {"models": [{"id": m["slug"], "label": m["display_name"]} for m in found]}
        except Exception as error:
            return JSONResponse({"error": safe_error(error)}, status_code=401)

    @app.post("/api/edgar/tools")
    async def tools(body: ToolInput):
        async with asyncio.timeout(45):
            return await tool_dispatch(body.name, body.arguments)

    @app.post("/api/edgar/chat")
    async def chat(request: Request, body: ChatInput):
        sid = require_session(request)
        if body.adapter not in {a["id"] for a in ADAPTERS}:
            return JSONResponse({"error": {"code": "invalid_adapter"}}, status_code=400)
        lock = session_locks[sid]
        if lock.locked():
            return JSONResponse({"error": {"code": "turn_in_progress"}}, status_code=409)
        await lock.acquire()
        epoch = epochs[sid]
        try:
            token = await manager.get_access_token(sid)
            catalog = await eligible_models(sid, token)
            if body.model not in {m["slug"] for m in catalog}:
                raise ValueError("model_not_eligible")
            if epochs[sid] != epoch:
                raise ValueError("session_changed")
            registration = manager.status(sid)["active_registration"]
            cid = body.conversation_id or secrets.token_urlsafe(24)
            existing = conversations.get(cid)
            if body.conversation_id and (
                not existing
                or existing["session"] != sid
                or existing["registration"] != registration
            ):
                lock.release()
                return JSONResponse(
                    {"error": {"code": "conversation_unavailable"}}, status_code=404
                )
            history = existing["history"] if existing else []
            if len(json.dumps(history)) > 100_000:
                lock.release()
                return JSONResponse({"error": {"code": "conversation_full"}}, status_code=400)
        except BaseException as error:
            lock.release()
            if isinstance(error, asyncio.CancelledError):
                raise
            return JSONResponse({"error": safe_error(error)}, status_code=401)

        # No provider request is started until the response body is consumed.
        async def stream():
            queue: asyncio.Queue = asyncio.Queue(maxsize=64)

            async def emit(event):
                if event["type"] == "text_delta":
                    await queue.put(("token", {"delta": event["text"]}))
                elif event["type"] in {"tool_start", "tool_end"}:
                    await queue.put(("tool", {**event, "status": event["type"]}))

            producer_started = False

            async def produce():
                nonlocal producer_started
                producer_started = True
                try:
                    if epochs[sid] != epoch:
                        raise asyncio.CancelledError
                    async with asyncio.timeout(180):
                        result = await runner(
                            body.adapter,
                            token,
                            body.model,
                            history,
                            body.message,
                            tools=tool_dispatch,
                            emit=emit,
                        )
                    if epochs[sid] != epoch:
                        raise asyncio.CancelledError
                    conversations[cid] = {
                        "session": sid,
                        "registration": registration,
                        "history": result.history,
                        "at": time.monotonic(),
                    }
                    # Bounded volatile history. No cross-account persistence or SDK global session.
                    expired = [
                        c for c, v in conversations.items() if time.monotonic() - v["at"] > 86400
                    ]
                    for old in expired:
                        conversations.pop(old, None)
                    while len(conversations) > 200:
                        conversations.pop(next(iter(conversations)))
                    await queue.put(
                        (
                            "completed",
                            {
                                "conversation_id": cid,
                                "text": result.text,
                                "citations": result.citations,
                                "usage": result.usage,
                                "telemetry": result.telemetry,
                                "latency_ms": result.telemetry.get("elapsed_ms"),
                            },
                        )
                    )
                except asyncio.CancelledError:
                    # Consent can be withdrawn even when a slow browser filled the queue.
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(
                        ("error", {"code": "cancelled", "message": "The request was cancelled."})
                    )
                except Exception as error:
                    await queue.put(("error", safe_error(error)))
                finally:
                    active_tasks.pop(sid, None)
                    lock.release()
                await queue.put(None)

            task = asyncio.create_task(produce())
            active_tasks[sid] = task

            def never_started(done):
                if done.cancelled() and not producer_started:
                    active_tasks.pop(sid, None)
                    lock.release()
                    queue.put_nowait(
                        ("error", {"code": "cancelled", "message": "The request was cancelled."})
                    )
                    queue.put_nowait(None)

            task.add_done_callback(never_started)
            try:
                while (item := await queue.get()) is not None:
                    event, payload = item
                    yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Early returns above must not retain the lock.
        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-store",
            },
        )

    @app.get("/")
    async def home():
        return RedirectResponse("/chat-with-edgar/")

    app.mount("/chat-with-edgar", StaticFiles(directory=ROOT / "site", html=True), name="site")
    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=19371)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose an unprivileged loopback port")
    uvicorn.run(
        create_app(port=args.port),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
