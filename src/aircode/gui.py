from __future__ import annotations

import asyncio
import secrets
import sys

from .agent import PermissionRequest, run_agent
from .auth import ZenAuth
from .catalog import load_models
from .config import Config
from .store import Store

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCharFormat, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the optional extra
    HAVE_QT = False


if HAVE_QT:

    class Engine(QThread):
        text = Signal(str)
        reasoning = Signal(str)
        tool_start = Signal(str)
        tool_end = Signal(str, bool)
        error = Signal(str)
        done = Signal(dict)
        permission = Signal(str, object)
        status = Signal(str)
        session = Signal(str, str)
        usage = Signal(dict)

        def run(self) -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        def submit(self, coroutine):
            return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def stop(self) -> None:
            loop = getattr(self, "loop", None)
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
            self.wait(2000)

    class Broker:
        def __init__(self, engine: Engine):
            self.engine = engine
            self.pending: dict[str, asyncio.Future] = {}

        async def ask(self, request: PermissionRequest) -> bool:
            rid = secrets.token_hex(6)
            future = asyncio.get_running_loop().create_future()
            self.pending[rid] = future
            self.engine.permission.emit(rid, request.to_dict())
            try:
                return bool(await asyncio.wait_for(future, 600))
            except TimeoutError:
                return False
            finally:
                self.pending.pop(rid, None)

        def resolve(self, rid: str, allow: bool) -> None:
            future = self.pending.get(rid)
            loop = getattr(self.engine, "loop", None)
            if future is not None and not future.done() and loop is not None:
                loop.call_soon_threadsafe(future.set_result, allow)

    class Composer(QPlainTextEdit):
        submitted = Signal()

        def keyPressEvent(self, event) -> None:
            enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if enter and not shift:
                self.submitted.emit()
                event.accept()
                return
            super().keyPressEvent(event)

    class MainWindow(QMainWindow):
        def __init__(
            self,
            config: Config,
            session_id: str | None = None,
            model: str | None = None,
        ):
            super().__init__()
            self.config = config
            self.store = Store()
            self.session_id = session_id or self.store.create_session(
                model=model or config.model, cwd=str(config.cwd)
            )
            session = self.store.get_session(self.session_id) or {}
            self.model = model or session.get("model") or config.model
            self.engine = Engine(self)
            self.broker = Broker(self.engine)
            self._task: asyncio.Task | None = None
            self._busy = False
            self._assistant_open = False
            self._build()
            self._connect()
            self.engine.start()
            self._load_models()
            self._load_session()
            self.setWindowTitle("Aircode")

        def _build(self) -> None:
            root = QWidget()
            layout = QVBoxLayout(root)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(8)

            top = QHBoxLayout()
            top.setSpacing(8)
            brand = QLabel("Aircode")
            brand.setObjectName("brand")
            self.model_box = QComboBox()
            self.model_box.setMinimumWidth(300)
            self.status = QLabel("Ready")
            self.status.setObjectName("status")
            self.new_button = QPushButton("New")
            self.stop_button = QPushButton("Stop")
            self.stop_button.setEnabled(False)
            self.stop_button.setObjectName("stop")
            top.addWidget(brand)
            top.addWidget(self.model_box)
            top.addStretch(1)
            top.addWidget(self.status)
            top.addWidget(self.new_button)
            top.addWidget(self.stop_button)
            layout.addLayout(top)

            self.transcript = QTextEdit()
            self.transcript.setReadOnly(True)
            self.transcript.setObjectName("transcript")
            self.transcript.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            layout.addWidget(self.transcript, 1)

            composer_row = QHBoxLayout()
            composer_row.setSpacing(8)
            self.composer = Composer()
            self.composer.setPlaceholderText(
                "Message Aircode...  Enter sends, Shift+Enter adds a line"
            )
            self.composer.setMaximumHeight(120)
            self.send_button = QPushButton("Send")
            self.send_button.setObjectName("send")
            composer_row.addWidget(self.composer, 1)
            composer_row.addWidget(self.send_button)
            layout.addLayout(composer_row)

            self.setCentralWidget(root)
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #0f1115; color: #e6e8eb; }
                QLabel#brand { color: #d97706; font-size: 17px; font-weight: 700; }
                QLabel#status { color: #9aa3b2; }
                QTextEdit#transcript {
                    background: #0f1115;
                    border: 1px solid #262b36;
                    border-radius: 6px;
                    padding: 12px;
                    selection-background-color: #d97706;
                    selection-color: #0f1115;
                }
                QPlainTextEdit {
                    background: #151922;
                    border: 1px solid #2a2f3a;
                    border-radius: 6px;
                    padding: 8px;
                    color: #e6e8eb;
                }
                QPlainTextEdit:focus { border: 1px solid #d97706; }
                QComboBox {
                    background: #151922;
                    border: 1px solid #2a2f3a;
                    border-radius: 6px;
                    padding: 6px 8px;
                    color: #e6e8eb;
                }
                QComboBox QAbstractItemView {
                    background: #151922;
                    color: #e6e8eb;
                    selection-background-color: #2a2f3a;
                }
                QPushButton {
                    background: #262b36;
                    color: #e6e8eb;
                    border: 1px solid #3a3f4a;
                    border-radius: 6px;
                    padding: 7px 14px;
                    min-height: 20px;
                }
                QPushButton:hover { background: #303643; }
                QPushButton:disabled { color: #6b7280; background: #1a1e27; }
                QPushButton#send { background: #d97706; color: #0f1115; border: none; font-weight: 600; }
                QPushButton#send:hover { background: #f59e0b; }
                QPushButton#send:disabled { background: #3a3f4a; color: #9aa3b2; }
                QPushButton#stop { color: #f87171; }
                """
            )

        def _connect(self) -> None:
            self.engine.text.connect(self.on_text)
            self.engine.reasoning.connect(lambda _: self._set_status("Thinking"))
            self.engine.tool_start.connect(self.on_tool_start)
            self.engine.tool_end.connect(self.on_tool_end)
            self.engine.error.connect(self.on_error)
            self.engine.done.connect(self.on_done)
            self.engine.permission.connect(self.on_permission)
            self.engine.status.connect(self._set_status)
            self.engine.session.connect(self.on_session)
            self.engine.usage.connect(self.on_usage)
            self.send_button.clicked.connect(self.submit)
            self.composer.submitted.connect(self.submit)
            self.new_button.clicked.connect(self.new_session)
            self.stop_button.clicked.connect(self.stop)
            self.model_box.currentIndexChanged.connect(self.on_model_changed)

        def _load_models(self) -> None:
            models = load_models()
            self.model_box.blockSignals(True)
            self.model_box.clear()
            current = -1
            for index, model in enumerate(models):
                self.model_box.addItem(f"{model.name}  ({model.id})", model.id)
                if model.id == self.model.split("/", 1)[-1]:
                    current = index
            if current >= 0:
                self.model_box.setCurrentIndex(current)
            else:
                self.model_box.addItem(self.model, self.model)
                self.model_box.setCurrentIndex(self.model_box.count() - 1)
            self.model_box.blockSignals(False)

        def _clear(self) -> None:
            self.transcript.clear()
            self._assistant_open = False

        def _load_session(self) -> None:
            self._clear()
            for message in self.store.messages(self.session_id):
                role = message.get("role")
                content = message.get("content") or ""
                if role == "user":
                    self._block("You", content, "#9aa3b2")
                elif role == "assistant" and content:
                    self._block("Aircode", content, "#d97706")
                elif role == "tool":
                    name = message.get("name") or "tool"
                    self._block(name, content[:2000], "#6b7280", mono=True)
            if not ZenAuth().logged_in():
                self._block(
                    "Aircode",
                    "Not logged in. Run `aircode auth login` in a terminal, then reopen.",
                    "#fbbf24",
                )

        def _block(self, title: str, text: str, color: str, *, mono: bool = False) -> None:
            cursor = self.transcript.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if self.transcript.toPlainText():
                cursor.insertText("\n\n")
            title_format = QTextCharFormat()
            title_format.setForeground(QColor(color))
            title_format.setFontWeight(QFont.Weight.DemiBold)
            cursor.insertText(title + "\n", title_format)
            body_format = QTextCharFormat()
            body_format.setForeground(QColor("#e6e8eb" if not mono else "#9aa3b2"))
            cursor.insertText(text, body_format)
            self.transcript.setTextCursor(cursor)
            self._scroll_end()

        def _stream(self, text: str) -> None:
            cursor = self.transcript.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#e6e8eb"))
            cursor.insertText(text, fmt)
            self.transcript.setTextCursor(cursor)
            self._scroll_end()

        def _scroll_end(self) -> None:
            bar = self.transcript.verticalScrollBar()
            bar.setValue(bar.maximum())

        def _set_status(self, text: str) -> None:
            self.status.setText(text)

        def _set_busy(self, busy: bool) -> None:
            self._busy = busy
            self.send_button.setEnabled(not busy)
            self.stop_button.setEnabled(busy)

        def submit(self) -> None:
            if self._busy:
                return
            text = self.composer.toPlainText().strip()
            if not text:
                return
            self.composer.clear()
            self._set_busy(True)
            self._set_status("Thinking")
            self._block("You", text, "#9aa3b2")
            self.transcript.append("")
            cursor = self.transcript.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#d97706"))
            fmt.setFontWeight(QFont.Weight.DemiBold)
            cursor.insertText("Aircode\n", fmt)
            self.transcript.setTextCursor(cursor)
            self._assistant_open = True
            self.engine.submit(self._run(text))

        async def _run(self, text: str) -> None:
            self._task = asyncio.current_task()

            async def emit(event: dict) -> None:
                kind = event.get("type")
                if kind == "text":
                    self.engine.text.emit(str(event.get("text") or ""))
                elif kind == "reasoning":
                    self.engine.reasoning.emit(str(event.get("text") or ""))
                elif kind == "tool_start":
                    self.engine.tool_start.emit(str(event.get("summary") or ""))
                elif kind == "tool_end":
                    self.engine.tool_end.emit(
                        str(event.get("output") or ""), bool(event.get("denied"))
                    )
                elif kind == "error":
                    self.engine.error.emit(str(event.get("message") or ""))
                elif kind == "done":
                    self.engine.done.emit(dict(event.get("usage") or {}))
                elif kind == "session":
                    self.engine.session.emit(
                        str(event.get("id") or ""), str(event.get("title") or "")
                    )
                elif kind == "usage":
                    self.engine.usage.emit(dict(event.get("usage") or {}))

            try:
                await run_agent(
                    self.store,
                    self.config,
                    self.session_id,
                    text,
                    emit=emit,
                    permission=self.broker.ask,
                    model=self.model,
                )
            except asyncio.CancelledError:
                self.engine.error.emit("Cancelled.")
            except Exception as exc:
                self.engine.error.emit(f"{type(exc).__name__}: {exc}")
            finally:
                self._task = None

        def stop(self) -> None:
            task = self._task
            loop = getattr(self.engine, "loop", None)
            if task is not None and loop is not None:
                loop.call_soon_threadsafe(task.cancel)

        def on_text(self, text: str) -> None:
            self._stream(text)

        def on_tool_start(self, summary: str) -> None:
            self._set_status(f"Running {summary.split(' ', 1)[0]}")
            cursor = self.transcript.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#9aa3b2"))
            cursor.insertText(f"\n→ {summary}\n", fmt)
            self.transcript.setTextCursor(cursor)
            self._scroll_end()

        def on_tool_end(self, output: str, denied: bool) -> None:
            if denied:
                self._stream("[denied]")
            elif output.strip():
                cursor = self.transcript.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                fmt = QTextCharFormat()
                fmt.setForeground(QColor("#6b7280"))
                cursor.insertText(output[:2000] + "\n", fmt)
                self.transcript.setTextCursor(cursor)
                self._scroll_end()
            self._set_status("Thinking")

        def on_error(self, message: str) -> None:
            self._block("Error", message, "#f87171")
            self._set_status("Error")
            self._set_busy(False)

        def on_done(self, usage: dict) -> None:
            self._set_status("Ready")
            self._set_busy(False)
            total = usage.get("total_tokens")
            if total:
                self.status.setText(f"{total} tokens")
            self.composer.setFocus()

        def on_usage(self, usage: dict) -> None:
            total = usage.get("total_tokens")
            if total:
                self._set_status(f"{total} tokens")

        def on_session(self, session_id: str, title: str) -> None:
            if session_id:
                self.session_id = session_id
            if title and title != "Untitled":
                self.setWindowTitle(f"Aircode - {title}")

        def on_permission(self, rid: str, payload: dict) -> None:
            self._set_status("Waiting for approval")
            tool = payload.get("tool", "tool")
            preview = str(payload.get("preview") or "")
            box = QMessageBox(self)
            box.setWindowTitle("Aircode permission")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f"Allow {tool}?")
            box.setInformativeText(preview[:4000])
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.No)
            allowed = box.exec() == QMessageBox.StandardButton.Yes
            self.broker.resolve(rid, allowed)
            self._set_status("Thinking")

        def on_model_changed(self, index: int) -> None:
            if index < 0:
                return
            model_id = self.model_box.itemData(index)
            if not model_id:
                return
            self.model = f"opencode/{model_id}"
            self.config.model = self.model
            self.store.update_session(self.session_id, model=self.model)
            self._set_status("Ready")

        def new_session(self) -> None:
            if self._busy:
                return
            self.session_id = self.store.create_session(
                model=self.model, cwd=str(self.config.cwd)
            )
            self._load_session()
            self.setWindowTitle("Aircode")
            self._set_status("Ready")
            self.composer.setFocus()

        def closeEvent(self, event) -> None:
            self.stop()
            self.engine.stop()
            self.store.close()
            super().closeEvent(event)


def run_gui(
    config: Config,
    session_id: str | None = None,
    model: str | None = None,
) -> int:
    if not HAVE_QT:
        raise SystemExit(
            "PySide6 is not installed. Install it with: pip install 'aircode-py[gui]'"
        )
    from .launcher import has_display

    if not has_display():
        raise SystemExit("No display detected. Use `aircode tui` or `aircode web`.")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Aircode")
    app.setStyle("Fusion")
    window = MainWindow(config, session_id=session_id, model=model)
    window.resize(1040, 760)
    window.show()
    return int(app.exec())


__all__ = ["run_gui"]
