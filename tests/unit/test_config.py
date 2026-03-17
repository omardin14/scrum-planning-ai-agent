"""Tests for configuration and environment variable handling."""

import os

import pytest

from scrum_agent.config import (
    detect_proxy,
    disable_langsmith_tracing,
    get_anthropic_api_key,
    get_config_dir,
    get_config_file,
    get_session_prune_days,
    is_langsmith_enabled,
    load_user_config,
)


def test_get_anthropic_api_key_returns_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    assert get_anthropic_api_key() == "test-key-123"


def test_get_anthropic_api_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(OSError, match="ANTHROPIC_API_KEY is not set"):
        get_anthropic_api_key()


def test_langsmith_enabled_when_configured(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
    assert is_langsmith_enabled() is True


def test_langsmith_disabled_when_no_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert is_langsmith_enabled() is False


def test_langsmith_disabled_when_tracing_off(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
    assert is_langsmith_enabled() is False


class TestProxyDetection:
    """Tests for proxy environment variable detection and LangSmith auto-disable."""

    def _clear_proxy_vars(self, monkeypatch):
        """Remove all proxy env vars so tests start from a clean state."""
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)

    def test_detect_proxy_https(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        assert detect_proxy() == "http://proxy:8080"

    def test_detect_proxy_http(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        monkeypatch.setenv("HTTP_PROXY", "http://proxy:3128")
        assert detect_proxy() == "http://proxy:3128"

    def test_detect_proxy_lowercase(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        monkeypatch.setenv("https_proxy", "http://proxy:9090")
        assert detect_proxy() == "http://proxy:9090"

    def test_detect_proxy_none(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        assert detect_proxy() is None

    def test_disable_langsmith_tracing(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        disable_langsmith_tracing()
        assert "LANGSMITH_TRACING" not in os.environ


class TestGetConfigDir:
    """Tests for get_config_dir() — returns ~/.scrum-agent/, creating it if absent."""

    def test_returns_scrum_agent_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("scrum_agent.config.Path.home", lambda: tmp_path)
        result = get_config_dir()
        assert result == tmp_path / ".scrum-agent"

    def test_creates_directory_if_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr("scrum_agent.config.Path.home", lambda: tmp_path)
        target = tmp_path / ".scrum-agent"
        assert not target.exists()
        get_config_dir()
        assert target.is_dir()

    def test_no_error_if_directory_already_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr("scrum_agent.config.Path.home", lambda: tmp_path)
        (tmp_path / ".scrum-agent").mkdir()
        # Should not raise
        get_config_dir()


class TestGetConfigFile:
    """Tests for get_config_file() — returns ~/.scrum-agent/.env path."""

    def test_returns_dot_env_inside_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("scrum_agent.config.Path.home", lambda: tmp_path)
        result = get_config_file()
        assert result == tmp_path / ".scrum-agent" / ".env"


class TestLoadUserConfig:
    """Tests for load_user_config() — loads ~/.scrum-agent/.env without overriding existing vars."""

    def test_loads_vars_from_file(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".scrum-agent" / ".env"
        config_file.parent.mkdir()
        config_file.write_text("TEST_LOAD_VAR=hello-from-file\n")
        monkeypatch.setattr("scrum_agent.config.get_config_file", lambda: config_file)
        monkeypatch.delenv("TEST_LOAD_VAR", raising=False)
        load_user_config()
        assert os.environ.get("TEST_LOAD_VAR") == "hello-from-file"

    def test_does_not_override_existing_env_vars(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".scrum-agent" / ".env"
        config_file.parent.mkdir()
        config_file.write_text("TEST_OVERRIDE_VAR=from-file\n")
        monkeypatch.setattr("scrum_agent.config.get_config_file", lambda: config_file)
        monkeypatch.setenv("TEST_OVERRIDE_VAR", "from-shell")
        load_user_config()
        # Shell value should win (override=False)
        assert os.environ.get("TEST_OVERRIDE_VAR") == "from-shell"

    def test_noop_when_file_absent(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".scrum-agent" / ".env"
        monkeypatch.setattr("scrum_agent.config.get_config_file", lambda: config_file)
        # Should not raise even though the file doesn't exist
        load_user_config()


class TestGetSessionPruneDays:
    """Tests for get_session_prune_days() — SESSION_PRUNE_DAYS env var."""

    def test_default_30(self, monkeypatch):
        monkeypatch.delenv("SESSION_PRUNE_DAYS", raising=False)
        assert get_session_prune_days() == 30

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "60")
        assert get_session_prune_days() == 60

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "0")
        assert get_session_prune_days() == 0

    def test_negative_falls_back_to_30(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "-5")
        assert get_session_prune_days() == 30

    def test_invalid_falls_back_to_30(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "abc")
        assert get_session_prune_days() == 30
