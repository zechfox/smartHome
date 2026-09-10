"""Wires configuration, state store and Modbus entities together."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.config import AppConfig, ModbusDeviceConfig
from app.modbus.client import ModbusDevice
from app.modbus.sensor import SensorPoller
from app.modbus.switch import ModbusSwitch
from app.state import Entity, StateStore

logger = logging.getLogger(__name__)

DeviceFactory = Callable[[ModbusDeviceConfig], ModbusDevice]


class SmartHomeSystem:
    def __init__(
        self,
        config: AppConfig,
        device_factory: DeviceFactory | None = None,
    ) -> None:
        self.config = config
        self.store = StateStore()
        self.devices: dict[str, ModbusDevice] = {}
        self.switches: dict[str, ModbusSwitch] = {}
        self.sensors: dict[str, SensorPoller] = {}
        self._device_factory = device_factory or ModbusDevice
        self._tasks: list[asyncio.Task[None]] = []
        self.shutdown_event = asyncio.Event()

    async def start(self) -> None:
        self._build_entities()
        await self.refresh_switches()
        await self.poll_all_once()
        for switch in self.switches.values():
            if switch.config.scan_interval:
                self._tasks.append(
                    asyncio.create_task(switch.run(), name=f"poll:{switch.entity_id}")
                )
        for poller in self.sensors.values():
            self._tasks.append(
                asyncio.create_task(poller.run(), name=f"poll:{poller.entity_id}")
            )

    async def stop(self) -> None:
        self.shutdown_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await asyncio.gather(
            *(device.close() for device in self.devices.values()),
            return_exceptions=True,
        )

    async def poll_all_once(self) -> None:
        await asyncio.gather(*(poller.poll_once() for poller in self.sensors.values()))

    async def refresh_switches(self) -> None:
        await asyncio.gather(*(switch.refresh() for switch in self.switches.values()))

    async def set_switch(self, entity_id: str, on: bool) -> Entity:
        switch = self.switches[entity_id]
        if on:
            await switch.turn_on()
        else:
            await switch.turn_off()
        entity = self.store.get(entity_id)
        assert entity is not None
        return entity

    def device_status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": device.name,
                "host": device.config.host,
                "port": device.config.port,
                "connected": device.connected,
            }
            for device in self.devices.values()
        ]

    def _build_entities(self) -> None:
        for device_config in self.config.modbus:
            device = self._device_factory(device_config)
            self.devices[device_config.name] = device

            for switch_config in device_config.switches:
                entity_id = f"switch.{switch_config.id}"
                self.store.add(
                    Entity(
                        entity_id=entity_id,
                        name=switch_config.display_name,
                        domain="switch",
                        attributes={
                            "device": device_config.name,
                            "register_type": switch_config.type,
                            "address": switch_config.address,
                        },
                    )
                )
                self.switches[entity_id] = ModbusSwitch(device, switch_config, self.store)

            for sensor_config in device_config.sensors:
                entity_id = f"sensor.{sensor_config.id}"
                attributes: dict[str, Any] = {
                    "device": device_config.name,
                    "address": sensor_config.address,
                    "data_type": sensor_config.data_type,
                }
                if sensor_config.unit:
                    attributes["unit_of_measurement"] = sensor_config.unit
                self.store.add(
                    Entity(
                        entity_id=entity_id,
                        name=sensor_config.display_name,
                        domain="sensor",
                        attributes=attributes,
                    )
                )
                self.sensors[entity_id] = SensorPoller(device, sensor_config, self.store)
