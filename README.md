# smartHome

A lightweight home automation controller: Modbus TCP devices, a REST API and a
small live dashboard. Built to replace a full Home Assistant install for a
handful of devices (door lockers + a Panasonic heat exchanger).

## Features

- Modbus TCP switches (coils or holding registers) with optional verify-read
- Modbus sensors polled on per-sensor intervals (int16/uint16/int32/uint32/float32/custom struct)
- REST API protected by a bearer token
- Web dashboard with live WebSocket updates and polling fallback
- Runtime editing of device/entity settings from the dashboard, persisted back to `config.yaml`
- Runs in a single process; no database, no add-ons

## Quick start

Requires Python 3.9 or newer.

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
| GET | `/api/devices` | List devices with editable connection settings |
| PATCH | `/api/devices/{device_name}` | Update device settings (host/port/slave/timeout/reconnect_interval) |
| PATCH | `/api/entities/{entity_id}` | Update entity settings (address/scan_interval/scale/name/...) |
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

### One-shot install (dedicated user + systemd user service)

Build a release tarball (on the development machine):

```bash
./deploy/package.sh          # -> dist/smartHome-<version>.tar.gz (+ .sha256)
```

Copy it to the target, extract it, then install:

```bash
tar -xzf smartHome-<version>.tar.gz
cd smartHome-<version>
sudo ./deploy/install.sh
```

Creates a dedicated `smartHome` user, installs the app and a virtualenv into
`/home/smartHome/.smartHome`, installs a systemd *user* unit
(`deploy/smart-home.user.service`) and enables lingering so it starts at boot.
Re-running the script upgrades the code and dependencies while keeping
`config.yaml`.

## Operations

Open the dashboard at `http://<target>:8080` (or the configured
`server.host`/`server.port`) and paste the `api_token` from `config.yaml`.

The one-shot install runs smartHome as a systemd *user* service for the
`smartHome` account. Since that account has no login shell, `systemctl --user`
needs its runtime directory:

```bash
sudo -u smartHome env XDG_RUNTIME_DIR=/run/user/$(id -u smartHome) systemctl --user status smart-home
sudo -u smartHome env XDG_RUNTIME_DIR=/run/user/$(id -u smartHome) systemctl --user restart smart-home
sudo -u smartHome env XDG_RUNTIME_DIR=/run/user/$(id -u smartHome) systemctl --user stop smart-home
```

Optional convenience wrapper:

```bash
sudo tee /usr/local/bin/smart-home-ctl >/dev/null <<'EOF'
#!/bin/sh
exec sudo -u smartHome env XDG_RUNTIME_DIR="/run/user/$(id -u smartHome)" \
    systemctl --user "$@"
EOF
sudo chmod +x /usr/local/bin/smart-home-ctl
```

Then use it without the long prefix:

```bash
smart-home-ctl status smart-home
smart-home-ctl restart smart-home
```

Follow the logs (user-unit messages are in the system journal under the
`smartHome` UID):

```bash
sudo journalctl _UID=$(id -u smartHome) -f
```

For the `/opt/smart-home` system service from the section above, use plain
`sudo systemctl status smart-home` and `sudo journalctl -u smart-home -f`
instead.

### Edit the configuration

The live configuration is `/home/smartHome/.smartHome/config.yaml`:

```bash
sudo nano /home/smartHome/.smartHome/config.yaml
smart-home-ctl restart smart-home     # apply server.* changes
```

- `server.host`, `server.port`, `server.api_token` and
  `server.shutdown_timeout` are read at startup, so restart after changing
  them. After changing `api_token`, log in again with the new token.
- `modbus` device and entity settings can also be edited live from the
  dashboard; those updates take effect immediately and are written back to
  `config.yaml`.
- `SMART_HOME_CONFIG` overrides the config path and `SMART_HOME_TOKEN`
  overrides the token, e.g. via
  `smart-home-ctl edit smart-home` (adds an override drop-in).

### Upgrade

Build a new tarball with `./deploy/package.sh`, copy and extract it on the
target, then run `sudo ./deploy/install.sh` again. It refreshes the code and
dependencies, keeps `config.yaml`, and restarts the service.

### Uninstall

```bash
smart-home-ctl disable --now smart-home
sudo loginctl disable-linger smartHome
sudo userdel -r smartHome
```

`userdel -r` deletes `/home/smartHome`, including `config.yaml`; back it up
first if needed.

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
