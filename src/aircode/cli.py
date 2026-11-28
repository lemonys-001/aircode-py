from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .agent import PermissionRequest, run_agent
from .auth import AuthError, CredentialStore, ZenAuth
from .catalog import fetch_models, load_models
from .config import Config, load_config
from .parallel import bench, cpu_workers, gil_enabled
from .paths import (
    auth_path,
    cache_dir,
    config_path,
    data_dir,
    db_path,
    ensure_dirs,
    models_cache_path,
)
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aircode-py",
        description="Aircode: a Python coding agent for the terminal, desktop and web.",
    )
    parser.add_argument("--version", action="version", version=f"aircode-py {__version__}")
    sub = parser.add_subparsers(dest="command")

    auth = sub.add_parser("auth", help="manage the OpenCode Zen login")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    login = auth_sub.add_parser("login", help="log in with a device code")
    login.add_argument("--no-browser", action="store_true", help="do not open the browser")
    login.add_argument("--org", help="workspace id, skip the picker")
    auth_sub.add_parser("status", help="show stored credentials")
    auth_sub.add_parser("logout", help="delete the stored Zen token")
    imp = auth_sub.add_parser("import", help="import credentials from OpenCode")
    imp.add_argument("--overwrite", action="store_true")

    models = sub.add_parser("models", help="list Zen models")
    models.add_argument("--refresh", action="store_true")
    models.add_argument("--json", action="store_true")

    sessions = sub.add_parser("sessions", help="list and manage sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", required=True)
    sessions_sub.add_parser("list")
    show = sessions_sub.add_parser("show")
    show.add_argument("session")
    export = sessions_sub.add_parser("export")
    export.add_argument("session")
    export.add_argument("--out", type=Path)
    delete = sessions_sub.add_parser("delete")
    delete.add_argument("session")

    run = sub.add_parser("run", help="run one prompt headlessly")
    run.add_argument("prompt", nargs="+")
    _add_chat_args(run)

    chat = sub.add_parser("chat", help="headless chat REPL")
    _add_chat_args(chat)

    tui = sub.add_parser("tui", help="terminal interface")
    _add_chat_args(tui)

    gui = sub.add_parser("gui", help="native desktop window")
    _add_chat_args(gui)

    web = sub.add_parser("web", help="local web interface")
    _add_chat_args(web)
    web.add_argument("--host", default=None)
    web.add_argument("--port", type=int, default=None)
    web.add_argument("--token", default=None)
    web.add_argument("--open", action="store_true", help="open the browser")

    doctor = sub.add_parser("doctor", help="show environment and auth status")
    doctor.add_argument("--bench", action="store_true", help="run the CPU benchmark")
    return parser


def _add_chat_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-m", "--model")
    parser.add_argument("--auto", action="store_true", help="approve tools without asking")
    parser.add_argument("-s", "--session")
    parser.add_argument("--cwd", type=Path)


def _config(args) -> Config:
    ensure_dirs()
    cwd = getattr(args, "cwd", None) or Path.cwd()
    config = load_config(cwd)
    if getattr(args, "auto", False):
        config.auto_approve = True
    if getattr(args, "model", None):
        config.model = args.model
    return config


def _require_login() -> ZenAuth:
    auth = ZenAuth()
    if not auth.logged_in():
        print("Not logged in. Run: aircode-py auth login", file=sys.stderr)
        raise SystemExit(2)
    return auth


def _print_auth_status() -> None:
    store = CredentialStore()
    status = store.status()
    if not status:
        print("No credentials. Run: aircode-py auth login")
        return
    for provider, value in sorted(status.items()):
        expires = int(value.get("expires") or 0)
        when = ""
        if expires:
            when = datetime.fromtimestamp(expires / 1000).strftime(" expires %Y-%m-%d %H:%M")
        org = f" workspace={value['org']}" if value.get("org") else ""
        account = f" account={value['account']}" if value.get("account") else ""
        print(f"{provider}: {value.get('type')}{org}{account}{when}")


async def _auth_login(args) -> int:
    auth = ZenAuth()
    opened = {"done": False}

    def on_code(code):
        print("Open this URL and approve the login:")
        print(f"  {code.url}")
        print(f"Code: {code.user_code}")
        print("Waiting for approval...", flush=True)
        opened["done"] = True

    async def select_org(orgs):
        print("Workspaces:")
        for index, org in enumerate(orgs, 1):
            print(f"  {index}. {org.name} ({org.id})")
        answer = await asyncio.to_thread(input, "Select workspace [1]: ")
        answer = (answer or "1").strip()
        try:
            choice = int(answer)
        except ValueError:
            choice = 1
        return orgs[min(max(choice, 1), len(orgs)) - 1].id

    try:
        credential = await auth.device_login(
            on_code=on_code,
            select_org=select_org,
            open_browser=not args.no_browser,
            org_id=args.org,
        )
    except AuthError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    print(f"Logged in. Workspace: {credential.org or '(default)'}")
    print(f"Token stored in {auth_path()}")
    return 0


async def _models(args) -> int:
    models = await fetch_models(force=args.refresh)
    if args.json:
        print(json.dumps([model.to_dict() for model in models], indent=2))
        return 0
    for model in models:
        print(model.label)
    return 0


def _sessions(args) -> int:
    store = Store()
    try:
        if args.sessions_command == "list":
            rows = store.list_sessions()
            if not rows:
                print("No sessions.")
                return 0
            for row in rows:
                when = datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d %H:%M")
                print(f"{row['id']}  {when}  {row['title']}  [{row['model']}]")
            return 0
        if args.sessions_command == "show":
            data = store.export_session(args.session)
            if not data:
                print("No such session.", file=sys.stderr)
                return 1
            for message in data["messages"]:
                print(f"\n[{message['role']}]")
                print(message.get("content") or "")
            return 0
        if args.sessions_command == "export":
            data = store.export_session(args.session)
            if not data:
                print("No such session.", file=sys.stderr)
                return 1
            text = json.dumps(data, indent=2)
            if args.out:
                args.out.write_text(text, encoding="utf-8")
                print(f"Wrote {args.out}")
            else:
                print(text)
            return 0
        if args.sessions_command == "delete":
            if not store.delete_session(args.session):
                print("No such session.", file=sys.stderr)
                return 1
            print("Deleted.")
            return 0
    finally:
        store.close()
    return 0


def _permission_prompt(config: Config):
    async def ask(request: PermissionRequest) -> bool:
        if config.auto_approve:
            return True
        print(f"\nPermission: {request.tool}", file=sys.stderr)
        print(request.preview, file=sys.stderr)
        answer = await asyncio.to_thread(input, "Allow? [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    return ask


def _cli_emit(show_tools: bool = True):
    async def emit(event: dict) -> None:
        kind = event.get("type")
        if kind == "text":
            sys.stdout.write(str(event.get("text") or ""))
            sys.stdout.flush()
        elif kind == "reasoning":
            if sys.stderr.isatty():
                sys.stderr.write(f"\033[2m{event.get('text')}\033[0m")
                sys.stderr.flush()
        elif kind == "session":
            print(f"[session {event.get('id')}]", file=sys.stderr)
        elif kind == "tool_start" and show_tools:
            print(f"\n[tool] {event.get('summary')}", file=sys.stderr)
        elif kind == "tool_end" and show_tools:
            output = str(event.get("output") or "")
            if event.get("denied"):
                print("[tool] denied", file=sys.stderr)
            elif output:
                print(output[:2000], file=sys.stderr)
        elif kind == "error":
            print(f"\nerror: {event.get('message')}", file=sys.stderr)
        elif kind == "done":
            print("", flush=True)

    return emit


async def _run_once(config: Config, auth: ZenAuth, session: str | None, text: str) -> int:
    store = Store()
    try:
        await run_agent(
            store,
            config,
            session,
            text,
            emit=_cli_emit(),
            permission=_permission_prompt(config),
            auth=auth,
        )
    finally:
        store.close()
    return 0


async def _chat(config: Config, auth: ZenAuth, session: str | None) -> int:
    store = Store()
    if session is None:
        session = store.create_session(model=config.model, cwd=str(config.cwd))
    print("Aircode chat. /help for commands, /quit to exit.")
    try:
        while True:
            try:
                text = await asyncio.to_thread(input, "you> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                command, _, value = text.partition(" ")
                value = value.strip()
                if command in ("/quit", "/exit"):
                    break
                if command == "/help":
                    print("/new  /model <id>  /session  /sessions  /auto  /quit")
                    continue
                if command == "/new":
                    session = store.create_session(model=config.model, cwd=str(config.cwd))
                    print(f"New session {session}")
                    continue
                if command == "/model":
                    if value:
                        config.model = value
                        store.update_session(session, model=value)
                        print(f"Model: {value}")
                    else:
                        print(config.model)
                    continue
                if command == "/session":
                    print(session)
                    continue
                if command == "/sessions":
                    for row in store.list_sessions():
                        print(f"{row['id']}  {row['title']}")
                    continue
                if command == "/auto":
                    config.auto_approve = not config.auto_approve
                    print(f"Auto-approve: {config.auto_approve}")
                    continue
                print("Unknown command. /help")
                continue
            await run_agent(
                store,
                config,
                session,
                text,
                emit=_cli_emit(show_tools=False),
                permission=_permission_prompt(config),
                auth=auth,
            )
            print()
    finally:
        store.close()
    return 0


def _doctor(args) -> int:
    print(f"aircode-py {__version__}")
    print(f"python {sys.version.split()[0]}")
    print(f"GIL enabled: {gil_enabled()}")
    print(f"CPU workers: {cpu_workers()}")
    print(f"config: {config_path()}")
    print(f"data: {data_dir()}")
    print(f"cache: {cache_dir()}")
    print(f"database: {db_path()}")
    print(f"models cache: {models_cache_path()}")
    print(f"textual: {importlib.util.find_spec('textual') is not None}")
    print(f"fastapi: {importlib.util.find_spec('fastapi') is not None}")
    print(f"PySide6: {importlib.util.find_spec('PySide6') is not None}")
    print(f"display: {bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))}")
    dist = Path(__file__).parent / "web" / "dist" / "index.html"
    print(f"web build: {dist.exists()} ({dist})")
    store = Store()
    try:
        print(f"sessions: {len(store.list_sessions(limit=1000))}")
    finally:
        store.close()
    _print_auth_status()
    cached = load_models()
    free = [model for model in cached if model.free]
    print(f"models: {len(cached)} ({len(free)} free)")
    if args.bench:
        result = bench()
        print("bench:", json.dumps(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        from .launcher import main as launcher_main

        return launcher_main()

    if args.command == "auth":
        if args.auth_command == "login":
            return asyncio.run(_auth_login(args))
        if args.auth_command == "status":
            _print_auth_status()
            return 0
        if args.auth_command == "logout":
            print("Logged out." if ZenAuth().logout() else "No Zen token stored.")
            return 0
        if args.auth_command == "import":
            try:
                imported = ZenAuth().import_opencode(overwrite=args.overwrite)
            except AuthError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print("Imported: " + ", ".join(imported) if imported else "Nothing to import.")
            return 0

    if args.command == "models":
        return asyncio.run(_models(args))
    if args.command == "sessions":
        return _sessions(args)

    if args.command in ("run", "chat", "tui", "gui", "web"):
        config = _config(args)
        if args.command == "run":
            auth = _require_login()
            prompt = " ".join(args.prompt)
            return asyncio.run(_run_once(config, auth, args.session, prompt))
        if args.command == "chat":
            auth = _require_login()
            return asyncio.run(_chat(config, auth, args.session))
        if args.command == "tui":
            from .tui import run_tui

            return run_tui(config, session_id=args.session, model=args.model)
        if args.command == "gui":
            from .gui import run_gui

            return run_gui(config, session_id=args.session, model=args.model)
        if args.command == "web":
            from .server import run_server

            return run_server(
                config,
                host=args.host or config.server_host,
                port=args.port or config.server_port,
                token=args.token,
                open_browser=args.open,
            )

    if args.command == "doctor":
        return _doctor(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
