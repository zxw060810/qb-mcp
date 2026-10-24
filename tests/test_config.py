"""Tests for configuration loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qb_mcp.config import ConfigError, load_config


def write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / ".instances.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def base(**overrides):
    instance = {"url": "http://127.0.0.1:8080", "username": "admin", "password": "pw"}
    instance.update(overrides)
    return {"instances": {"a": instance}}


def test_loads_single_instance(tmp_path, monkeypatch):
    monkeypatch.delenv("QB_MCP_CONFIG", raising=False)
    path = write(tmp_path, base())
    config = load_config(path)
    assert config.names() == ["a"]
    assert config.get("a").base_url == "http://127.0.0.1:8080"
    assert config.get("a").display == "a"


def test_label_overrides_display(tmp_path):
    config = load_config(write(tmp_path, base(label="Home NAS")))
    assert config.get("a").display == "Home NAS"


def test_env_placeholder_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_QB_PASS", "s3cret")
    config = load_config(write(tmp_path, base(password="${MY_QB_PASS}")))
    assert config.get("a").password == "s3cret"


def test_missing_env_placeholder_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_NOT_SET", raising=False)
    with pytest.raises(ConfigError, match="NOPE_NOT_SET"):
        load_config(write(tmp_path, base(password="${NOPE_NOT_SET}")))


def test_password_from_file(tmp_path):
    secret = tmp_path / "pw.txt"
    secret.write_text("from-file\n", encoding="utf-8")
    config = load_config(write(tmp_path, base(password={"file": str(secret)})))
    assert config.get("a").password == "from-file"


def test_multiple_instances_with_different_credentials(tmp_path):
    payload = {
        "instances": {
            "one": {"url": "http://a:1", "username": "u1", "password": "p1"},
            "two": {"url": "http://b:2", "username": "u2", "password": "p2"},
        }
    }
    config = load_config(write(tmp_path, payload))
    assert config.get("one").username == "u1"
    assert config.get("two").username == "u2"
    assert config.get("two").password == "p2"


def test_missing_required_field_raises(tmp_path):
    payload = {"instances": {"a": {"url": "http://x:1", "username": "u"}}}
    with pytest.raises(ConfigError, match="password"):
        load_config(write(tmp_path, payload))


def test_url_scheme_is_validated(tmp_path):
    with pytest.raises(ConfigError, match="http"):
        load_config(write(tmp_path, base(url="ftp://x")))


def test_unknown_instance_lists_known_keys(tmp_path):
    config = load_config(write(tmp_path, base()))
    with pytest.raises(ConfigError, match="configured instances: a"):
        config.get("missing")


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="configuration file not found"):
        load_config(tmp_path / "nope.json")
