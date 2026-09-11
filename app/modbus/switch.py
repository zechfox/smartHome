"""Modbus switch entities (coil or holding register)."""

from __future__ import annotations

import asyncio
import logging

from app.config import SwitchConfig
from app.modbus.client import ModbusDevice, ModbusError
from app.state import StateStore

logger = logging.getLogger(__name__)


class ModbusSwitch:
    def __init__(self, device: ModbusDevice, config: SwitchConfig, store: StateStore) -> None:
        self.device = device
        self.config = config
        self.store = store
        self.entity_id = f"switch.{config.id}"
        self._wake = asyncio.Event()

    def notify_change(self) -> None:
        self._wake.set()

    async def turn_on(self) -> None:
        await self._write(True)

    async def turn_off(self) -> None:
        await self._write(False)

    async def run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.config.scan_interval or 30.0
                )
            except asyncio.TimeoutError:
                pass
            finally:
                self._wake.clear()
            await self.refresh()

    async def refresh(self) -> None:
        """Read the current state from the device without writing."""
        try:
            is_on = await self._read_state()
            await self.store.set_state(
                self.entity_id, "on" if is_on else "off", available=True
            )
        except ModbusError as exc:
            await self.store.set_availability(self.entity_id, False)
            logger.debug("refresh of %s failed: %s", self.entity_id, exc)

    async def _write(self, on: bool) -> None:
        value = self.config.command_on if on else self.config.command_off
        try:
            if self.config.type == "coil":
                await self.device.write_coil(self.config.address, bool(value))
            else:
                await self.device.write_register(self.config.address, value)

            if self.config.verify_delay > 0:
                await asyncio.sleep(self.config.verify_delay)
                is_on = await self._read_state()
                await self.store.set_state(
                    self.entity_id, "on" if is_on else "off", available=True
                )
            else:
                await self.store.set_state(
                    self.entity_id, "on" if on else "off", available=True
                )
        except ModbusError:
            await self.store.set_availability(self.entity_id, False)
            raise

    async def _read_state(self) -> bool:
        registers = await self.device.read(
            self.config.type, self.config.address, count=1
        )
        expected_on = 1 if self.config.type == "coil" else self.config.command_on
        return registers[0] == expected_on
