"""Tests for the in-memory state store."""

from __future__ import annotations

import asyncio

import pytest

from app.state import Entity, StateStore


def test_add_and_get():
    store = StateStore()
    entity = Entity(entity_id="sensor.a", name="A", domain="sensor")
    store.add(entity)
    assert store.get("sensor.a") is entity
    assert store.all() == [entity]
    assert entity.last_changed is not None
    assert entity.last_updated is not None


def test_duplicate_add_raises():
    store = StateStore()
    store.add(Entity(entity_id="sensor.a", name="A", domain="sensor"))
    with pytest.raises(ValueError, match="duplicate"):
        store.add(Entity(entity_id="sensor.a", name="A", domain="sensor"))


async def test_set_state_notifies_subscribers():
    store = StateStore()
    store.add(Entity(entity_id="sensor.a", name="A", domain="sensor"))
    queue = store.subscribe()

    await store.set_state("sensor.a", 42)

    payload = await asyncio.wait_for(queue.get(), timeout=1)
    assert payload["type"] == "state_changed"
    assert payload["entity"]["state"] == 42
    assert payload["entity"]["available"] is True


async def test_set_state_tracks_last_changed_only_on_change():
    store = StateStore()
    store.add(Entity(entity_id="sensor.a", name="A", domain="sensor"))

    await store.set_state("sensor.a", 1)
    changed = store.get("sensor.a").last_changed

    await store.set_state("sensor.a", 1)
    assert store.get("sensor.a").last_changed == changed

    await store.set_state("sensor.a", 2)
    assert store.get("sensor.a").last_changed != changed


async def test_set_availability():
    store = StateStore()
    store.add(Entity(entity_id="sensor.a", name="A", domain="sensor"))
    queue = store.subscribe()

    await store.set_availability("sensor.a", False)
    assert store.get("sensor.a").available is False
    payload = await asyncio.wait_for(queue.get(), timeout=1)
    assert payload["entity"]["available"] is False

    await store.set_availability("sensor.a", False)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.05)


async def test_unsubscribe():
    store = StateStore()
    store.add(Entity(entity_id="sensor.a", name="A", domain="sensor"))
    queue = store.subscribe()
    store.unsubscribe(queue)
    await store.set_state("sensor.a", 1)
    assert queue.empty()


async def test_update_entity_meta_replaces_attributes():
    store = StateStore()
    entity = Entity(
        entity_id="sensor.a",
        name="A",
        domain="sensor",
        attributes={"device": "d", "unit_of_measurement": "H"},
    )
    store.add(entity)
    entity.available = False
    queue = store.subscribe()

    updated = await store.update_entity_meta(
        "sensor.a", name="B", attributes={"device": "d", "address": 1}
    )

    assert updated is entity
    assert updated.name == "B"
    assert updated.attributes == {"device": "d", "address": 1}
    assert "unit_of_measurement" not in updated.attributes
    assert updated.available is False
    assert updated.state is None

    payload = await asyncio.wait_for(queue.get(), timeout=1)
    assert payload["type"] == "state_changed"
    assert payload["entity"]["attributes"] == {"device": "d", "address": 1}
    assert payload["entity"]["available"] is False
