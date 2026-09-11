"""Register decoding helpers for Modbus sensors."""

from __future__ import annotations

import struct
from typing import Union

from app.config import SensorConfig

_FORMATS = {
    "int16": ">h",
    "uint16": ">H",
    "int32": ">i",
    "uint32": ">I",
    "float32": ">f",
}


def registers_to_bytes(registers: list[int]) -> bytes:
    return b"".join((register & 0xFFFF).to_bytes(2, "big") for register in registers)


def decode_value(
    sensor: SensorConfig, registers: list[int]
) -> Union[int, float, list[Union[int, float]]]:
    """Decode raw registers according to the sensor's data type."""
    raw = registers_to_bytes(registers)
    fmt = sensor.struct_format if sensor.data_type == "custom" else _FORMATS[sensor.data_type]
    try:
        values = struct.unpack(fmt or "", raw)
    except struct.error as exc:
        raise ValueError(f"cannot decode sensor '{sensor.id}': {exc}") from exc
    if sensor.data_type == "custom":
        return values[0] if len(values) == 1 else list(values)
    return values[0]


def apply_scale(sensor: SensorConfig, value: Union[int, float, list[Union[int, float]]]):
    """Apply scale and precision; custom multi-value results pass through."""
    if isinstance(value, list):
        return value
    scaled = value * sensor.scale
    if sensor.precision is not None:
        scaled = round(scaled, sensor.precision)
    return scaled
