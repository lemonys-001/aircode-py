from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import config_path, find_project_root, project_config_path


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class Config:
    model: str = "opencode/deepseek-v4.1-flash-free"
    cwd: Path = field(default_factory=Path.cwd)
    project_root: Path = field(default_factory=Path.cwd)
    auto_approve: bool = False
    write_policy: str = "ask"
    edit_policy: str = "ask"
    bash_policy: str = "ask"
    webfetch_policy: str = "ask"
    bash_timeout: int = 120
    max_steps: int = 40
    max_history_chars: int = 240_000
    server_host: str = "127.0.0.1"
    server_port: int = 4097
    theme: str = "dark"
    raw: dict[str, Any] = field(default_factory=dict)

    def permission(self, tool_name: str) -> str:
        mapping = {
            "write": self.write_policy,
            "edit": self.edit_policy,
            "bash": self.bash_policy,
            "webfetch": self.webfetch_policy,
        }
        return mapping.get(tool_name, "ask").lower()

    def with_auto(self, auto: bool) -> Config:
        self.auto_approve = auto
        return self


def load_config(
    cwd: Path | None = None,
    *,
    global_path: Path | None = None,
    project_path: Path | None = None,
) -> Config:
    cwd = (cwd or Path.cwd()).resolve()
    global_data = _read_toml(global_path or config_path())
    project_root = find_project_root(cwd)
    project_data = _read_toml(project_path or project_config_path(project_root))
    data = _merge(global_data, project_data)

    general = data.get("general", {}) if isinstance(data.get("general"), dict) else {}
    permissions = data.get("permissions", {}) if isinstance(data.get("permissions"), dict) else {}
    tools = data.get("tools", {}) if isinstance(data.get("tools"), dict) else {}
    server = data.get("server", {}) if isinstance(data.get("server"), dict) else {}

    config = Config(
        model=str(general.get("model", Config.model)),
        cwd=cwd,
        project_root=project_root,
        write_policy=str(permissions.get("write", "ask")),
        edit_policy=str(permissions.get("edit", "ask")),
        bash_policy=str(permissions.get("bash", "ask")),
        webfetch_policy=str(permissions.get("webfetch", "ask")),
        bash_timeout=int(tools.get("bash_timeout", 120)),
        max_steps=int(general.get("max_steps", 40)),
        max_history_chars=int(general.get("max_history_chars", 240_000)),
        server_host=str(server.get("host", "127.0.0.1")),
        server_port=int(server.get("port", 4097)),
        theme=str(general.get("theme", "dark")),
        raw=data,
    )
    return config
