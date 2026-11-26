from __future__ import annotations

import importlib.util
import os
import sys

from . import __version__
from .config import load_config


def has_display() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def has_pyside() -> bool:
    return importlib.util.find_spec("PySide6") is not None


def choose_mode() -> str | None:
    forced = os.environ.get("AIRCODE_MODE", "").strip().lower()
    if forced in {"gui", "tui", "web", "chat"}:
        return forced
    display = has_display()
    gui_available = display and has_pyside()
    if not sys.stdin.isatty():
        if gui_available:
            return "gui"
        print("No terminal and no display. Try: aircode --help", file=sys.stderr)
        return None

    options = [
        ("gui", "Desktop window", gui_available),
        ("tui", "Terminal", importlib.util.find_spec("textual") is not None),
        ("web", "Web server (browser)", importlib.util.find_spec("fastapi") is not None),
        ("chat", "Headless chat", True),
    ]
    default = next((index for index, (_, _, available) in enumerate(options) if available), 3)
    print(f"Aircode {__version__}")
    for index, (_, label, available) in enumerate(options, 1):
        marker = " (default)" if index - 1 == default else ""
        state = "" if available else " (not installed)"
        print(f"  {index}. {label}{state}{marker}")
    try:
        answer = input(f"Choose [1-{len(options)}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not answer:
        return options[default][0]
    try:
        index = int(answer) - 1
    except ValueError:
        index = default
    if not 0 <= index < len(options):
        index = default
    mode = options[index][0]
    if not options[index][2]:
        print(f"{mode} is not available; falling back.", file=sys.stderr)
        return options[default][0] if options[default][2] else "chat"
    return mode


def run_mode(mode: str, config) -> int:
    if mode == "gui":
        try:
            from .gui import run_gui

            return run_gui(config)
        except ImportError as exc:
            print(f"PySide6 is not installed ({exc}).", file=sys.stderr)
            print("Install it with: pip install 'aircode-py[gui]'", file=sys.stderr)
            return run_mode("tui", config)
        except Exception as exc:
            print(f"Could not open the desktop window: {exc}", file=sys.stderr)
            return run_mode("tui", config)
    if mode == "tui":
        try:
            from .tui import run_tui

            return run_tui(config)
        except ImportError as exc:
            print(f"Textual is not installed ({exc}).", file=sys.stderr)
            return 1
    if mode == "web":
        from .server import run_server

        return run_server(config)
    if mode == "chat":
        from .auth import ZenAuth

        if not ZenAuth().logged_in():
            print("Not logged in. Run: aircode auth login", file=sys.stderr)
            return 2
        import asyncio

        from .cli import _chat

        return asyncio.run(_chat(config, ZenAuth(), None))
    print(f"Unknown mode: {mode}", file=sys.stderr)
    return 1


def main() -> int:
    config = load_config()
    mode = choose_mode()
    if mode is None:
        return 1
    return run_mode(mode, config)
