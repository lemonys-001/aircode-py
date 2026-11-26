from aircode.catalog import _parse, resolve_model
from aircode.config import load_config


def test_config_merges_global_and_project(tmp_path):
    global_path = tmp_path / "config.toml"
    project_path = tmp_path / "project.toml"
    global_path.write_text(
        '[general]\nmodel = "opencode/global"\nmax_steps = 5\n'
        '[permissions]\nbash = "deny"\n'
    )
    project_path.write_text('[general]\nmodel = "opencode/project"\n')

    config = load_config(
        tmp_path, global_path=global_path, project_path=project_path
    )
    assert config.model == "opencode/project"
    assert config.max_steps == 5
    assert config.permission("bash") == "deny"
    assert config.permission("write") == "ask"
    assert config.permission("read") == "ask"


def test_catalog_parses_free_models_and_bad_nesting():
    payload = {
        "data": [
            {
                "id": "odd-free",
                "name": "Odd",
                "api": {"chat-completions": {"base_url": "https://example.test/v1"}},
                "cost": {"text": "not-a-dict"},
                "capabilities": {"tools": "not-a-dict"},
                "limit": "not-a-dict",
            },
            {"id": "skip-me", "api": {"responses": {}}},
        ]
    }
    models = _parse(payload)
    assert len(models) == 1
    model = models[0]
    assert model.id == "odd-free"
    assert model.free is True
    assert model.tools is True
    assert model.context == 200_000
    assert model.base_url == "https://example.test/v1"
    assert model.reference == "opencode/odd-free"


def test_resolve_model_accepts_reference():
    model = resolve_model("opencode/deepseek-v4.1-flash-free")
    assert model.id == "deepseek-v4.1-flash-free"
    assert model.reference.startswith("opencode/")
    assert model.base_url.startswith("https://")
