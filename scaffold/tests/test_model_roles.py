"""Tests for the model-role configuration."""

from __future__ import annotations

import json

from scaffold.model_roles import (
    DEFAULT_CHEAP_MODEL,
    DEFAULT_DECISION_MODEL,
    DEFAULT_MID_MODEL,
    ModelRoles,
    load_model_roles,
)


class TestModelRoles:
    def test_defaults(self) -> None:
        roles = ModelRoles()
        assert roles.cheap == DEFAULT_CHEAP_MODEL
        assert roles.mid == DEFAULT_MID_MODEL
        assert roles.decision == DEFAULT_DECISION_MODEL

    def test_explicit_path(self, tmp_path) -> None:
        cfg = tmp_path / "roles.json"
        cfg.write_text(json.dumps({"cheap": "model-a", "mid": "model-b"}))
        roles = load_model_roles(cfg)
        assert roles.cheap == "model-a"
        assert roles.mid == "model-b"
        assert roles.decision == DEFAULT_DECISION_MODEL  # unspecified → default

    def test_env_var(self, tmp_path, monkeypatch) -> None:
        cfg = tmp_path / "custom.json"
        cfg.write_text(json.dumps({"decision": "model-z"}))
        monkeypatch.setenv("SCAFFOLD_MODEL_ROLES", str(cfg))
        assert load_model_roles().decision == "model-z"

    def test_find_upwards_from_cwd(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "model-roles.json").write_text(json.dumps({"cheap": "found-it"}))
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        monkeypatch.delenv("SCAFFOLD_MODEL_ROLES", raising=False)
        monkeypatch.chdir(nested)
        assert load_model_roles().cheap == "found-it"

    def test_malformed_implicit_config_falls_back(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "model-roles.json").write_text("{not json")
        monkeypatch.delenv("SCAFFOLD_MODEL_ROLES", raising=False)
        monkeypatch.chdir(tmp_path)
        assert load_model_roles().cheap == DEFAULT_CHEAP_MODEL
