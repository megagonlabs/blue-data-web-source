"""Tests for profile management — storage layer and CLI commands."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import blue_data_web_cli.profiles as prof_mod
from blue_data_web_cli.config import reset_source
from blue_data_web_cli.main import cli


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset():
    reset_source()
    yield
    reset_source()


@pytest.fixture()
def profiles_file(tmp_path, monkeypatch):
    """Point the profiles module at a temp file for each test."""
    p = tmp_path / "profiles.json"
    monkeypatch.setattr(prof_mod, "_PROFILES_FILE", p)
    return p


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def mock_source():
    source = MagicMock()
    with patch("blue_data_web_cli.main.get_source", return_value=source):
        yield source


# ── Storage layer ─────────────────────────────────────────────────────────────

class TestStorageLayer:
    def test_list_empty(self, profiles_file):
        assert prof_mod.list_profiles() == []

    def test_upsert_and_list(self, profiles_file):
        prof_mod.upsert_profile("dev", {"redis_host": "localhost", "collection": "dev-web"})
        assert prof_mod.list_profiles() == ["dev"]

    def test_upsert_merges(self, profiles_file):
        prof_mod.upsert_profile("dev", {"redis_host": "localhost"})
        prof_mod.upsert_profile("dev", {"collection": "dev-web"})
        p = prof_mod.get_profile("dev")
        assert p["redis_host"] == "localhost"
        assert p["collection"] == "dev-web"

    def test_get_missing_returns_none(self, profiles_file):
        assert prof_mod.get_profile("nonexistent") is None

    def test_remove_existing(self, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev"})
        assert prof_mod.remove_profile("dev") is True
        assert prof_mod.get_profile("dev") is None

    def test_remove_missing_returns_false(self, profiles_file):
        assert prof_mod.remove_profile("ghost") is False

    def test_remove_clears_default(self, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev"})
        prof_mod.set_default("dev")
        prof_mod.remove_profile("dev")
        assert prof_mod.default_profile_name() is None

    def test_set_and_get_default(self, profiles_file):
        prof_mod.upsert_profile("prod", {"collection": "prod"})
        prof_mod.set_default("prod")
        assert prof_mod.default_profile_name() == "prod"

    def test_active_profile_prefers_env(self, profiles_file, monkeypatch):
        prof_mod.upsert_profile("prod", {"collection": "prod"})
        prof_mod.set_default("prod")
        monkeypatch.setenv("BLUE_DATA_PROFILE", "dev")
        assert prof_mod.active_profile_name() == "dev"
        monkeypatch.delenv("BLUE_DATA_PROFILE")

    def test_active_profile_falls_back_to_default(self, profiles_file, monkeypatch):
        monkeypatch.delenv("BLUE_DATA_PROFILE", raising=False)
        prof_mod.upsert_profile("prod", {"collection": "prod"})
        prof_mod.set_default("prod")
        assert prof_mod.active_profile_name() == "prod"

    def test_active_profile_none_when_unset(self, profiles_file, monkeypatch):
        monkeypatch.delenv("BLUE_DATA_PROFILE", raising=False)
        assert prof_mod.active_profile_name() is None

    def test_default_excluded_from_list(self, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev"})
        prof_mod.set_default("dev")
        assert "_default" not in prof_mod.list_profiles()

    def test_multiple_profiles_sorted(self, profiles_file):
        prof_mod.upsert_profile("staging", {"collection": "s"})
        prof_mod.upsert_profile("dev",     {"collection": "d"})
        prof_mod.upsert_profile("prod",    {"collection": "p"})
        assert prof_mod.list_profiles() == ["dev", "prod", "staging"]


class TestApplyProfile:
    def test_applies_missing_env_vars(self, profiles_file, monkeypatch):
        monkeypatch.delenv("BLUE_WEB_COLLECTION", raising=False)
        prof_mod.upsert_profile("dev", {"collection": "dev-web"})
        prof_mod.apply_profile("dev")
        assert os.environ.get("BLUE_WEB_COLLECTION") == "dev-web"
        monkeypatch.delenv("BLUE_WEB_COLLECTION", raising=False)

    def test_env_var_wins_over_profile(self, profiles_file, monkeypatch):
        monkeypatch.setenv("BLUE_WEB_COLLECTION", "shell-collection")
        prof_mod.upsert_profile("dev", {"collection": "profile-collection"})
        prof_mod.apply_profile("dev")
        assert os.environ["BLUE_WEB_COLLECTION"] == "shell-collection"

    def test_apply_none_is_noop(self, profiles_file):
        prof_mod.apply_profile(None)  # must not raise

    def test_apply_missing_profile_is_noop(self, profiles_file, monkeypatch):
        monkeypatch.delenv("BLUE_WEB_COLLECTION", raising=False)
        prof_mod.apply_profile("nonexistent")
        assert "BLUE_WEB_COLLECTION" not in os.environ


# ── CLI: config profile add ───────────────────────────────────────────────────

class TestProfileAdd:
    def test_add_creates_profile(self, runner, profiles_file):
        result = runner.invoke(cli, [
            "profile", "add", "dev",
            "--collection", "dev-web",
            "--redis-host", "localhost",
        ])
        assert result.exit_code == 0
        assert "dev" in result.output
        p = prof_mod.get_profile("dev")
        assert p["collection"] == "dev-web"
        assert p["redis_host"] == "localhost"

    def test_add_duplicate_exits_1(self, runner, profiles_file):
        runner.invoke(cli, ["profile", "add", "dev", "--collection", "dev-web"])
        result = runner.invoke(cli, ["profile", "add", "dev", "--collection", "other"])
        assert result.exit_code == 1

    def test_add_json_output(self, runner, profiles_file):
        result = runner.invoke(cli, [
            "--format", "json", "profile", "add", "dev",
            "--collection", "dev-web",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["profile"] == "dev"
        assert data["settings"]["collection"] == "dev-web"

    def test_add_no_settings_exits_2(self, runner, profiles_file):
        result = runner.invoke(cli, ["profile", "add", "dev"])
        assert result.exit_code == 2

    def test_add_invalid_embedding_choice(self, runner, profiles_file):
        result = runner.invoke(cli, [
            "profile", "add", "dev", "--embedding", "invalid",
        ])
        assert result.exit_code != 0

    def test_add_all_options(self, runner, profiles_file):
        result = runner.invoke(cli, [
            "profile", "add", "full",
            "--redis-host", "redis.host",
            "--redis-port", "6380",
            "--collection", "my-col",
            "--embedding", "openai",
            "--chunk-size", "500",
            "--overlap", "50",
            "--timeout", "60",
            "--enricher", "llm",
            "--llm-model", "gpt-4o",
            "--embedding-model", "text-embedding-3-large",
        ])
        assert result.exit_code == 0
        p = prof_mod.get_profile("full")
        assert p["redis_port"] == 6380
        assert p["chunk_size"] == 500
        assert p["embedding"] == "openai"


# ── CLI: config profile remove ────────────────────────────────────────────────

class TestProfileRemove:
    def test_remove_existing(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev"})
        result = runner.invoke(cli, ["profile", "remove", "dev"])
        assert result.exit_code == 0
        assert prof_mod.get_profile("dev") is None

    def test_remove_missing_exits_1(self, runner, profiles_file):
        result = runner.invoke(cli, ["profile", "remove", "ghost"])
        assert result.exit_code == 1

    def test_remove_json_output(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev"})
        result = runner.invoke(cli, ["--format", "json", "profile", "remove", "dev"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["removed"] == "dev"


# ── CLI: config profile list ──────────────────────────────────────────────────

class TestProfileList:
    def test_empty(self, runner, profiles_file):
        result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0
        assert "No profiles" in result.output

    def test_lists_names(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "d"})
        prof_mod.upsert_profile("prod", {"collection": "p"})
        result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0
        assert "dev" in result.output
        assert "prod" in result.output

    def test_marks_default(self, runner, profiles_file):
        prof_mod.upsert_profile("prod", {"collection": "p"})
        prof_mod.set_default("prod")
        result = runner.invoke(cli, ["profile", "list"])
        assert "(default)" in result.output

    def test_json_output(self, runner, profiles_file):
        prof_mod.upsert_profile("dev",  {"collection": "d"})
        prof_mod.upsert_profile("prod", {"collection": "p"})
        prof_mod.set_default("prod")
        result = runner.invoke(cli, ["--format", "json", "profile", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "dev" in data["profiles"]
        assert "prod" in data["profiles"]
        assert data["default"] == "prod"


# ── CLI: config profile config ────────────────────────────────────────────────

class TestProfileConfig:
    def test_config_updates_existing(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev-web"})
        result = runner.invoke(cli, [
            "profile", "config", "dev", "--redis-host", "localhost",
        ])
        assert result.exit_code == 0
        p = prof_mod.get_profile("dev")
        assert p["collection"] == "dev-web"
        assert p["redis_host"] == "localhost"

    def test_config_missing_profile_exits_1(self, runner, profiles_file):
        result = runner.invoke(cli, [
            "profile", "config", "ghost", "--collection", "x",
        ])
        assert result.exit_code == 1

    def test_config_no_settings_exits_2(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev-web"})
        result = runner.invoke(cli, ["profile", "config", "dev"])
        assert result.exit_code == 2

    def test_config_json_output(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev-web"})
        result = runner.invoke(cli, [
            "--format", "json", "profile", "config", "dev", "--collection", "updated",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["profile"] == "dev"
        assert data["settings"]["collection"] == "updated"

    def test_config_does_not_create_new(self, runner, profiles_file):
        result = runner.invoke(cli, [
            "profile", "config", "new-profile", "--collection", "x",
        ])
        assert result.exit_code == 1
        assert prof_mod.get_profile("new-profile") is None


# ── CLI: config profile inspect ───────────────────────────────────────────────

class TestProfileInspect:
    def test_inspect_by_name(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev-web", "redis_host": "localhost"})
        result = runner.invoke(cli, ["profile", "inspect", "dev"])
        assert result.exit_code == 0
        assert "dev-web" in result.output
        assert "localhost" in result.output

    def test_inspect_missing_exits_1(self, runner, profiles_file):
        result = runner.invoke(cli, ["profile", "inspect", "ghost"])
        assert result.exit_code == 1

    def test_inspect_no_name_no_default_exits_2(self, runner, profiles_file, monkeypatch):
        monkeypatch.delenv("BLUE_DATA_PROFILE", raising=False)
        result = runner.invoke(cli, ["profile", "inspect"])
        assert result.exit_code == 2

    def test_inspect_uses_active_when_no_arg(self, runner, profiles_file, monkeypatch):
        monkeypatch.delenv("BLUE_DATA_PROFILE", raising=False)
        prof_mod.upsert_profile("prod", {"collection": "prod-web"})
        prof_mod.set_default("prod")
        result = runner.invoke(cli, ["profile", "inspect"])
        assert result.exit_code == 0
        assert "prod-web" in result.output

    def test_inspect_json_output(self, runner, profiles_file):
        prof_mod.upsert_profile("dev", {"collection": "dev-web"})
        result = runner.invoke(cli, ["--format", "json", "profile", "inspect", "dev"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["profile"] == "dev"
        assert data["settings"]["collection"] == "dev-web"


# ── CLI: config profile select ────────────────────────────────────────────────

class TestProfileSelect:
    def test_select_sets_default(self, runner, profiles_file):
        prof_mod.upsert_profile("prod", {"collection": "prod"})
        result = runner.invoke(cli, ["profile", "select", "prod"])
        assert result.exit_code == 0
        assert prof_mod.default_profile_name() == "prod"

    def test_select_missing_exits_1(self, runner, profiles_file):
        result = runner.invoke(cli, ["profile", "select", "ghost"])
        assert result.exit_code == 1

    def test_select_json_output(self, runner, profiles_file):
        prof_mod.upsert_profile("prod", {"collection": "prod"})
        result = runner.invoke(cli, ["--format", "json", "profile", "select", "prod"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["default"] == "prod"


# ── --profile flag integration ────────────────────────────────────────────────

class TestProfileFlag:
    def test_profile_injects_collection_before_source(self, runner, profiles_file, monkeypatch):
        """Profile value reaches the source when env var is absent."""
        monkeypatch.delenv("BLUE_WEB_COLLECTION", raising=False)
        prof_mod.upsert_profile("dev", {"collection": "dev-web"})

        captured_env = {}
        original_get_source = __import__(
            "blue_data_web_cli.config", fromlist=["get_source"]
        ).get_source

        def _capturing_get_source():
            captured_env["collection"] = os.environ.get("BLUE_WEB_COLLECTION")
            source = MagicMock()
            source.execute_query.return_value = None
            return source

        with patch("blue_data_web_cli.main.get_source", side_effect=_capturing_get_source):
            result = runner.invoke(cli, ["--profile", "dev", "index", "ensure"])

        assert result.exit_code == 0
        assert captured_env["collection"] == "dev-web"

    def test_env_var_wins_over_profile(self, runner, profiles_file, monkeypatch):
        """Shell env var takes precedence over profile value."""
        monkeypatch.setenv("BLUE_WEB_COLLECTION", "shell-col")
        prof_mod.upsert_profile("dev", {"collection": "profile-col"})

        captured_env = {}

        def _capturing_get_source():
            captured_env["collection"] = os.environ.get("BLUE_WEB_COLLECTION")
            source = MagicMock()
            source.execute_query.return_value = None
            return source

        with patch("blue_data_web_cli.main.get_source", side_effect=_capturing_get_source):
            result = runner.invoke(cli, ["--profile", "dev", "index", "ensure"])

        assert result.exit_code == 0
        assert captured_env["collection"] == "shell-col"
