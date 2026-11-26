from __future__ import annotations

import time

from .agent import PermissionRequest, run_agent
from .auth import ZenAuth
from .catalog import load_models
from .config import Config
from .store import Store

try:  # textual is a hard dependency, but keep the error readable
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Input, Label, Markdown, OptionList, Static
    from textual.widgets.option_list import Option
except ImportError as exc:  # pragma: no cover - exercised only without deps
    raise SystemExit(
        "Textual is not installed. Install Aircode with: pip install aircode-py"
    ) from exc


class PermissionScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "deny", "Deny")]

    def __init__(self, request: PermissionRequest):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Allow {self.request.tool}?", id="permission-title"),
            Static(self.request.preview, id="permission-preview"),
            Horizontal(
                Button("Deny", id="deny"),
                Button("Allow", id="allow", variant="primary"),
                id="permission-actions",
            ),
            id="permission-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "allow")

    def action_deny(self) -> None:
        self.dismiss(False)


class ModelScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str):
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        models = load_models()
        options = [
            Option(
                f"{'* ' if model.id in self.current else '  '}{model.name}  ({model.id})",
                id=model.id,
            )
            for model in models
        ]
        yield Vertical(Label("Model"), OptionList(*options, id="model-options"), id="model-dialog")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AircodeApp(App):
    CSS = """
    Screen {
        background: #0f1115;
        color: #e6e8eb;
    }
    #topbar {
        height: 3;
        padding: 0 1;
        background: #151922;
        border-bottom: solid #262b36;
        align-vertical: middle;
    }
    #brand {
        width: auto;
        margin-right: 2;
        text-style: bold;
        color: #d97706;
    }
    #model, #session {
        width: auto;
        margin-right: 2;
        color: #9aa3b2;
    }
    #status {
        width: 1fr;
        text-align: right;
        color: #9aa3b2;
    }
    #transcript {
        padding: 1 2;
        background: #0f1115;
    }
    #composer {
        height: 3;
        padding: 0 1;
        background: #151922;
        border-top: solid #262b36;
        align-vertical: middle;
    }
    #prompt {
        width: 1fr;
        border: none;
        background: #0f1115;
        color: #e6e8eb;
    }
    #prompt:focus {
        border: none;
    }
    #send {
        width: auto;
        margin-left: 1;
        background: #d97706;
        color: #0f1115;
        border: none;
    }
    #send:hover {
        background: #f59e0b;
    }
    Markdown {
        margin-bottom: 1;
        background: #0f1115;
    }
    Markdown.user {
        color: #9aa3b2;
    }
    .tool {
        color: #9aa3b2;
        margin-bottom: 1;
    }
    .error {
        color: #f87171;
        margin-bottom: 1;
    }
    .notice {
        color: #fbbf24;
        margin-bottom: 1;
    }
    #permission-dialog, #model-dialog {
        width: 70%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: #151922;
        border: solid #3a3f4a;
        align: center middle;
    }
    #permission-preview {
        margin: 1 0;
        color: #cbd5e1;
        max-height: 18;
        overflow-y: auto;
    }
    #permission-actions {
        height: auto;
        align-horizontal: right;
    }
    #permission-actions Button {
        margin-left: 1;
    }
    #deny {
        background: #262b36;
        color: #e6e8eb;
    }
    #model-options {
        height: auto;
        max-height: 24;
        background: #0f1115;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+o", "pick_model", "Model"),
        Binding("ctrl+l", "clear", "Clear"),
    ]

    def __init__(self, config: Config, session_id: str | None = None, model: str | None = None):
        super().__init__()
        self.config = config
        self.store = Store()
        self.session_id = session_id or self.store.create_session(
            model=model or config.model, cwd=str(config.cwd)
        )
        session = self.store.get_session(self.session_id) or {}
        self.model = model or session.get("model") or config.model
        self._busy = False
        self._assistant: Markdown | None = None
        self._buffer: list[str] = []
        self._last_render = 0.0

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("Aircode", id="brand"),
            Static(self._short_model(), id="model"),
            Static(self.session_id, id="session"),
            Static("Ready", id="status"),
            id="topbar",
        )
        yield VerticalScroll(id="transcript")
        yield Horizontal(
            Input(placeholder="Message Aircode...", id="prompt"),
            Button("Send", id="send", variant="primary"),
            id="composer",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Aircode"
        self.query_one("#prompt", Input).focus()
        self.run_worker(self._load_history(), group="load", exclusive=True)
        if not ZenAuth().logged_in():
            self._notice("Not logged in. Run: aircode auth login")

    def _short_model(self) -> str:
        return self.model.split("/", 1)[-1]

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status", Static).update(text)
        except Exception:
            pass

    async def _mount(self, widget) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(widget)
        transcript.scroll_end(animate=False)

    def _notice(self, text: str) -> None:
        self.run_worker(self._mount(Static(text, classes="notice")), group="notice")

    async def _load_history(self) -> None:
        try:
            transcript = self.query_one("#transcript", VerticalScroll)
            await transcript.remove_children()
        except Exception:
            pass
        for message in self.store.messages(self.session_id):
            role = message.get("role")
            content = message.get("content") or ""
            if role == "user":
                await self._mount(Markdown(content, classes="user"))
            elif role == "assistant" and content:
                await self._mount(Markdown(content))
            elif role == "tool":
                await self._mount(
                    Static(
                        f"[{message.get('name') or 'tool'}]\n{content[:1500]}",
                        classes="tool",
                    )
                )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            self._submit()

    def _submit(self) -> None:
        if self._busy:
            return
        prompt = self.query_one("#prompt", Input)
        text = prompt.value.strip()
        if not text:
            return
        prompt.value = ""
        self.run_worker(self._send(text), group="chat", exclusive=True)

    async def _send(self, text: str) -> None:
        self._busy = True
        self._set_status("Thinking")
        self.query_one("#send", Button).disabled = True
        await self._mount(Markdown(text, classes="user"))
        self._assistant = Markdown("")
        await self._mount(self._assistant)
        self._buffer = []
        self._last_render = 0.0

        async def emit(event: dict) -> None:
            kind = event.get("type")
            if kind == "text":
                self._buffer.append(str(event.get("text") or ""))
                now = time.monotonic()
                if now - self._last_render > 0.05 and self._assistant is not None:
                    self._last_render = now
                    self._assistant.update("".join(self._buffer))
            elif kind == "reasoning":
                self._set_status("Thinking")
            elif kind == "tool_start":
                self._set_status(f"Running {event.get('name')}")
                await self._mount(
                    Static(f"→ {event.get('summary')}", classes="tool")
                )
            elif kind == "tool_end":
                output = str(event.get("output") or "").strip()
                if output and not event.get("denied"):
                    await self._mount(Static(output[:1500], classes="tool"))
                self._set_status("Thinking")
            elif kind == "session":
                self.session_id = str(event.get("id") or self.session_id)
                try:
                    self.query_one("#session", Static).update(self.session_id)
                except Exception:
                    pass
            elif kind == "error":
                await self._mount(Static(str(event.get("message")), classes="error"))
                self._set_status("Error")
            elif kind == "usage":
                usage = event.get("usage") or {}
                total = usage.get("total_tokens")
                if total:
                    self._set_status(f"{total} tokens")

        async def permission(request: PermissionRequest) -> bool:
            self._set_status("Waiting for approval")
            return bool(await self.push_screen_wait(PermissionScreen(request)))

        try:
            await run_agent(
                self.store,
                self.config,
                self.session_id,
                text,
                emit=emit,
                permission=permission,
                model=self.model,
            )
        except Exception as exc:
            await self._mount(Static(f"{type(exc).__name__}: {exc}", classes="error"))
        finally:
            if self._assistant is not None:
                self._assistant.update("".join(self._buffer))
            self._busy = False
            self._assistant = None
            self.query_one("#send", Button).disabled = False
            self._set_status("Ready")
            self.query_one("#prompt", Input).focus()

    def action_new_session(self) -> None:
        if self._busy:
            return
        self.session_id = self.store.create_session(
            model=self.model, cwd=str(self.config.cwd)
        )
        self.query_one("#session", Static).update(self.session_id)
        self.run_worker(self._load_history(), group="load", exclusive=True)
        self._set_status("Ready")

    def action_pick_model(self) -> None:
        self.run_worker(self._pick_model(), group="model", exclusive=True)

    async def _pick_model(self) -> None:
        model = await self.push_screen_wait(ModelScreen(self.model))
        if model:
            self.model = f"opencode/{model}"
            self.config.model = self.model
            self.store.update_session(self.session_id, model=self.model)
            self.query_one("#model", Static).update(self._short_model())
            self._set_status("Ready")

    def action_clear(self) -> None:
        if self._busy:
            return
        self.run_worker(self._load_history(), group="load", exclusive=True)

    def on_unmount(self) -> None:
        self.store.close()


def run_tui(config: Config, session_id: str | None = None, model: str | None = None) -> int:
    app = AircodeApp(config, session_id=session_id, model=model)
    app.run()
    return 0
