"""Tests for Modbus register decoding."""

from __future__ import annotations

import pytest

from app.config import SensorConfig
from app.modbus.decoder import apply_scale, decode_value


def test_int16_signed():
    sensor = SensorConfig(id="x", address=0, data_type="int16")
    assert decode_value(sensor, [0xFFFF]) == -1
    assert decode_value(sensor, [0x7FFF]) == 32767


def test_uint16():
    sensor = SensorConfig(id="x", address=0, data_type="uint16")
    assert decode_value(sensor, [0xFFFF]) == 65535


def test_int32():
    sensor = SensorConfig(id="x", address=0, data_type="int32")
    assert decode_value(sensor, [0xFFFF, 0xFFFF]) == -1
    assert decode_value(sensor, [0x0000, 0x0100]) == 256
    assert sensor.register_count == 2


def test_float32():
    sensor = SensorConfig(id="x", address=0, data_type="float32")
    assert decode_value(sensor, [0x3FC0, 0x0000]) == 1.5


def test_custom_multi_value():
    sensor = SensorConfig(id="x", address=0, data_type="custom", count=3, struct="=BBBBBB")
    assert decode_value(sensor, [0x0102, 0x0304, 0x0506]) == [1, 2, 3, 4, 5, 6]


def test_custom_single_value():
    sensor = SensorConfig(id="x", address=0, data_type="custom", count=1, struct=">H")
    assert decode_value(sensor, [1234]) == 1234


def test_scale_and_precision():
    sensor = SensorConfig(id="x", address=0, data_type="int16", scale=0.1, precision=1)
    assert apply_scale(sensor, 123) == 12.3


def test_scale_ignores_custom_lists():
    sensor = SensorConfig(id="x", address=0, data_type="custom", count=2, struct="=BBBB", scale=2)
    assert apply_scale(sensor, [1, 2, 3, 4]) == [1, 2, 3, 4]


def test_decode_error():
    sensor = SensorConfig(id="x", address=0, data_type="custom", count=1, struct="=I")
    with pytest.raises(ValueError, match="cannot decode"):
        decode_value(sensor, [1])
