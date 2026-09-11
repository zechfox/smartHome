"""Configuration models and YAML loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, model_validator

DEFAULT_CONFIG_PATH = Path("config.yaml")
ENV_CONFIG_PATH = "SMART_HOME_CONFIG"
ENV_TOKEN = "SMART_HOME_TOKEN"


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    api_token: str = ""
    shutdown_timeout: int = Field(default=10, ge=0)


class SwitchConfig(BaseModel):
    id: str
    name: str | None = None
    type: Literal["coil", "holding"] = "coil"
    address: int = Field(ge=0)
    command_on: int = 1
    command_off: int = 0
    verify_delay: float = Field(default=0.0, ge=0)
    scan_interval: float | None = Field(default=None, gt=0)

    @property
    def display_name(self) -> str:
        return self.name or self.id.replace("_", " ").title()


class SensorConfig(BaseModel):
    id: str
    name: str | None = None
    address: int = Field(ge=0)
    data_type: Literal["int16", "uint16", "int32", "uint32", "float32", "custom"] = "int16"
    count: int | None = Field(default=None, ge=1)
    struct_format: str | None = Field(default=None, alias="struct")
    unit: str | None = None
    scan_interval: float = Field(default=10.0, gt=0)
    scale: float = 1.0
    precision: int | None = Field(default=None, ge=0)

    model_config = {"populate_by_name": True}

    @property
    def display_name(self) -> str:
        return self.name or self.id.replace("_", " ").title()

    @property
    def register_count(self) -> int:
        if self.data_type == "custom":
            return self.count if self.count else 1
        return 2 if self.data_type in ("int32", "uint32", "float32") else 1

    @model_validator(mode="after")
    def _validate_custom(self) -> SensorConfig:
        if self.data_type == "custom":
            if not self.count:
                raise ValueError("data_type 'custom' requires 'count'")
            if not self.struct_format:
                raise ValueError("data_type 'custom' requires 'struct'")
        return self


class ModbusDeviceConfig(BaseModel):
    name: str
    host: str
    port: int = Field(default=502, ge=1, le=65535)
    slave: int = Field(default=1, ge=0, le=247)
    timeout: float = Field(default=5.0, gt=0)
    reconnect_interval: float = Field(default=5.0, gt=0)
    switches: list[SwitchConfig] = Field(default_factory=list)
    sensors: list[SensorConfig] = Field(default_factory=list)


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    modbus: list[ModbusDeviceConfig] = Field(default_factory=list)

    _source_path: Path | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> AppConfig:
        device_names = [device.name for device in self.modbus]
        if len(device_names) != len(set(device_names)):
            raise ValueError("modbus device names must be unique")

        for domain, attribute in (("switch", "switches"), ("sensor", "sensors")):
            entity_ids = [
                entity.id for device in self.modbus for entity in getattr(device, attribute)
            ]
            if len(entity_ids) != len(set(entity_ids)):
                raise ValueError(f"{domain} entity ids must be unique")
        return self


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and validate the YAML configuration.

    Path resolution order: explicit ``path`` argument, ``SMART_HOME_CONFIG``
    environment variable, then ``./config.yaml``. The API token may be
    overridden with the ``SMART_HOME_TOKEN`` environment variable.
    """
    env_path = os.environ.get(ENV_CONFIG_PATH)
    resolved = Path(path or env_path or DEFAULT_CONFIG_PATH)
    if not resolved.is_file():
        raise ConfigError(f"configuration file not found: {resolved}")

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {resolved}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"configuration root must be a mapping in {resolved}")

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {resolved}:\n{exc}") from exc

    token = os.environ.get(ENV_TOKEN) or config.server.api_token
    if not token:
        raise ConfigError(
            "server.api_token must not be empty (or set the SMART_HOME_TOKEN environment variable)"
        )
    config.server.api_token = token
    config._source_path = resolved
    return config


def validated_update(
    model: BaseModel, updates: dict[str, Any], *, allowed: set[str]
) -> None:
    """Validate an update against ``model`` and apply it in place.

    Mutates the same object (rather than replacing it) so any shared references
    held by pollers/devices remain valid. Unknown keys raise ``ValueError``.
    """
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")

    merged = model.model_dump() | updates
    validated = type(model).model_validate(merged)
    for key in updates:
        setattr(model, key, getattr(validated, key))


def save_config(config: AppConfig) -> None:
    """Persist only the ``modbus`` section back to the source YAML file.

    Everything above the top-level ``modbus:`` line (header comments and the
    ``server`` section) is preserved byte-for-byte; only the ``modbus`` list is
    re-serialized. The write is atomic (temp file + ``os.replace``).
    """
    path = config._source_path
    if path is None:
        raise RuntimeError("config source path is not set")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().startswith("modbus:"):
            break
    else:
        raise RuntimeError("modbus: section not found in config file")
    prefix = "\n".join(lines[:idx]) + "\n"

    modbus_data = [
        device.model_dump(mode="json", exclude_none=True, by_alias=True)
        for device in config.modbus
    ]
    body = yaml.safe_dump(modbus_data, sort_keys=False, default_flow_style=False)
    indented = "\n".join(
        ("  " + line) if line.strip() else "" for line in body.splitlines()
    )
    new_text = prefix + "modbus:\n" + indented + "\n"

    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(new_text, encoding="utf-8")
    os.replace(tmp_path, path)
