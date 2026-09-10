"""Async Modbus TCP client wrapper with reconnect backoff and serialized access."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from pymodbus.client import AsyncModbusTcpClient

from app.config import ModbusDeviceConfig

logger = logging.getLogger(__name__)


class ModbusError(Exception):
    """Raised when a Modbus operation fails."""


def _slave_keyword() -> str:
    """pymodbus renamed ``slave`` to ``device_id``; support both."""
    parameters = inspect.signature(AsyncModbusTcpClient.read_holding_registers).parameters
    return "device_id" if "device_id" in parameters else "slave"


_SLAVE_KEYWORD = _slave_keyword()


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


class ModbusDevice:
    """One TCP connection to a Modbus device, shared by its entities."""

    def __init__(self, config: ModbusDeviceConfig) -> None:
        self.config = config
        self._client = AsyncModbusTcpClient(
            config.host,
            port=config.port,
            timeout=config.timeout,
        )
        self._lock = asyncio.Lock()
        self._next_connect_at = 0.0

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def connected(self) -> bool:
        return bool(self._client.connected)

    async def read(self, kind: str, address: int, count: int = 1) -> list[int]:
        async with self._lock:
            await self._ensure_connected()
            slave = {_SLAVE_KEYWORD: self.config.slave}
            try:
                if kind == "coil":
                    result = await self._client.read_coils(address, count=count, **slave)
                    if result.isError():
                        raise ModbusError(f"read_coils({address}, {count}) failed: {result}")
                    return [int(bit) for bit in result.bits[:count]]
                if kind != "holding":
                    raise ValueError(f"unsupported register kind: {kind}")
                result = await self._client.read_holding_registers(address, count=count, **slave)
                if result.isError():
                    raise ModbusError(
                        f"read_holding_registers({address}, {count}) failed: {result}"
                    )
                return list(result.registers)
            except ModbusError:
                raise
            except Exception as exc:
                await self._disconnect()
                raise ModbusError(f"read {kind} @{address} failed: {exc}") from exc

    async def write_coil(self, address: int, value: bool) -> None:
        async with self._lock:
            await self._ensure_connected()
            try:
                result = await self._client.write_coil(
                    address, value, **{_SLAVE_KEYWORD: self.config.slave}
                )
                if result.isError():
                    raise ModbusError(f"write_coil({address}, {value}) failed: {result}")
            except ModbusError:
                raise
            except Exception as exc:
                await self._disconnect()
                raise ModbusError(f"write coil @{address} failed: {exc}") from exc

    async def write_register(self, address: int, value: int) -> None:
        async with self._lock:
            await self._ensure_connected()
            try:
                result = await self._client.write_register(
                    address, value, **{_SLAVE_KEYWORD: self.config.slave}
                )
                if result.isError():
                    raise ModbusError(f"write_register({address}, {value}) failed: {result}")
            except ModbusError:
                raise
            except Exception as exc:
                await self._disconnect()
                raise ModbusError(f"write register @{address} failed: {exc}") from exc

    async def close(self) -> None:
        await self._disconnect()

    async def _ensure_connected(self) -> None:
        if self._client.connected:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._next_connect_at:
            remaining = self._next_connect_at - now
            raise ModbusError(
                f"device '{self.name}' offline, retrying in {remaining:.1f}s"
            )
        try:
            connected = await self._client.connect()
        except Exception as exc:
            connected = False
            logger.debug("connect to %s failed: %s", self.name, exc)
        if not connected:
            self._next_connect_at = loop.time() + self.config.reconnect_interval
            raise ModbusError(
                f"cannot connect to {self.name} "
                f"({self.config.host}:{self.config.port})"
            )
        self._next_connect_at = 0.0

    async def _disconnect(self) -> None:
        try:
            await _maybe_await(self._client.close())
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.debug("closing %s failed: %s", self.name, exc)
