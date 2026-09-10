"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest

from app.config import AppConfig, ConfigError, SensorConfig, load_config

VALID = """
server:
  api_token: secret
modbus:
  - name: dev
    host: 10.0.0.1
    switches:
      - id: sw1
        address: 1
    sensors:
      - id: temp
        address: 2
        data_type: int16
"""


def write_config(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_config(tmp_path):
    config = load_config(write_config(tmp_path, VALID))
    assert config.server.api_token == "secret"
    assert config.modbus[0].switches[0].id == "sw1"
    assert config.modbus[0].sensors[0].data_type == "int16"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_missing_token_raises(tmp_path):
    text = VALID.replace("api_token: secret", 'api_token: ""')
    with pytest.raises(ConfigError, match="api_token"):
        load_config(write_config(tmp_path, text))


def test_token_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_HOME_TOKEN", "from-env")
    config = load_config(write_config(tmp_path, VALID))
    assert config.server.api_token == "from-env"


def test_invalid_yaml_raises(tmp_path):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(write_config(tmp_path, "modbus: [unclosed"))


def test_duplicate_ids_rejected():
    raw = {
        "server": {"api_token": "x"},
        "modbus": [
            {
                "name": "a",
                "host": "1.1.1.1",
                "switches": [
                    {"id": "dup", "address": 1},
                    {"id": "dup", "address": 2},
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="unique"):
        AppConfig.model_validate(raw)


def test_duplicate_device_names_rejected():
    raw = {
        "server": {"api_token": "x"},
        "modbus": [
            {"name": "a", "host": "1.1.1.1"},
            {"name": "a", "host": "1.1.1.2"},
        ],
    }
    with pytest.raises(ValueError, match="unique"):
        AppConfig.model_validate(raw)


def test_custom_sensor_requires_count_and_struct():
    with pytest.raises(ValueError, match="count"):
        SensorConfig(id="x", address=1, data_type="custom")
    with pytest.raises(ValueError, match="struct"):
        SensorConfig(id="x", address=1, data_type="custom", count=2)


def test_struct_alias_and_register_count():
    sensor = SensorConfig(id="x", address=1, data_type="custom", count=3, struct="=BBBBBB")
    assert sensor.struct_format == "=BBBBBB"
    assert sensor.register_count == 3
    assert SensorConfig(id="y", address=1, data_type="int32").register_count == 2
    assert SensorConfig(id="z", address=1).register_count == 1
