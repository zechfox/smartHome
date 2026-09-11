"""REST API and WebSocket tests against a fake Modbus device."""

from __future__ import annotations

import time

import pytest
from conftest import TOKEN, FakeModbusDevice, make_app
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


def test_attributes_expose_editable_fields_for_prefill(client, auth_headers):
    switch = client.get("/api/entities/switch.lock", headers=auth_headers).json()
    assert switch["attributes"]["address"] == 2
    assert switch["attributes"]["command_on"] == 1
    assert switch["attributes"]["command_off"] == 0
    assert switch["attributes"]["verify_delay"] == 0

    sensor = client.get("/api/entities/sensor.warning", headers=auth_headers).json()
    assert sensor["attributes"]["address"] == 15
    assert sensor["attributes"]["data_type"] == "int16"
    assert sensor["attributes"]["scan_interval"] == 3600


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


def test_loop_bound_primitives_are_created_lazily(config, devices):
    """Python 3.9 binds asyncio primitives to the loop that creates them."""
    app = make_app(config, devices)
    system = app.state.system
    assert system._shutdown_event is None
    assert system.store._lock is None


def test_websocket_handler_cleans_up_on_disconnect(client):
    system = client.app.state.system
    with client.websocket_connect(f"/ws?token={TOKEN}") as websocket:
        websocket.receive_json()
        assert system.store.subscriber_count == 1

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and system.store.subscriber_count:
        time.sleep(0.01)
    assert system.store.subscriber_count == 0


def test_list_devices(client, auth_headers):
    response = client.get("/api/devices", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "test_device",
            "host": "127.0.0.1",
            "port": 502,
            "slave": 1,
            "timeout": 5.0,
            "reconnect_interval": 5.0,
            "connected": True,
        }
    ]


def test_patch_sensor_param(client, auth_headers, config):
    response = client.patch(
        "/api/entities/sensor.warning", json={"scale": 2.0}, headers=auth_headers
    )
    assert response.status_code == 200
    assert config.modbus[0].sensors[0].scale == 2.0

    response = client.patch(
        "/api/entities/sensor.warning", json={"unit": "H"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["attributes"]["unit_of_measurement"] == "H"
    assert config.modbus[0].sensors[0].unit == "H"


def test_patch_switch_param(client, auth_headers, config):
    response = client.patch(
        "/api/entities/switch.lock", json={"scan_interval": 15}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["entity_id"] == "switch.lock"
    system = client.app.state.system
    assert system.switches["switch.lock"].config.scan_interval == 15
    assert config.modbus[0].switches[0].scan_interval == 15


def test_patch_device_param(client, auth_headers, config, devices):
    response = client.patch(
        "/api/devices/test_device", json={"host": "10.0.0.99"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["host"] == "10.0.0.99"
    assert devices[0].config.host == "10.0.0.99"
    assert config.modbus[0].host == "10.0.0.99"
    assert devices[0].reconfigured is True
    assert devices[0].reconfigures == [True]


def test_patch_device_slave_no_rebuild(client, auth_headers, devices):
    response = client.patch(
        "/api/devices/test_device", json={"slave": 2}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["slave"] == 2
    assert devices[0].reconfigures == [False]


def test_patch_unknown_entity_and_device(client, auth_headers):
    assert (
        client.patch(
            "/api/entities/sensor.nope", json={"scale": 2.0}, headers=auth_headers
        ).status_code
        == 404
    )
    assert (
        client.patch(
            "/api/devices/nope", json={"host": "x"}, headers=auth_headers
        ).status_code
        == 404
    )


def test_patch_invalid_value_and_unknown_field(client, auth_headers):
    assert (
        client.patch(
            "/api/entities/sensor.warning", json={"address": -1}, headers=auth_headers
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/entities/sensor.warning", json={"addres": 5}, headers=auth_headers
        ).status_code
        == 422
    )


def test_patch_empty_body(client, auth_headers):
    assert (
        client.patch(
            "/api/entities/sensor.warning", json={}, headers=auth_headers
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/devices/test_device", json={}, headers=auth_headers
        ).status_code
        == 422
    )


def test_patch_switch_scan_interval_null_rejected(client, auth_headers):
    assert (
        client.patch(
            "/api/entities/switch.lock", json={"scan_interval": None}, headers=auth_headers
        ).status_code
        == 422
    )


def test_persist_config_to_yaml(tmp_path):
    from app.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "# header comment\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  api_token: secret\n"
        "\n"
        "modbus:\n"
        "  - name: dev\n"
        "    host: 10.0.0.1\n"
        "    sensors:\n"
        "      - id: temp\n"
        "        address: 2\n"
        "        data_type: int16\n",
        encoding="utf-8",
    )

    def factory(device_config):
        return FakeModbusDevice(device_config)

    cfg = load_config(path)
    app = create_app(cfg, device_factory=factory)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret"}
        response = client.patch(
            "/api/entities/sensor.temp", json={"scale": 2.5}, headers=headers
        )
        assert response.status_code == 200

    text = path.read_text(encoding="utf-8")
    assert "scale: 2.5" in text
    assert "# header comment" in text
    assert "server:" in text
    assert "api_token: secret" in text
