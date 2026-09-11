"""Wires configuration, state store and Modbus entities together."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.config import (
    AppConfig,
    ModbusDeviceConfig,
    SensorConfig,
    SwitchConfig,
    save_config,
    validated_update,
)
from app.modbus.client import ModbusDevice
from app.modbus.sensor import SensorPoller
from app.modbus.switch import ModbusSwitch
from app.state import Entity, StateStore

logger = logging.getLogger(__name__)

DeviceFactory = Callable[[ModbusDeviceConfig], ModbusDevice]

SWITCH_FIELDS = frozenset(
    {"address", "command_on", "command_off", "verify_delay", "scan_interval", "name"}
)
SENSOR_FIELDS = frozenset({"address", "scan_interval", "scale", "precision", "unit", "name"})
DEVICE_FIELDS = frozenset({"host", "port", "slave", "timeout", "reconnect_interval"})


def _switch_attributes(device_name: str, cfg: SwitchConfig) -> dict[str, Any]:
    return {
        "device": device_name,
        "register_type": cfg.type,
        "address": cfg.address,
        "command_on": cfg.command_on,
        "command_off": cfg.command_off,
        "verify_delay": cfg.verify_delay,
        "scan_interval": cfg.scan_interval,
    }


def _sensor_attributes(device_name: str, cfg: SensorConfig) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "device": device_name,
        "address": cfg.address,
        "data_type": cfg.data_type,
        "scan_interval": cfg.scan_interval,
        "scale": cfg.scale,
        "precision": cfg.precision,
    }
    if cfg.unit:
        attributes["unit_of_measurement"] = cfg.unit
    return attributes


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

    def list_devices(self) -> list[dict[str, Any]]:
        return [
            {
                "name": device.name,
                "host": device.config.host,
                "port": device.config.port,
                "slave": device.config.slave,
                "timeout": device.config.timeout,
                "reconnect_interval": device.config.reconnect_interval,
                "connected": device.connected,
            }
            for device in self.devices.values()
        ]

    async def update_entity(self, entity_id: str, updates: dict[str, Any]) -> Entity:
        if entity_id.startswith("switch."):
            switch = self.switches[entity_id]
            if "scan_interval" in updates and updates["scan_interval"] is None:
                raise ValueError("scan_interval cannot be null for a switch")
            validated_update(switch.config, updates, allowed=SWITCH_FIELDS)
            attributes = _switch_attributes(switch.device.name, switch.config)
            await self.store.update_entity_meta(
                entity_id, name=switch.config.display_name, attributes=attributes
            )
            switch.notify_change()
        else:
            poller = self.sensors[entity_id]
            validated_update(poller.sensor, updates, allowed=SENSOR_FIELDS)
            attributes = _sensor_attributes(poller.device.name, poller.sensor)
            await self.store.update_entity_meta(
                entity_id, name=poller.sensor.display_name, attributes=attributes
            )
            poller.notify_change()

        save_config(self.config)
        entity = self.store.get(entity_id)
        assert entity is not None
        return entity

    async def update_device(self, device_name: str, updates: dict[str, Any]) -> dict[str, Any]:
        device = self.devices[device_name]
        validated_update(device.config, updates, allowed=DEVICE_FIELDS)
        rebuild = bool({"host", "port", "timeout"} & updates.keys())
        await device.reconfigure(rebuild)
        save_config(self.config)
        return {
            "name": device.name,
            "host": device.config.host,
            "port": device.config.port,
            "slave": device.config.slave,
            "timeout": device.config.timeout,
            "reconnect_interval": device.config.reconnect_interval,
            "connected": device.connected,
        }

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
                        attributes=_switch_attributes(device_config.name, switch_config),
                    )
                )
                self.switches[entity_id] = ModbusSwitch(device, switch_config, self.store)

            for sensor_config in device_config.sensors:
                entity_id = f"sensor.{sensor_config.id}"
                self.store.add(
                    Entity(
                        entity_id=entity_id,
                        name=sensor_config.display_name,
                        domain="sensor",
                        attributes=_sensor_attributes(device_config.name, sensor_config),
                    )
                )
                self.sensors[entity_id] = SensorPoller(device, sensor_config, self.store)
