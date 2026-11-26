from __future__ import annotations

import asyncio
import fnmatch
import glob as globlib
import json
import re
import shutil
from pathlib import Path

MAX_OUTPUT = 50_000
MAX_READ_CHARS = 60_000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache"}

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a text file. Returns numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, absolute or relative to the project."},
                    "offset": {"type": "integer", "description": "First line (1-based). Default 1."},
                    "limit": {"type": "integer", "description": "Maximum lines. Default 2000."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create or overwrite a text file. Parent directories are created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace an exact string in a file. Read the file first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the project directory and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds. Default 120."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern, for example **/*.py.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory to search. Default project."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regular expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "include": {"type": "string", "description": "Glob filter, for example *.py."},
                    "path": {"type": "string", "description": "File or directory. Default project."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list",
            "description": "List a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory. Default project."},
                },
                "required": [],
            },
        },
    },
]

TOOL_NAMES = {item["function"]["name"] for item in TOOLS}


def _resolve(path: str | None, cwd: Path) -> Path:
    if not path:
        return cwd
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve(strict=False)


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated ({len(text) - limit} more characters)"


def _relative(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def _read(args: dict, cwd: Path, _: int) -> str:
    path = _resolve(args.get("path"), cwd)
    if not path.is_file():
        return f"ERROR: not a file: {path}"
    offset = max(1, int(args.get("offset") or 1))
    limit = min(5000, max(1, int(args.get("limit") or 2000)))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"ERROR: {exc}"
    lines = text.splitlines()
    selected = lines[offset - 1 : offset - 1 + limit]
    numbered = "\n".join(f"{offset + index:6d}\t{line}" for index, line in enumerate(selected))
    if offset - 1 + limit < len(lines):
        numbered += f"\n... {len(lines) - (offset - 1 + limit)} more lines"
    return _truncate(numbered or "(empty file)", MAX_READ_CHARS)


def _write(args: dict, cwd: Path, _: int) -> str:
    path = _resolve(args.get("path"), cwd)
    content = args.get("content")
    if not isinstance(content, str):
        return "ERROR: content must be a string"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"ERROR: {exc}"
    return f"Wrote {len(content)} characters to {_relative(path, cwd)}"


def _edit(args: dict, cwd: Path, _: int) -> str:
    path = _resolve(args.get("path"), cwd)
    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or not old:
        return "ERROR: old_string and new_string must be non-empty strings"
    if not path.is_file():
        return f"ERROR: not a file: {path}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"ERROR: {exc}"
    count = text.count(old)
    if count == 0:
        return "ERROR: old_string was not found. Read the file and copy it exactly."
    replace_all = bool(args.get("replace_all"))
    if count > 1 and not replace_all:
        return f"ERROR: old_string occurs {count} times. Add context or set replace_all."
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"ERROR: {exc}"
    return f"Edited {_relative(path, cwd)} ({count if replace_all else 1} replacement)"


async def _bash(args: dict, cwd: Path, default_timeout: int) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "ERROR: command must be a non-empty string"
    timeout = int(args.get("timeout") or default_timeout or 120)
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return f"ERROR: {exc}"
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return f"ERROR: command timed out after {timeout}s"
    out = stdout.decode("utf-8", "replace")
    err = stderr.decode("utf-8", "replace")
    pieces: list[str] = []
    if out:
        pieces.append(out.rstrip())
    if err:
        pieces.append("[stderr]\n" + err.rstrip())
    if process.returncode:
        pieces.append(f"[exit {process.returncode}]")
    return _truncate("\n".join(pieces) or "(no output)")


def _glob(args: dict, cwd: Path, _: int) -> str:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return "ERROR: pattern is required"
    root = _resolve(args.get("path"), cwd)
    if Path(pattern).is_absolute():
        matches = globlib.iglob(str(Path(pattern).expanduser()), recursive=True)
    else:
        try:
            matches = (str(item) for item in root.glob(pattern))
        except (NotImplementedError, ValueError) as exc:
            return f"ERROR: {exc}"
    results: list[str] = []
    for match in matches:
        if any(part in SKIP_DIRS for part in Path(match).parts):
            continue
        results.append(_relative(Path(match), cwd))
        if len(results) >= 500:
            results.append("... truncated at 500 results")
            break
    return "\n".join(sorted(results)) or "(no matches)"


def _grep_python(pattern: str, root: Path, include: str | None, cwd: Path) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"
    results: list[str] = []
    files = [root] if root.is_file() else root.rglob("*")
    for path in files:
        if len(results) >= 200:
            results.append("... truncated at 200 matches")
            break
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if include and not fnmatch.fnmatch(path.name, include):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                results.append(f"{_relative(path, cwd)}:{number}:{line[:300]}")
                if len(results) >= 200:
                    break
    return "\n".join(results) or "(no matches)"


async def _grep(args: dict, cwd: Path, _: int) -> str:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return "ERROR: pattern is required"
    root = _resolve(args.get("path"), cwd)
    include = args.get("include") if isinstance(args.get("include"), str) else None
    rg = shutil.which("rg")
    if rg:
        command = [
            rg,
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--max-count",
            "200",
            "-S",
        ]
        if include:
            command += ["--glob", include]
        command += [pattern, str(root)]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        except OSError:
            pass
        else:
            if process.returncode in (0, 1):
                output = stdout.decode("utf-8", "replace").strip()
                return _truncate(output or "(no matches)")
            _ = stderr
    return _truncate(await asyncio.to_thread(_grep_python, pattern, root, include, cwd))


def _list(args: dict, cwd: Path, _: int) -> str:
    path = _resolve(args.get("path"), cwd)
    if not path.is_dir():
        return f"ERROR: not a directory: {path}"
    try:
        entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError as exc:
        return f"ERROR: {exc}"
    lines = []
    for entry in entries[:1000]:
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"{entry.name}{suffix}")
    if len(entries) > 1000:
        lines.append("... truncated at 1000 entries")
    return "\n".join(lines) or "(empty directory)"


async def run_tool(
    name: str,
    args: dict,
    cwd: Path,
    *,
    default_timeout: int = 120,
) -> str:
    try:
        if name == "read":
            return _read(args, cwd, default_timeout)
        if name == "write":
            return _write(args, cwd, default_timeout)
        if name == "edit":
            return _edit(args, cwd, default_timeout)
        if name == "bash":
            return await _bash(args, cwd, default_timeout)
        if name == "glob":
            return _glob(args, cwd, default_timeout)
        if name == "grep":
            return await _grep(args, cwd, default_timeout)
        if name == "list":
            return _list(args, cwd, default_timeout)
        return f"ERROR: unknown tool {name}"
    except Exception as exc:  # tool failures are returned to the model
        return f"ERROR: {type(exc).__name__}: {exc}"


def parse_arguments(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON arguments: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be an object")
    return value
