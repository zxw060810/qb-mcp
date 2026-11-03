"""Config discovery and error-reporting branches.

The happy paths live in ``test_config.py``. This file covers the paths that
only run when something is wrong: a missing candidate, a broken JSON file, an
unreadable password file and a config that is world-readable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qb_mcp import config as cfg
from qb_mcp.config import ConfigError

VALID = {
    "instances": {
        "home": {"url": "http://127.0.0.1:8080", "username": "admin", "password": "x"},
    }
}


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_environment_variable_wins_over_the_default_name(tmp_path, monkeypatch) -> None:
    target = _write(tmp_path / "custom.json", VALID)
    monkeypatch.setenv(cfg.CONFIG_ENV_VAR, str(target))

    assert cfg.find_config_path() == target


def test_default_name_is_used_when_no_env_is_set(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(cfg.CONFIG_ENV_VAR, raising=False)
    for name in cfg.DEFAULT_CONFIG_NAMES[1:]:
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_NAMES", [cfg.DEFAULT_CONFIG_NAMES[0]])
        break
    target = _write(tmp_path / cfg.DEFAULT_CONFIG_NAMES[0], VALID)
    monkeypatch.chdir(tmp_path)

    assert cfg.find_config_path() == Path(cfg.DEFAULT_CONFIG_NAMES[0])


def test_missing_everywhere_lists_what_was_searched(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(cfg.CONFIG_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        cfg.find_config_path()

    assert "no configuration file found" in str(excinfo.value)
    assert "Searched:" in str(excinfo.value)


def test_invalid_json_is_reported_with_the_path(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        cfg.load_config(bad)

    assert "invalid JSON" in str(excinfo.value)


def test_unreadable_file_is_reported(tmp_path, monkeypatch) -> None:
    target = _write(tmp_path / "ok.json", VALID)

    def boom(_self, *_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)

    with pytest.raises(ConfigError) as excinfo:
        cfg.load_config(target)

    assert "cannot read" in str(excinfo.value)


@pytest.mark.parametrize("payload", [{}, {"instances": {}}, {"instances": []}])
def test_instances_must_be_a_non_empty_object(tmp_path, payload) -> None:
    target = _write(tmp_path / "x.json", payload)

    with pytest.raises(ConfigError) as excinfo:
        cfg.load_config(target)

    assert "'instances' must be a non-empty object" in str(excinfo.value)


def test_password_file_that_cannot_be_read_is_reported(tmp_path) -> None:
    payload = {
        "instances": {
            "home": {
                "url": "http://127.0.0.1:8080",
                "username": "admin",
                "password": {"file": str(tmp_path / "missing.pass")},
            }
        }
    }
    target = _write(tmp_path / "x.json", payload)

    with pytest.raises(ConfigError) as excinfo:
        cfg.load_config(target)

    assert "cannot read" in str(excinfo.value)


def test_world_readable_config_emits_a_warning(tmp_path, capsys) -> None:
    target = _write(tmp_path / "loose.json", VALID)
    os.chmod(target, 0o644)

    cfg._warn_if_world_readable(target)

    assert "readable by group/others" in capsys.readouterr().err


def test_stat_failure_is_silently_ignored(monkeypatch) -> None:
    def boom(_self):
        raise OSError("gone")

    monkeypatch.setattr(Path, "stat", boom)

    cfg._warn_if_world_readable(Path("/definitely/not/here"))


def test_password_with_an_unsupported_shape_is_rejected(tmp_path) -> None:
    payload = {
        "instances": {
            "home": {"url": "http://127.0.0.1:8080", "username": "admin", "password": 42}
        }
    }
    target = _write(tmp_path / "x.json", payload)

    with pytest.raises(ConfigError) as excinfo:
        cfg.load_config(target)

    assert "must be a string" in str(excinfo.value)
