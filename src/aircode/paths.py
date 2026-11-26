from __future__ import annotations

import os
import sys
from pathlib import Path


def _home() -> Path:
    return Path.home()


def _xdg(env: str, fallback: Path) -> Path:
    value = os.environ.get(env)
    return Path(value).expanduser() if value else fallback


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", _home() / "AppData" / "Roaming"))
        return base / "Aircode"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "Aircode"
    return _xdg("XDG_CONFIG_HOME", _home() / ".config") / "aircode"


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local"))
        return base / "Aircode"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "Aircode"
    return _xdg("XDG_DATA_HOME", _home() / ".local" / "share") / "aircode"


def cache_dir() -> Path:
    if sys.platform == "win32":
        return data_dir() / "cache"
    if sys.platform == "darwin":
        return _home() / "Library" / "Caches" / "Aircode"
    return _xdg("XDG_CACHE_HOME", _home() / ".cache") / "aircode"


def auth_path() -> Path:
    return data_dir() / "auth.json"


def db_path() -> Path:
    return data_dir() / "sessions.db"


def config_path() -> Path:
    return config_dir() / "config.toml"


def models_cache_path() -> Path:
    return cache_dir() / "models.json"


def project_config_path(cwd: Path) -> Path:
    return cwd / ".aircode" / "config.toml"


def ensure_dirs() -> None:
    for path in (config_dir(), data_dir(), cache_dir()):
        path.mkdir(parents=True, exist_ok=True)


def import_opencode_auth_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", _home() / "AppData" / "Roaming"))
        return base / "opencode" / "auth.json"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "opencode" / "auth.json"
    return _xdg("XDG_DATA_HOME", _home() / ".local" / "share") / "opencode" / "auth.json"


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / ".aircode").exists():
            return candidate
    return current


def read_agents_md(start: Path, limit: int = 40_000) -> str:
    """Read AGENTS.md from the project root and its parents, nearest first."""

    current = start.resolve()
    chunks: list[str] = []
    seen: set[Path] = set()
    for candidate in (current, *current.parents):
        path = candidate / "AGENTS.md"
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.append(f"# {path}\n\n{text}")
        if sum(len(c) for c in chunks) >= limit:
            break
    return "\n\n".join(chunks)[:limit]
