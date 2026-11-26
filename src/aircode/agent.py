from __future__ import annotations

import asyncio
import json
import platform
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .auth import AuthError, ZenAuth
from .catalog import Model, resolve_model
from .config import Config
from .paths import read_agents_md
from .provider import ProviderError, ZenProvider
from .store import Store, new_request_id
from .tools import TOOLS, parse_arguments, run_tool

Emit = Callable[[dict], Awaitable[None]]


@dataclass
class PermissionRequest:
    tool: str
    args: dict
    preview: str

    def to_dict(self) -> dict:
        return {"tool": self.tool, "args": self.args, "preview": self.preview}


async def _deny(_: PermissionRequest) -> bool:
    return False


def _system_prompt(cwd: Path, model: Model, max_steps: int) -> str:
    context = read_agents_md(cwd)
    parts = [
        "You are Aircode, an AI coding agent running on the user's machine.",
        f"Working directory: {cwd}",
        f"Operating system: {platform.system()} {platform.release()}",
        f"Model: {model.id}",
        "",
        "Rules:",
        "- Be concise and direct. Answer the question before adding detail.",
        "- Use the tools to inspect and change the project. Never invent file contents.",
        "- Read a file before editing it, and keep changes small and focused.",
        "- Ask before destructive actions; the interface already prompts for writes and commands.",
        "- Do not print secrets. Do not claim to have run something you did not run.",
        f"- You may take at most {max_steps} tool steps for this task.",
    ]
    if context:
        parts += ["", "Project instructions (AGENTS.md):", context]
    return "\n".join(parts)


def _to_openai(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        role = row.get("role")
        content = row.get("content") or ""
        if role == "assistant":
            item: dict = {"role": "assistant", "content": content}
            if row.get("tool_calls"):
                item["tool_calls"] = row["tool_calls"]
            result.append(item)
        elif role == "tool":
            item = {"role": "tool", "content": content}
            if row.get("tool_call_id"):
                item["tool_call_id"] = row["tool_call_id"]
            if row.get("name"):
                item["name"] = row["name"]
            result.append(item)
        elif role in ("user", "system"):
            result.append({"role": role, "content": content})
    return result


def _outside(cwd: Path, args: dict, name: str) -> bool:
    values = [args.get("path")]
    if name == "glob":
        values.append(args.get("pattern"))
    for raw in values:
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw).expanduser()
        target = path if path.is_absolute() else cwd / path
        try:
            target.resolve().relative_to(cwd.resolve())
        except ValueError:
            return True
    return False


def _trim(messages: list[dict], budget: int) -> list[dict]:
    units: list[list[dict]] = []
    for message in messages:
        if message.get("role") == "user" or not units:
            units.append([message])
        else:
            units[-1].append(message)

    kept: list[list[dict]] = []
    used = 0
    for unit in reversed(units):
        size = sum(
            len(str(message.get("content") or ""))
            + len(str(message.get("tool_calls") or ""))
            + 200
            for message in unit
        )
        if used + size > budget and kept:
            break
        kept.insert(0, unit)
        used += size
    return [message for unit in kept for message in unit]


def _tool_summary(name: str, args: dict) -> str:
    for key in ("command", "path", "pattern"):
        value = args.get(key)
        if isinstance(value, str):
            return f"{name} {value[:160]}"
    return name


async def run_agent(
    store: Store,
    config: Config,
    session_id: str | None,
    text: str,
    *,
    emit: Emit,
    permission: Callable[[PermissionRequest], Awaitable[bool]] | None = None,
    provider=None,
    auth: ZenAuth | None = None,
    model: str | None = None,
) -> str:
    permission = permission or _deny
    session = store.get_session(session_id) if session_id else None
    if session is None:
        sid = store.create_session(
            model=model or config.model,
            cwd=str(config.cwd),
            session_id=session_id,
        )
        session = store.get_session(sid) or {
            "id": sid,
            "cwd": str(config.cwd),
            "model": model or config.model,
            "title": "Untitled",
        }
    else:
        sid = str(session["id"])
    cwd = Path(session.get("cwd") or config.cwd).resolve()
    resolved = resolve_model(model or session.get("model") or config.model)
    store.update_session(sid, model=resolved.reference, cwd=str(cwd))

    store.add_message(sid, "user", text)
    store.rename_from_first_message(sid, text)
    await emit(
        {
            "type": "session",
            "id": sid,
            "model": resolved.reference,
            "title": (store.get_session(sid) or {}).get("title", "Untitled"),
        }
    )

    if provider is None:
        credential = await (auth or ZenAuth()).access()
        provider = ZenProvider(credential.access, org=credential.org)

    history = _to_openai(store.messages(sid))
    system = _system_prompt(cwd, resolved, config.max_steps)
    usage_total: dict[str, int] = {}
    tool_schemas = TOOLS if resolved.tools else None
    steps = 0
    read_only = {"read", "glob", "grep", "list"}

    try:
        while steps < config.max_steps:
            request_messages = [{"role": "system", "content": system}] + _trim(
                list(history), config.max_history_chars
            )
            assistant_text: list[str] = []
            reasoning_text: list[str] = []
            calls: dict[int, dict] = {}
            finish_reason = ""
            emitted = False

            for attempt in range(3):
                try:
                    async for event in provider.stream(
                        resolved,
                        request_messages,
                        tool_schemas,
                        session_id=sid,
                        request_id=new_request_id(),
                    ):
                        kind = event.get("type")
                        if kind == "text":
                            chunk = str(event.get("text") or "")
                            if chunk:
                                assistant_text.append(chunk)
                                emitted = True
                                await emit({"type": "text", "text": chunk})
                        elif kind == "reasoning":
                            chunk = str(event.get("text") or "")
                            if chunk:
                                reasoning_text.append(chunk)
                                emitted = True
                                await emit({"type": "reasoning", "text": chunk})
                        elif kind == "tool_call":
                            emitted = True
                            index = int(event.get("index") or 0)
                            entry = calls.setdefault(
                                index,
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if event.get("id"):
                                entry["id"] = str(event["id"])
                            if event.get("name"):
                                entry["function"]["name"] = str(event["name"])
                            entry["function"]["arguments"] += str(event.get("arguments") or "")
                        elif kind == "usage":
                            usage = event.get("usage")
                            if isinstance(usage, dict):
                                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                                    if isinstance(usage.get(key), int):
                                        usage_total[key] = usage_total.get(key, 0) + usage[key]
                                await emit({"type": "usage", "usage": usage})
                        elif kind == "done":
                            finish_reason = str(event.get("finish_reason") or "stop")
                    break
                except ProviderError as exc:
                    retryable = exc.status in (429, 500, 502, 503, 504)
                    if retryable and not emitted and attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    raise

            full_text = "".join(assistant_text)
            full_reasoning = "".join(reasoning_text)
            tool_calls = [calls[index] for index in sorted(calls) if calls[index]["function"]["name"]]
            for index, call in enumerate(tool_calls):
                if not call.get("id"):
                    call["id"] = f"call_{sid[-6:]}_{steps}_{index}"

            store.add_message(
                sid,
                "assistant",
                full_text,
                reasoning=full_reasoning or None,
                tool_calls=tool_calls or None,
            )
            assistant_history: dict = {"role": "assistant", "content": full_text}
            if tool_calls:
                assistant_history["tool_calls"] = tool_calls
            history.append(assistant_history)

            if not tool_calls:
                if not full_text and finish_reason not in ("stop", "length"):
                    await emit({"type": "error", "message": "The model returned an empty response."})
                break

            steps += 1
            for call in tool_calls:
                name = call["function"]["name"]
                raw_arguments = call["function"]["arguments"]
                try:
                    args = parse_arguments(raw_arguments)
                except ValueError as exc:
                    result = f"ERROR: {exc}"
                    await emit({"type": "tool_start", "name": name, "args": {}, "summary": name})
                    await emit({"type": "tool_end", "name": name, "output": result})
                    store.add_message(
                        sid,
                        "tool",
                        result,
                        tool_call_id=call["id"],
                        name=name,
                    )
                    history.append(
                        {
                            "role": "tool",
                            "content": result,
                            "tool_call_id": call["id"],
                            "name": name,
                        }
                    )
                    continue

                summary = _tool_summary(name, args)
                await emit(
                    {"type": "tool_start", "name": name, "args": args, "summary": summary}
                )
                allowed_read = name in read_only and not _outside(cwd, args, name)
                policy = "allow" if allowed_read else config.permission(name)
                if config.auto_approve or policy == "allow":
                    allowed = True
                elif policy == "deny":
                    allowed = False
                else:
                    preview = json.dumps(args, indent=2, ensure_ascii=False)[:4000]
                    allowed = await permission(PermissionRequest(name, args, preview))
                if allowed:
                    result = await run_tool(
                        name, args, cwd, default_timeout=config.bash_timeout
                    )
                else:
                    result = "Permission denied by the user. Do not retry without asking."
                await emit(
                    {
                        "type": "tool_end",
                        "name": name,
                        "output": result[:8000],
                        "denied": not allowed,
                    }
                )
                store.add_message(
                    sid,
                    "tool",
                    result,
                    tool_call_id=call["id"],
                    name=name,
                )
                history.append(
                    {
                        "role": "tool",
                        "content": result,
                        "tool_call_id": call["id"],
                        "name": name,
                    }
                )

        else:
            await emit(
                {
                    "type": "error",
                    "message": f"Reached the step limit ({config.max_steps}).",
                }
            )

        await emit(
            {
                "type": "done",
                "usage": usage_total,
                "session": sid,
                "model": resolved.reference,
            }
        )
        return sid
    except asyncio.CancelledError:
        try:
            await emit({"type": "error", "message": "Cancelled."})
        except Exception:
            pass
        raise
    except AuthError as exc:
        await emit({"type": "error", "message": str(exc)})
        return sid
    except ProviderError as exc:
        await emit({"type": "error", "message": str(exc)})
        return sid
    except Exception as exc:  # surface a recoverable error to the interface
        await emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        return sid
