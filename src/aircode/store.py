from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Untitled',
    cwd TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    parent_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reasoning TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    name TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""


def new_session_id() -> str:
    stamp = int(time.time() * 1000)
    return f"ses_{stamp:x}{secrets.token_hex(4)}"


def new_request_id() -> str:
    return f"msg_{secrets.token_hex(8)}"


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        with self._lock, self.conn:
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def create_session(
        self,
        *,
        model: str,
        cwd: str,
        title: str = "Untitled",
        parent_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        sid = session_id or new_session_id()
        now = int(time.time())
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO sessions (id, title, cwd, model, parent_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, title, cwd, model, parent_id, now, now),
            )
        return sid

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def latest_session(self) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
                "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self.conn:
            cursor = self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0

    def update_session(
        self,
        session_id: str,
        *,
        model: str | None = None,
        title: str | None = None,
        cwd: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[object] = []
        if model is not None:
            fields.append("model = ?")
            values.append(model)
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if cwd is not None:
            fields.append("cwd = ?")
            values.append(cwd)
        fields.append("updated_at = ?")
        values.append(int(time.time()))
        values.append(session_id)
        with self._lock, self.conn:
            self.conn.execute(
                f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?", values
            )

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str = "",
        *,
        reasoning: str | None = None,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> int:
        now = int(time.time())
        encoded = json.dumps(tool_calls) if tool_calls else None
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "INSERT INTO messages "
                "(session_id, role, content, reasoning, tool_calls, tool_call_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, role, content, reasoning, encoded, tool_call_id, name, now),
            )
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
        return int(cursor.lastrowid)

    def messages(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            encoded = item.pop("tool_calls", None)
            try:
                item["tool_calls"] = json.loads(encoded) if encoded else None
            except json.JSONDecodeError:
                item["tool_calls"] = None
            result.append(item)
        return result

    def rename_from_first_message(self, session_id: str, text: str) -> None:
        session = self.get_session(session_id)
        if not session or session.get("title") not in (None, "", "Untitled"):
            return
        title = " ".join(text.strip().split())
        if len(title) > 60:
            title = title[:57].rstrip() + "..."
        if title:
            self.update_session(session_id, title=title)

    def export_session(self, session_id: str) -> dict | None:
        session = self.get_session(session_id)
        if not session:
            return None
        return {"session": session, "messages": self.messages(session_id)}
