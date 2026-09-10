"""REST API and WebSocket tests against a fake Modbus device."""

from __future__ import annotations

import time

import pytest
from conftest import TOKEN, FakeModbusDevice
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_without_auth(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["entity_count"] == 4
    assert body["devices"] == [
        {"name": "test_device", "host": "127.0.0.1", "port": 502, "connected": True}
    ]


def test_entities_require_token(client):
    assert client.get("/api/entities").status_code == 401
    assert (
        client.get("/api/entities", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )


def test_list_entities(client, auth_headers):
    response = client.get("/api/entities", headers=auth_headers)
    assert response.status_code == 200
    ids = {entity["entity_id"] for entity in response.json()}
    assert ids == {"switch.lock", "switch.fan", "sensor.warning", "sensor.timer"}


def test_filter_by_domain(client, auth_headers):
    response = client.get("/api/entities?domain=switch", headers=auth_headers)
    assert {entity["entity_id"] for entity in response.json()} == {
        "switch.lock",
        "switch.fan",
    }


def test_get_single_entity_and_404(client, auth_headers):
    response = client.get("/api/entities/sensor.warning", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Warning"
    assert client.get("/api/entities/sensor.nope", headers=auth_headers).status_code == 404


def test_switch_state_initialized_on_startup(client, auth_headers):
    response = client.get("/api/entities/switch.lock", headers=auth_headers)
    assert response.json()["state"] == "off"
    assert response.json()["available"] is True


def test_set_coil_switch(client, auth_headers, devices):
    response = client.post(
        "/api/entities/switch.lock/set", json={"state": "on"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["state"] == "on"
    assert devices[0].coils[2] is True

    response = client.post(
        "/api/entities/switch.lock/set", json={"state": "off"}, headers=auth_headers
    )
    assert response.json()["state"] == "off"
    assert devices[0].coils[2] is False


def test_set_holding_switch_with_verify(client, auth_headers, devices):
    response = client.post(
        "/api/entities/switch.fan/set", json={"state": "on"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["state"] == "on"
    assert devices[0].registers[1] == 1

    response = client.post(
        "/api/entities/switch.fan/set", json={"state": "off"}, headers=auth_headers
    )
    assert response.json()["state"] == "off"
    assert devices[0].registers[1] == 0


def test_set_sensor_rejected(client, auth_headers):
    response = client.post(
        "/api/entities/sensor.warning/set", json={"state": "on"}, headers=auth_headers
    )
    assert response.status_code == 400


def test_set_unknown_entity(client, auth_headers):
    response = client.post(
        "/api/entities/switch.nope/set", json={"state": "on"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_invalid_state_payload(client, auth_headers):
    response = client.post(
        "/api/entities/switch.lock/set", json={"state": "maybe"}, headers=auth_headers
    )
    assert response.status_code == 422


def test_modbus_failure_returns_502(client, auth_headers, devices):
    devices[0].fail = True
    response = client.post(
        "/api/entities/switch.lock/set", json={"state": "on"}, headers=auth_headers
    )
    assert response.status_code == 502
    entity = client.get("/api/entities/switch.lock", headers=auth_headers).json()
    assert entity["available"] is False


def test_sensor_values_polled_on_startup(config, devices):
    def factory(device_config):
        device = FakeModbusDevice(device_config)
        device.registers.update({15: -7, 17: 0x0102, 18: 0x0304, 19: 0x0506})
        devices.append(device)
        return device

    app = create_app(config, device_factory=factory)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        warning = client.get("/api/entities/sensor.warning", headers=headers).json()
        assert warning["state"] == -7
        timer = client.get("/api/entities/sensor.timer", headers=headers).json()
        assert timer["state"] == [1, 2, 3, 4, 5, 6]


def test_websocket_snapshot_and_updates(client, auth_headers):
    with client.websocket_connect(f"/ws?token={TOKEN}") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "snapshot"
        assert len(snapshot["entities"]) == 4

        client.post(
            "/api/entities/switch.lock/set", json={"state": "on"}, headers=auth_headers
        )

        update = websocket.receive_json()
        assert update["type"] == "state_changed"
        assert update["entity"]["entity_id"] == "switch.lock"
        assert update["entity"]["state"] == "on"


def test_websocket_rejects_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=nope"):
            pass


def test_websocket_handler_cleans_up_on_disconnect(client):
    system = client.app.state.system
    with client.websocket_connect(f"/ws?token={TOKEN}") as websocket:
        websocket.receive_json()
        assert system.store.subscriber_count == 1

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and system.store.subscriber_count:
        time.sleep(0.01)
    assert system.store.subscriber_count == 0
