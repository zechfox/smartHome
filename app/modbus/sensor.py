"""Periodic polling of Modbus sensor registers."""

from __future__ import annotations

import asyncio
import logging

from app.config import SensorConfig
from app.modbus.client import ModbusDevice, ModbusError
from app.modbus.decoder import apply_scale, decode_value
from app.state import StateStore

logger = logging.getLogger(__name__)


class SensorPoller:
    def __init__(self, device: ModbusDevice, sensor: SensorConfig, store: StateStore) -> None:
        self.device = device
        self.sensor = sensor
        self.store = store
        self.entity_id = f"sensor.{sensor.id}"

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.sensor.scan_interval)
            await self.poll_once()

    async def poll_once(self) -> None:
        entity = self.store.get(self.entity_id)
        try:
            registers = await self.device.read(
                "holding", self.sensor.address, count=self.sensor.register_count
            )
            value = apply_scale(self.sensor, decode_value(self.sensor, registers))
            await self.store.set_state(self.entity_id, value, available=True)
            if entity is not None and not entity.available:
                logger.info("%s is back online", self.entity_id)
        except (ModbusError, ValueError) as exc:
            await self.store.set_availability(self.entity_id, False)
            if entity is None or entity.available:
                logger.warning("%s unavailable: %s", self.entity_id, exc)
