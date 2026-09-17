# SNMP Network Device Simulator

A Dockerized, **CLI-only** virtual SNMP lab for Ubuntu Server. It simulates routers, switches, firewalls, and generic SNMP devices so a **Datadog Agent on the same host** can scrape realistic, time-varying metrics.

There is **no web UI, no React app, and no HTML dashboard**. Everything is controlled over SSH with Django management commands.

The SNMP protocol is served by [SNMPSim](https://github.com/lextudio/snmpsim) (`snmpsim-command-responder`). Django stores the inventory, generates `.snmprec` data, and reloads the engine when you change a device.

## What you can do

- Spin up N simulated devices with HIGH / MEDIUM / LOW traffic plus UNREACHABLE devices
- Poll them with `snmpwalk` / `snmpget` and with the Datadog Agent SNMP integration
- Change a live device between `UP / REACHABLE` and `DOWN / UNREACHABLE` without rebuilding
- Change traffic profiles, add/remove devices, and keep state across Docker restarts

## Architecture

```text
Ubuntu host
├── Datadog Agent          polls 10.200.1.x:161  (or 127.0.0.1:1161)
└── Docker  snmp-lab  (10.200.0.0/16)
    └── snmp-simulator
        ├── Django CLI + SQLite inventory     /data/db
        ├── Generated SNMPSim data            /data/snmp/<device-id>/public.snmprec
        ├── SNMPSim command responder         UDP/161 per device IP
        └── Community multiplexer             0.0.0.0:1161  community = device-id
```

Each reachable device is assigned an address in `10.200.1.0/24` and served as its own SNMPSim engine with community `public`. Unreachable devices stay in inventory (and in the Datadog config) but SNMPSim does **not** listen on their IP, so Datadog times out.

A second endpoint on **UDP 1161** selects the device by community string (`device-001`, `device-002`, …). Use that from the host when you cannot route into the Docker subnet.

## Project layout

```text
.
├── docker-compose.yml
├── Dockerfile
├── manage.py
├── requirements.txt
├── README.md
├── config/                 # simulator + device-type YAML
├── devices/examples/       # sample device JSON
├── snmp_data/              # placeholder for generated SNMP files
├── examples/datadog/       # Datadog Agent config examples
├── scripts/                # entrypoint, CLI wrapper, snmp smoke test
├── snmp_lab/               # Django project (CLI only, no HTTP)
└── simulator/              # inventory models, engine, management command
```

## Ubuntu deployment

### 1. Install Docker

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"
# log out and back in so group membership applies
```

Optional SNMP client on the host:

```bash
sudo apt-get install -y snmp
```

### 2. Copy this project onto the server

```bash
cd /opt
sudo mkdir -p snmp-simulator
sudo chown "$USER":"$USER" snmp-simulator
# copy the project files here
cd /opt/snmp-simulator
```

### 3. Build and start

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=50
```

The container starts a supervisor. The lab is empty until you create devices.

### 4. Create a simulation

Interactive (asks the five questions and validates the totals):

```bash
docker compose exec simulator sim start
```

Example answers:

```text
How many devices do you want? 50
How many HIGH traffic devices? 10
How many MEDIUM traffic devices? 20
How many LOW traffic devices? 15
How many UNREACHABLE devices? 5
```

`10 + 20 + 15 + 5` must equal `50` or the command is rejected.

Non-interactive:

```bash
docker compose exec simulator sim start --non-interactive \
  --total 50 --high 10 --medium 20 --low 15 --unreachable 5
```

Replace an existing lab:

```bash
docker compose exec simulator sim start --replace --non-interactive \
  --total 10 --high 2 --medium 4 --low 3 --unreachable 1
```

Automatic lab on container boot (optional):

```bash
export SIMULATOR_AUTO_START=true
export SIMULATOR_TOTAL=10
export SIMULATOR_HIGH=2
export SIMULATOR_MEDIUM=4
export SIMULATOR_LOW=3
export SIMULATOR_UNREACHABLE=1
docker compose up -d
```

## CLI reference

All commands run inside the container:

```bash
docker compose exec simulator sim <command>
# equivalent:
docker compose exec simulator python manage.py simulator <command>
```

| Command | Purpose |
| --- | --- |
| `start` | Prompt for counts, create devices, start SNMPSim |
| `stop` | Stop SNMPSim; keep the inventory |
| `status` | Engine PID, reachable/unreachable counts |
| `list` | Table of all devices (`--json` supported) |
| `show device-001` | Full device + interface details + query examples |
| `add [--type router] [--traffic high] [--reachability down]` | Add one device |
| `remove device-001` | Delete one device |
| `update device-001 --name edge-rtr-99 --type firewall` | Change metadata |
| `set-traffic device-001 high` | HIGH / MEDIUM / LOW |
| `set-reachability device-005 down` | `up`/`reachable` or `down`/`unreachable` |
| `reload` | Rewrite `.snmprec` files and restart SNMPSim |
| `reset --force` | Destroy the lab |
| `export-datadog` | Write Agent YAML into `/data/datadog/` |
| `types` | List device types from `config/device_types.yaml` |

Aliases from the original spec also work: `list-devices`, `add-device`, `remove-device`, `update-device`, `show-device`.

### Typical session

```bash
docker compose exec simulator sim start
docker compose exec simulator sim status
docker compose exec simulator sim list
docker compose exec simulator sim show device-001
docker compose exec simulator sim set-traffic device-001 high
docker compose exec simulator sim set-reachability device-005 down
docker compose exec simulator sim set-reachability device-005 up
docker compose exec simulator sim add --type switch --traffic medium
docker compose exec simulator sim remove device-012
```

## Device model

Each device gets a stable id (`device-001`, `device-002`, …) and:

- Hostname, vendor/model, location, contact, serial
- IPv4 address in `10.200.1.0/24`
- SNMPv2c community `public` (plus community alias = device id)
- Device type (router / switch / firewall / generic)
- Interface table with names, status, speed, MAC, alias
- Time-varying traffic, packets, errors, discards, CPU, memory, uptime

Built-in types (edit `config/device_types.yaml` to add more):

| Type | sysObjectID | Datadog profile match |
| --- | --- | --- |
| `router` | `1.3.6.1.4.1.9.1.2093` (Cisco ISR 4321) | cisco-isr |
| `switch` | `1.3.6.1.4.1.9.1.1745` (Catalyst 3850) | cisco-3850 |
| `firewall` | `1.3.6.1.4.1.9.1.745` (ASA 5505) | cisco-asa |
| `generic` | `1.3.6.1.4.1.8072.3.2.10` (net-snmp Linux) | generic-device / linux |

## Traffic profiles

Implemented with SNMPSim’s **numeric variation module**, so counters increase with wall-clock time and gauges oscillate. Datadog therefore sees real time-series, not a flat line.

| Profile | Approx. interface rate | CPU | Errors |
| --- | --- | --- | --- |
| HIGH | ~600 Mbps in / ~440 Mbps out on 1G | ~70% | some |
| MEDIUM | ~96 Mbps in / ~72 Mbps out on 1G | ~40% | rare |
| LOW | ~5.6 Mbps in / ~3.6 Mbps out on 100M | ~12% | none |

Per-interface `traffic_scale` keeps two HIGH devices from looking identical.

## Unreachable devices

`set-reachability device-005 down`:

1. SNMPSim stops listening on that device IP
2. The community-alias file for UDP 1161 is removed
3. The device **remains** in SQLite and in the Datadog config

Datadog’s next poll times out (`snmp.can_check` / NDM reachability). Bring it back with `set-reachability device-005 up`. No image rebuild is required.

## Persistence

Docker volume `snmp-simulator-data` is mounted at `/data`:

| Path | Contents |
| --- | --- |
| `/data/db/simulator.sqlite3` | Device inventory |
| `/data/snmp/<id>/public.snmprec` | SNMPSim snapshots |
| `/data/datadog/` | Generated Agent YAML |
| `/data/logs/snmpsim.log` | SNMPSim stdout |
| `/data/run/` | PIDs, reload flag |

`docker compose restart` keeps the lab. `docker compose down -v` destroys it.

## Testing the SNMP engine

Wait a few seconds after `start` / `set-*` so the supervisor can reload SNMPSim (`sim status` should show a PID).

From the Ubuntu host, community multiplexer:

```bash
snmpwalk -v2c -c device-001 -On 127.0.0.1:1161 1.3.6.1.2.1.1
snmpget  -v2c -c device-001 -On 127.0.0.1:1161 1.3.6.1.2.1.1.5.0
snmpwalk -v2c -c device-001 -On 127.0.0.1:1161 1.3.6.1.2.1.2
snmpwalk -v2c -c device-001 -On 127.0.0.1:1161 1.3.6.1.2.1.31.1.1.1.6
```

Walk the same OID twice a few seconds apart: `ifHCInOctets` / `sysUpTime` should increase.

From the host into the Docker subnet (native Ubuntu Docker Engine):

```bash
snmpwalk -v2c -c public -On 10.200.1.1:161 1.3.6.1.2.1.1
```

From inside the container:

```bash
docker compose exec simulator python /app/scripts/test_snmp.py --community device-001 --host 127.0.0.1 --port 1161
docker compose exec simulator snmpwalk -v2c -c public 10.200.1.1:161 1.3.6.1.2.1.1
```

An unreachable device must time out:

```bash
docker compose exec simulator sim set-reachability device-003 down
# this should fail
snmpwalk -v2c -c device-003 -t 2 -r 1 127.0.0.1:1161 1.3.6.1.2.1.1
```

Unit tests (no SNMP daemon required):

```bash
docker compose exec simulator python manage.py test simulator -v2
```

## Datadog Agent configuration

Install the Datadog Agent on the **same Ubuntu server** (not inside this compose file). Enable **Network Device Monitoring** / the SNMP integration.

### Option A — per-device IPs (preferred on Ubuntu)

After creating devices:

```bash
docker compose exec simulator sim export-datadog
docker compose cp simulator:/data/datadog/conf.yaml /tmp/snmp-conf.yaml
sudo mkdir -p /etc/datadog-agent/conf.d/snmp.d
sudo cp /tmp/snmp-conf.yaml /etc/datadog-agent/conf.d/snmp.d/conf.yaml
sudo systemctl restart datadog-agent
```

Each instance looks like:

```yaml
init_config:
  loader: core
  use_device_id_as_hostname: true

instances:
  - ip_address: '10.200.1.1'
    port: 161
    snmp_version: 2
    community_string: 'public'
    timeout: 3
    retries: 2
    tags:
      - 'device_id:device-001'
      - 'device_type:router'
      - 'traffic_profile:high'
      - 'env:snmp-lab'
```

Confirm the host can reach the lab subnet:

```bash
ping -c 1 10.200.1.1
snmpwalk -v2c -c public 10.200.1.1:161 1.3.6.1.2.1.1.5.0
```

If ping fails, the Agent cannot scrape Option A. Use Option B or add a route to the `snmp-lab` bridge (`ip route` will show `10.200.0.0/16` via `docker0` / `br-...` on Linux).

### Option B — subnet autodiscovery

Append `examples/datadog/datadog.yaml.snippet` to `/etc/datadog-agent/datadog.yaml`:

```yaml
network_devices:
  autodiscovery:
    workers: 50
    discovery_interval: 3600
    loader: core
    use_device_id_as_hostname: true
    configs:
      - network_address: 10.200.1.0/24
        snmp_version: 2
        port: 161
        community_string: 'public'
        tags:
          - 'env:snmp-lab'
```

Unreachable IPs in that /24 will be discovered as down, which is what you want for alert demos.

### Option C — host port 1161 (Docker Desktop / no subnet routing)

```bash
docker compose cp simulator:/data/datadog/conf.hostport.yaml /tmp/snmp-conf.yaml
sudo cp /tmp/snmp-conf.yaml /etc/datadog-agent/conf.d/snmp.d/conf.yaml
sudo systemctl restart datadog-agent
```

Here `ip_address` is `127.0.0.1`, `port` is `1161`, and `community_string` is `device-001`, `device-002`, …

### Verify collection

```bash
sudo datadog-agent status | less
sudo datadog-agent check snmp
```

In Datadog: **Infrastructure → Network Devices**, metrics such as `snmp.sysUpTime`, `snmp.ifInOctets`, `snmp.ifHCInOctets`, interface bandwidth, and Cisco CPU/memory on ISR/3850/ASA profiles.

Suggested demo monitors:

- Device unreachable: `snmp.can_check` is 0
- High bandwidth: `snmp.ifHCInOctets` rate on `traffic_profile:high`
- Interface down: `snmp.ifOperStatus` != 1

Re-export YAML after you add/remove devices (`sim export-datadog`) and copy it to the Agent again if you use Option A/C rather than autodiscovery.

## Extending device types

Add a block to `config/device_types.yaml`:

```yaml
paloalto:
  category: firewall
  vendor: Palo Alto
  model: PA-3220
  sys_object_id: "1.3.6.1.4.1.25461.2.3.18"
  sys_descr: "Palo Alto Networks PA-3220"
  metadata_type: firewall
  interface_count: [4, 8]
  interface_style: cisco_firewall
  extra_mibs: []
  name_patterns:
    - pa-fw-{nn}
```

Then `docker compose up -d --build` (YAML is copied into the image) and:

```bash
docker compose exec simulator sim add --type paloalto --traffic medium
```

`interface_style` values: `cisco_router`, `cisco_switch`, `cisco_firewall`, `linux`.  
`extra_mibs` values: `cisco_cpu`, `cisco_memory`, `cisco_chassis`, `host_resources`, `ucd_snmp`.

## SNMP OIDs served

Every device includes:

- SNMPv2-MIB system group (`sysDescr`, `sysObjectID`, `sysUpTime`, `sysName`, …)
- IF-MIB `ifTable` + `ifXTable` (64-bit octet/packet counters, aliases, high speed)
- IP-MIB scalars + `ipAddrTable`
- TCP-MIB / UDP-MIB scalars

Cisco types also serve CISCO-PROCESS-MIB, CISCO-MEMORY-POOL-MIB, OLD-CISCO-CHASSIS-MIB. Generic Linux types serve HOST-RESOURCES-MIB and UCD-SNMP-MIB.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `sim status` shows no PID | `docker compose logs simulator` and `/data/logs/snmpsim.log` |
| `snmpwalk` times out on 1161 | Device is DOWN, or reload still in progress (`sim status`) |
| Datadog cannot reach `10.200.1.x` | Use Option C (127.0.0.1:1161) or confirm `ip route get 10.200.1.1` |
| Categories rejected on start | HIGH+MEDIUM+LOW+UNREACHABLE must equal total |
| Permission error adding IPs | Container needs `NET_ADMIN` (already in compose) |
| Empty lab after restart | Volume was removed (`down -v`) or lab never started |

## Local development without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py test simulator
python manage.py simulator start --non-interactive --total 3 --high 1 --medium 1 --low 1 --unreachable 0
python manage.py simulator daemon
```

Binding UDP 161 requires root or a non-privileged port. Host-port **1161** is the easier local path.

## License / purpose

Internal lab tool for SNMP testing and Datadog Network Device Monitoring demonstrations. Not a replacement for production network equipment.
