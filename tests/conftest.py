"""Shared fixtures and a fake Modbus device for tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import (
    AppConfig,
    ModbusDeviceConfig,
    SensorConfig,
    ServerConfig,
    SwitchConfig,
)
from app.main import create_app
from app.modbus.client import ModbusError

TOKEN = "test-token"


class FakeModbusDevice:
    """Duck-typed stand-in for ModbusDevice."""

    def __init__(self, config: ModbusDeviceConfig) -> None:
        self.config = config
        self.registers: dict[int, int] = {}
        self.coils: dict[int, bool] = {}
        self.writes: list[tuple[str, int, int]] = []
        self.fail = False

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def connected(self) -> bool:
        return not self.fail

    async def read(self, kind: str, address: int, count: int = 1) -> list[int]:
        if self.fail:
            raise ModbusError("simulated failure")
        if kind == "coil":
            return [
                1 if self.coils.get(address + offset, False) else 0
                for offset in range(count)
            ]
        return [self.registers.get(address + offset, 0) for offset in range(count)]

    async def write_coil(self, address: int, value: bool) -> None:
        if self.fail:
            raise ModbusError("simulated failure")
        self.coils[address] = bool(value)
        self.writes.append(("coil", address, int(value)))

    async def write_register(self, address: int, value: int) -> None:
        if self.fail:
            raise ModbusError("simulated failure")
        self.registers[address] = value
        self.writes.append(("holding", address, value))

    async def close(self) -> None:
        pass


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        server=ServerConfig(api_token=TOKEN),
        modbus=[
            ModbusDeviceConfig(
                name="test_device",
                host="127.0.0.1",
                switches=[
                    SwitchConfig(
                        id="lock", name="Lock", type="coil", address=2, verify_delay=0
                    ),
                    SwitchConfig(
                        id="fan",
                        name="Fan",
                        type="holding",
                        address=1,
                        verify_delay=0.01,
                    ),
                ],
                sensors=[
                    SensorConfig(
                        id="warning",
                        name="Warning",
                        address=15,
                        data_type="int16",
                        scan_interval=3600,
                    ),
                    SensorConfig(
                        id="timer",
                        name="Timer",
                        address=17,
                        data_type="custom",
                        count=3,
                        struct="=BBBBBB",
                        scan_interval=3600,
                    ),
                ],
            )
        ],
    )


@pytest.fixture
def devices() -> list[FakeModbusDevice]:
    return []


def make_app(config: AppConfig, devices: list[FakeModbusDevice]):
    def factory(device_config: ModbusDeviceConfig) -> FakeModbusDevice:
        device = FakeModbusDevice(device_config)
        devices.append(device)
        return device

    return create_app(config, device_factory=factory)


@pytest.fixture
def app(config: AppConfig, devices: list[FakeModbusDevice]):
    return make_app(config, devices)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}
