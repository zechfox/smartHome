"""Modbus switch entities (coil or holding register)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

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
        self._off_task: Optional[asyncio.Task[None]] = None

    def notify_change(self) -> None:
        self._wake.set()

    def cancel_pending_off(self) -> None:
        if self._off_task is not None:
            self._off_task.cancel()
            self._off_task = None

    async def flush_pending_off(self) -> None:
        """Turn off now if an auto-off is pending (used on shutdown)."""
        if self._off_task is None:
            return
        self.cancel_pending_off()
        try:
            await self._write(False)
        except ModbusError:
            pass

    async def turn_on(self, duration: Optional[float] = None) -> None:
        """Switch on, optionally switching off again after ``duration`` seconds.

        ``duration`` overrides the configured ``pulse_duration``. The countdown
        starts when the command is issued, not after the verify read.
        """
        self.cancel_pending_off()
        effective = duration if duration is not None else self.config.pulse_duration
        loop = asyncio.get_running_loop()
        deadline = loop.time() + effective if effective else None
        await self._write(True)
        if deadline is not None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await self._write(False)
            else:
                self._off_task = asyncio.create_task(self._auto_off(remaining))

    async def turn_off(self) -> None:
        self.cancel_pending_off()
        await self._write(False)

    async def _auto_off(self, duration: float) -> None:
        try:
            await asyncio.sleep(duration)
            await self._write(False)
        except asyncio.CancelledError:
            raise
        except ModbusError:
            pass
        finally:
            self._off_task = None

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
