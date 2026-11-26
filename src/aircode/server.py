from __future__ import annotations

import asyncio
import json
import secrets
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent import PermissionRequest, run_agent
from .auth import AuthError, ZenAuth
from .catalog import fetch_models
from .config import Config
from .store import Store

DIST = Path(__file__).parent / "web" / "dist"
SENTINEL = object()


def _sse(event: dict) -> str:
    kind = str(event.get("type") or "message")
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {kind}\ndata: {data}\n\n"


class PermissionBroker:
    def __init__(self) -> None:
        self.pending: dict[str, asyncio.Future] = {}

    async def ask(self, request: PermissionRequest, emit) -> bool:
        rid = secrets.token_hex(6)
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        await emit(
            {
                "type": "permission",
                "id": rid,
                "request": request.to_dict(),
            }
        )
        try:
            return bool(await asyncio.wait_for(future, 600))
        except TimeoutError:
            return False
        finally:
            self.pending.pop(rid, None)

    def resolve(self, rid: str, allow: bool) -> bool:
        future = self.pending.get(rid)
        if future is None or future.done():
            return False
        future.set_result(allow)
        return True


def create_app(
    config: Config,
    *,
    store: Store | None = None,
    token: str | None = None,
    auth: ZenAuth | None = None,
) -> FastAPI:
    app = FastAPI(title="Aircode", version=__version__, docs_url=None, redoc_url=None)
    app.state.store = store or Store()
    app.state.config = config
    app.state.auth = auth or ZenAuth()
    app.state.token = token
    app.state.broker = PermissionBroker()
    app.state.tasks = {}

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if token and request.url.path.startswith("/api"):
            supplied = ""
            header = request.headers.get("authorization", "")
            if header.lower().startswith("bearer "):
                supplied = header[7:].strip()
            supplied = supplied or request.query_params.get("token", "")
            if supplied != token:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/status")
    async def status():
        return {
            "version": __version__,
            "logged_in": app.state.auth.logged_in(),
            "model": config.model,
            "cwd": str(config.cwd),
            "token_required": bool(token),
        }

    @app.get("/api/models")
    async def models(refresh: int = 0):
        items = await fetch_models(force=bool(refresh))
        return [model.to_dict() for model in items]

    @app.get("/api/sessions")
    async def sessions():
        return app.state.store.list_sessions()

    @app.post("/api/sessions")
    async def create_session(request: Request):
        body = await request.json()
        model = str(body.get("model") or config.model)
        session_id = app.state.store.create_session(model=model, cwd=str(config.cwd))
        return app.state.store.get_session(session_id)

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        data = app.state.store.export_session(session_id)
        if not data:
            raise HTTPException(404, "No such session")
        return data

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        if not app.state.store.delete_session(session_id):
            raise HTTPException(404, "No such session")
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await request.json()
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        if not app.state.auth.logged_in():
            raise HTTPException(401, "Not logged in. Run: aircode auth login")
        session_id = body.get("session_id") or None
        model = body.get("model") or None
        queue: asyncio.Queue = asyncio.Queue()

        task_ref: dict[str, asyncio.Task] = {}

        async def emit(event: dict) -> None:
            if event.get("type") == "session" and event.get("id"):
                app.state.tasks[str(event["id"])] = task_ref.get("task")
            await queue.put(event)

        async def permission(request_obj: PermissionRequest) -> bool:
            return await app.state.broker.ask(request_obj, emit)

        async def run() -> None:
            try:
                await run_agent(
                    app.state.store,
                    config,
                    session_id,
                    text,
                    emit=emit,
                    permission=permission,
                    model=model,
                )
            except asyncio.CancelledError:
                await emit({"type": "error", "message": "Cancelled."})
            except AuthError as exc:
                await emit({"type": "error", "message": str(exc)})
            except Exception as exc:
                await emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                await queue.put(SENTINEL)

        task = asyncio.create_task(run())
        task_ref["task"] = task
        app.state.tasks[session_id or "new"] = task

        def forget(_):
            for key, value in list(app.state.tasks.items()):
                if value is task:
                    app.state.tasks.pop(key, None)

        task.add_done_callback(forget)

        async def stream():
            try:
                while True:
                    event = await queue.get()
                    if event is SENTINEL:
                        break
                    yield _sse(event)
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/permission/{rid}")
    async def permission_result(rid: str, request: Request):
        body = await request.json()
        allow = bool(body.get("allow"))
        if not app.state.broker.resolve(rid, allow):
            raise HTTPException(404, "No pending permission request")
        return {"ok": True}

    @app.post("/api/cancel/{session_id}")
    async def cancel(session_id: str):
        task = app.state.tasks.get(session_id)
        if task is None:
            return {"ok": False}
        task.cancel()
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": __version__}

    if (DIST / "index.html").exists():
        assets = DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/favicon.svg")
        async def favicon():
            icon = DIST / "favicon.svg"
            return FileResponse(icon) if icon.exists() else HTMLResponse("", status_code=404)

        @app.get("/")
        async def index():
            return FileResponse(DIST / "index.html")

        @app.get("/api/{path:path}")
        async def api_missing(path: str):
            raise HTTPException(404, "Not found")

        @app.get("/{path:path}")
        async def spa(path: str):
            candidate = (DIST / path).resolve()
            if candidate.is_file() and DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(DIST / "index.html")
    else:

        @app.get("/")
        async def missing():
            return HTMLResponse(
                "<!doctype html><title>Aircode</title>"
                "<body style='background:#0f1115;color:#e6e8eb;font:15px system-ui;padding:32px'>"
                "<h1 style='color:#d97706'>Aircode</h1>"
                "<p>The web UI has not been built. Run <code>cd web && npm install && npm run build</code>."
                "</p></body>"
            )

    return app


def run_server(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 4097,
    token: str | None = None,
    open_browser: bool = False,
) -> int:
    import uvicorn

    generated = False
    if not token and host not in ("127.0.0.1", "localhost", "::1"):
        token = secrets.token_urlsafe(18)
        generated = True
    app = create_app(config, token=token)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    if token:
        url += f"#token={token}"
    print(f"Aircode web: {url}")
    if generated:
        print("A token was generated because the server is not bound to localhost.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
