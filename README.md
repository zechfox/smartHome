# smartHome

A lightweight home automation controller: Modbus TCP devices, a REST API and a
small live dashboard. Built to replace a full Home Assistant install for a
handful of devices (door lockers + a Panasonic heat exchanger).

## Features

- Modbus TCP switches (coils or holding registers) with optional verify-read
- Modbus sensors polled on per-sensor intervals (int16/uint16/int32/uint32/float32/custom struct)
- REST API protected by a bearer token
- Web dashboard with live WebSocket updates and polling fallback
- Runs in a single process; no database, no add-ons

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# edit config.yaml: set api_token and your device addresses
.venv/bin/python -m app
```

Open <http://localhost:8080> and paste the `api_token` from `config.yaml`.

## Configuration

All configuration lives in `config.yaml` (path overridable with
`SMART_HOME_CONFIG`, token overridable with `SMART_HOME_TOKEN`).

```yaml
server:
  host: 0.0.0.0
  port: 8080
  api_token: "a-long-random-string"
  shutdown_timeout: 10   # seconds to wait for open connections on shutdown

modbus:
  - name: unit_door_locker
    host: 10.0.0.30
    port: 502
    slave: 1
    timeout: 5            # seconds, optional
    reconnect_interval: 5 # seconds between reconnect attempts, optional
    switches:
      - id: unit_door_locker1
        name: Unit Door Locker 1
        type: coil          # coil | holding (default coil)
        address: 2
        command_on: 1       # value written for on (default 1)
        command_off: 0      # value written for off (default 0)
        verify_delay: 1     # seconds to wait before reading state back (0 = no verify)
        scan_interval: 30   # optional periodic state refresh in seconds
    sensors:
      - id: exchanger_warning_value
        name: Exchanger Warning Value
        address: 15
        data_type: int16    # int16 | uint16 | int32 | uint32 | float32 | custom
        scan_interval: 10
        unit: H             # optional
        scale: 1.0          # optional multiplier
        precision: 1        # optional rounding
      - id: exchanger_timer
        address: 17
        data_type: custom
        count: 3            # number of 16-bit registers
        struct: "=BBBBBB"   # struct format applied to the register bytes
```

Entity IDs are exposed as `switch.<id>` and `sensor.<id>`.

## API

All endpoints except `/api/health` require `Authorization: Bearer <api_token>`.

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/health` | Service and device connection status |
| GET | `/api/entities?domain=switch` | List entities (optional domain filter) |
| GET | `/api/entities/{entity_id}` | Get one entity |
| POST | `/api/entities/{entity_id}/set` | Switch: `{"state": "on"}` or `{"state": "off"}` |
| WS | `/ws?token=<api_token>` | Live state stream (snapshot + `state_changed` events) |

Example:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/entities
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"state":"on"}' http://localhost:8080/api/entities/switch.exchanger_state/set
```

## Deploy on Raspberry Pi (systemd)

```bash
sudo apt update && sudo apt install -y python3-venv
sudo mkdir -p /opt/smart-home
sudo chown "$USER":"$USER" /opt/smart-home
# copy the project into /opt/smart-home (git clone, scp, ...)
cd /opt/smart-home
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# edit /opt/smart-home/config.yaml and set a strong api_token
sudo cp deploy/smart-home.service /etc/systemd/system/smart-home.service
sudo systemctl daemon-reload
sudo systemctl enable --now smart-home
systemctl status smart-home
journalctl -u smart-home -f
```

Adjust `User=`/`Group=` and paths in the unit file if you do not deploy as `pi`
and `/opt/smart-home`.

## Security

- The API and dashboard can open door lockers: always set a strong
  `api_token`, and prefer binding `server.host` to your LAN interface.
- Put the service behind a reverse proxy with TLS if it is reachable from
  outside your home network.
- The dashboard stores the token in browser `localStorage`; use the **Lock**
  button on shared devices.

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check .
```
