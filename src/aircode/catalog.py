from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .paths import models_cache_path

CATALOG_URL = "https://opencode.ai/zen/v1/models"
DEFAULT_BASE_URL = "https://opencode.ai/inference/openai/v1"
CACHE_TTL = 24 * 60 * 60

FALLBACK_MODELS = [
    ("big-pickle", "Big Pickle"),
    ("deepseek-v4.1-flash-free", "DeepSeek V4.1 Flash Free"),
    ("mimo-v2.6-flash-free", "MiMo-V2.6-Flash Free"),
    ("iceman-free", "Iceman Free"),
    ("beam-free", "Beam Free"),
    ("flint-40b-free", "Flint 40B Free"),
    ("nemotron-3.5-super-vl-free", "Nemotron 3.5 Super VL Free"),
    ("nemotron-3.5-lightning-free", "Nemotron 3.5 Lightning Free"),
    ("hy4-flash-free", "Hy4 Flash Free"),
    ("tidepool-alpha-free", "Tidepool Alpha Free"),
]


@dataclass
class Model:
    id: str
    name: str
    base_url: str = DEFAULT_BASE_URL
    api: str = "chat-completions"
    tools: bool = True
    context: int = 200_000
    output: int = 32_000
    free: bool = False

    @property
    def label(self) -> str:
        suffix = "  free" if self.free else ""
        return f"{self.name} ({self.id}){suffix}"

    @property
    def reference(self) -> str:
        return f"opencode/{self.id}"

    def to_dict(self) -> dict:
        return asdict(self)


def _fallback() -> list[Model]:
    return [
        Model(
            id=model_id,
            name=name,
            free=model_id.endswith("-free") or model_id == "big-pickle",
        )
        for model_id, name in FALLBACK_MODELS
    ]


def _parse(payload: dict) -> list[Model]:
    models: list[Model] = []
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        api_map = item.get("api") if isinstance(item.get("api"), dict) else {}
        recommended = item.get("recommended_api")
        if recommended != "chat-completions" and "chat-completions" not in api_map:
            continue
        route = api_map.get("chat-completions") if isinstance(api_map.get("chat-completions"), dict) else {}
        cost_block = item.get("cost") if isinstance(item.get("cost"), dict) else {}
        text_cost = cost_block.get("text") if isinstance(cost_block.get("text"), dict) else {}
        input_cost = text_cost.get("input") if isinstance(text_cost.get("input"), dict) else {}
        price = input_cost.get("price")
        free = item["id"].endswith("-free") or price == 0
        caps_block = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
        capabilities = caps_block.get("tools") if isinstance(caps_block.get("tools"), dict) else {}
        limits = item.get("limit") if isinstance(item.get("limit"), dict) else {}
        models.append(
            Model(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                base_url=str(route.get("base_url") or DEFAULT_BASE_URL),
                api="chat-completions",
                tools=bool(capabilities.get("supported", True)),
                context=int(limits.get("context") or 200_000),
                output=int(limits.get("output") or 32_000),
                free=bool(free),
            )
        )
    models.sort(key=lambda model: (not model.free, model.name.lower()))
    return models or _fallback()


def _read_cache(path: Path, *, max_age: int = CACHE_TTL) -> list[Model] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    fetched = float(payload.get("fetched_at") or 0)
    if max_age and time.time() - fetched > max_age:
        return None
    raw = payload.get("models")
    if not isinstance(raw, list):
        return None
    models: list[Model] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        models.append(
            Model(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                base_url=str(item.get("base_url") or DEFAULT_BASE_URL),
                api=str(item.get("api") or "chat-completions"),
                tools=bool(item.get("tools", True)),
                context=int(item.get("context") or 200_000),
                output=int(item.get("output") or 32_000),
                free=bool(item.get("free", False)),
            )
        )
    return models or None


def _write_cache(path: Path, models: list[Model]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"fetched_at": time.time(), "models": [model.to_dict() for model in models]},
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_models(path: Path | None = None) -> list[Model]:
    return _read_cache(path or models_cache_path()) or _fallback()


async def fetch_models(
    *,
    force: bool = False,
    path: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    url: str = CATALOG_URL,
) -> list[Model]:
    cache_path = path or models_cache_path()
    if not force:
        cached = _read_cache(cache_path)
        if cached:
            return cached
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0), transport=transport
        ) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return load_models(cache_path)
    models = _parse(payload)
    _write_cache(cache_path, models)
    return models


def resolve_model(name: str, models: list[Model] | None = None) -> Model:
    reference = name.strip()
    if reference.startswith("opencode/"):
        reference = reference.split("/", 1)[1]
    for model in models or load_models():
        if model.id == reference:
            return model
    return Model(id=reference, name=reference, free=reference.endswith("-free"))
