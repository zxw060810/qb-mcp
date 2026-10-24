"""Configuration loading for qb-mcp.

Reads ``.instances.json`` (or a path from ``QB_MCP_CONFIG``), expands
``${ENV_VAR}`` placeholders, and validates the result.

Design notes
------------
* The real config file is git-ignored; ``instances.example.json`` is the template.
* Every instance carries its own credentials, so instances may belong to
  different qBittorrent accounts.
* A password may be a literal string, ``${ENV_VAR}``, or ``{"file": "/path"}``.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
CONFIG_ENV_VAR = "QB_MCP_CONFIG"
DEFAULT_CONFIG_NAMES = (".instances.json", "instances.json")


class ConfigError(RuntimeError):
    """Raised when the configuration is missing, unreadable or invalid."""


@dataclass(frozen=True)
class InstanceConfig:
    """One qBittorrent WebUI endpoint."""

    name: str
    url: str
    username: str
    password: str
    label: str = ""
    verify_ssl: bool = True
    timeout: float = 8.0

    @property
    def display(self) -> str:
        """Human-facing name, falling back to the key."""
        return self.label or self.name

    @property
    def base_url(self) -> str:
        """URL without a trailing slash."""
        return self.url.rstrip("/")


@dataclass
class AppConfig:
    """All configured instances."""

    instances: dict[str, InstanceConfig] = field(default_factory=dict)
    source: Path | None = None

    def names(self) -> list[str]:
        return sorted(self.instances)

    def get(self, name: str) -> InstanceConfig:
        try:
            return self.instances[name]
        except KeyError:
            known = ", ".join(self.names()) or "(none)"
            raise ConfigError(
                f"unknown instance {name!r}; configured instances: {known}"
            ) from None


def _warn_if_world_readable(path: Path) -> None:
    """Warn when the config file is readable by group/others."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(
            f"WARNING: {path} is readable by group/others; run: chmod 600 {path}",
            file=sys.stderr,
        )


def _expand(value: str) -> str:
    """Expand ``${VAR}`` placeholders from the environment."""

    def repl(match: re.Match[str]) -> str:
        var = match.group(1)
        found = os.environ.get(var)
        if found is None:
            raise ConfigError(f"environment variable {var!r} is not set")
        return found

    return ENV_PATTERN.sub(repl, value)


def _resolve_secret(raw: Any, field_name: str, instance: str) -> str:
    """Turn a password field into a plain string."""
    if isinstance(raw, str):
        return _expand(raw)
    if isinstance(raw, dict) and "file" in raw:
        path = Path(_expand(str(raw["file"]))).expanduser()
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"{instance}.{field_name}: cannot read {path}: {exc}") from exc
    raise ConfigError(
        f"{instance}.{field_name} must be a string, \"${{VAR}}\" or {{\"file\": \"...\"}}"
    )


def _parse_instance(name: str, raw: dict[str, Any]) -> InstanceConfig:
    for required in ("url", "username", "password"):
        if required not in raw:
            raise ConfigError(f"instance {name!r} is missing required field {required!r}")

    url = _expand(str(raw["url"]))
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"instance {name!r}: url must start with http:// or https://")

    return InstanceConfig(
        name=name,
        url=url,
        username=_expand(str(raw["username"])),
        password=_resolve_secret(raw["password"], "password", name),
        label=str(raw.get("label") or ""),
        verify_ssl=bool(raw.get("verify_ssl", True)),
        timeout=float(raw.get("timeout", 8.0)),
    )


def find_config_path(explicit: str | Path | None = None) -> Path:
    """Locate the config file."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(Path(name).expanduser() for name in DEFAULT_CONFIG_NAMES)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(c) for c in candidates)
    raise ConfigError(
        "no configuration file found. Copy instances.example.json to "
        f".instances.json and fill it in. Searched: {searched}"
    )


def load_config(explicit: str | Path | None = None) -> AppConfig:
    """Load and validate the configuration."""
    path = find_config_path(explicit)
    _warn_if_world_readable(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc

    raw_instances = data.get("instances")
    if not isinstance(raw_instances, dict) or not raw_instances:
        raise ConfigError(f"{path}: 'instances' must be a non-empty object")

    instances = {
        name: _parse_instance(name, raw if isinstance(raw, dict) else {})
        for name, raw in raw_instances.items()
    }
    return AppConfig(instances=instances, source=path)
