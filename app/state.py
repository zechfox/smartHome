"""In-memory entity state store with subscriber notifications."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Domain = Literal["switch", "sensor"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Entity:
    entity_id: str
    name: str
    domain: Domain
    state: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    available: bool = True
    last_changed: datetime | None = None
    last_updated: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "domain": self.domain,
            "state": self.state,
            "attributes": self.attributes,
            "available": self.available,
            "last_changed": self.last_changed.isoformat() if self.last_changed else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class StateStore:
    """Holds every entity and broadcasts changes to subscribers."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    def add(self, entity: Entity) -> None:
        if entity.entity_id in self._entities:
            raise ValueError(f"duplicate entity id: {entity.entity_id}")
        entity.last_changed = entity.last_updated = _now()
        self._entities[entity.entity_id] = entity

    def get(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def all(self) -> list[Entity]:
        return list(self._entities.values())

    async def set_state(
        self,
        entity_id: str,
        state: Any,
        *,
        attributes: dict[str, Any] | None = None,
        available: bool = True,
    ) -> Entity:
        async with self._lock:
            entity = self._entities[entity_id]
            now = _now()
            if entity.state != state:
                entity.last_changed = now
            entity.state = state
            if attributes:
                entity.attributes.update(attributes)
            entity.available = available
            entity.last_updated = now
        self._notify(entity)
        return entity

    async def set_availability(self, entity_id: str, available: bool) -> Entity:
        async with self._lock:
            entity = self._entities[entity_id]
            if entity.available == available:
                return entity
            entity.available = available
            entity.last_updated = _now()
        self._notify(entity)
        return entity

    async def update_entity_meta(
        self,
        entity_id: str,
        *,
        name: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Entity:
        async with self._lock:
            entity = self._entities[entity_id]
            if name is not None:
                entity.name = name
            if attributes is not None:
                entity.attributes = attributes
            entity.last_updated = _now()
        self._notify(entity)
        return entity

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _notify(self, entity: Entity) -> None:
        payload = {"type": "state_changed", "entity": entity.to_dict()}
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race safety
                    pass
            queue.put_nowait(payload)
