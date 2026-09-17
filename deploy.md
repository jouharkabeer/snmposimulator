# Deploy on an Ubuntu server

This guide covers cloning the project to a directory on an Ubuntu server, starting it with Docker Compose, and installing a **systemd service** so the simulator comes up on boot.

There is no web UI. After the service is running, you manage devices over SSH with the CLI.

## 1. Prerequisites

SSH into the server as a user that can use `sudo`.

Install Docker Engine and the Compose plugin:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in so the `docker` group applies, then check:

```bash
docker version
docker compose version
```

Optional SNMP client on the host (for `snmpwalk` tests):

```bash
sudo apt-get install -y snmp
```

## 2. Clone the project

Pick a directory. The systemd unit shipped in this repo uses **`/opt/snmp-simulator`**. If you clone somewhere else, edit the paths in the service file before installing it (see [Custom install path](#custom-install-path)).

```bash
sudo mkdir -p /opt/snmp-simulator
sudo chown "$USER":"$USER" /opt/snmp-simulator
cd /opt
git clone <YOUR_GIT_URL> snmp-simulator
cd /opt/snmp-simulator
```

If the files are already on the server (scp/rsync) instead of git:

```bash
sudo mkdir -p /opt/snmp-simulator
sudo chown "$USER":"$USER" /opt/snmp-simulator
rsync -a ./ /opt/snmp-simulator/
cd /opt/snmp-simulator
```

## 3. Optional environment file

```bash
cd /opt/snmp-simulator
cp .env.example .env
nano .env
```

Leave `SIMULATOR_AUTO_START=false` unless you want a lab created automatically on every container start.

To auto-create a lab when the service starts:

```bash
SIMULATOR_AUTO_START=true
SIMULATOR_TOTAL=10
SIMULATOR_HIGH=2
SIMULATOR_MEDIUM=4
SIMULATOR_LOW=3
SIMULATOR_UNREACHABLE=1
```

HIGH + MEDIUM + LOW + UNREACHABLE must equal TOTAL.

## 4. Install the systemd service

The unit file is `deploy/snmp-simulator.service`. It runs `docker compose up -d --build` from `/opt/snmp-simulator` and stops the stack with `docker compose down`.

```bash
cd /opt/snmp-simulator
sudo cp deploy/snmp-simulator.service /etc/systemd/system/snmp-simulator.service
sudo systemctl daemon-reload
sudo systemctl enable --now snmp-simulator.service
```

Check it:

```bash
sudo systemctl status snmp-simulator.service
docker compose -f /opt/snmp-simulator/docker-compose.yml ps
docker compose -f /opt/snmp-simulator/docker-compose.yml logs --tail=50
```

The container name is `snmp-simulator`. Compose already sets `restart: unless-stopped`, so Docker will restart the container after a crash. systemd brings the whole stack up after a reboot.

### Custom install path

If you cloned to another directory, for example `/home/ubuntu/snmp-simulator`, edit the unit before enabling it:

```bash
sudo nano /etc/systemd/system/snmp-simulator.service
```

Replace every `/opt/snmp-simulator` with your path (`WorkingDirectory`, `EnvironmentFile`, `ExecStartPre`, `ExecStart`, `ExecStop`, `ExecReload`). Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now snmp-simulator.service
```

## 5. Create devices (first time)

The service only starts the engine. An empty lab stays empty until you create devices.

```bash
cd /opt/snmp-simulator
docker compose exec simulator sim start
```

Answer:

```text
How many devices do you want?
How many HIGH traffic devices?
How many MEDIUM traffic devices?
How many LOW traffic devices?
How many UNREACHABLE devices?
```

Non-interactive example:

```bash
docker compose exec simulator sim start --non-interactive \
  --total 10 --high 2 --medium 4 --low 3 --unreachable 1
```

Inventory is stored in the Docker volume `snmp-simulator-data` and survives service restarts and host reboots.

## 6. Daily operations

Run these from `/opt/snmp-simulator` (or use the full `-f` path).

| Task | Command |
| --- | --- |
| Service status | `sudo systemctl status snmp-simulator` |
| Start / stop / restart service | `sudo systemctl start snmp-simulator`<br>`sudo systemctl stop snmp-simulator`<br>`sudo systemctl restart snmp-simulator` |
| Rebuild after a git pull | `sudo systemctl reload snmp-simulator`<br>or `sudo systemctl restart snmp-simulator` |
| Simulator status | `docker compose exec simulator sim status` |
| List devices | `docker compose exec simulator sim list` |
| Show one device | `docker compose exec simulator sim show device-001` |
| Change traffic | `docker compose exec simulator sim set-traffic device-001 high` |
| Mark unreachable | `docker compose exec simulator sim set-reachability device-005 down` |
| Container logs | `docker compose logs -f simulator` |
| systemd journal | `sudo journalctl -u snmp-simulator -e` |

## 7. Test SNMP from the server

Wait a few seconds after `sim start`, then:

```bash
snmpwalk -v2c -c device-001 -On 127.0.0.1:1161 1.3.6.1.2.1.1
```

On native Ubuntu Docker Engine, device IPs in `10.200.1.0/24` are usually reachable from the host:

```bash
snmpwalk -v2c -c public -On 10.200.1.1:161 1.3.6.1.2.1.1
```

## 8. Point the Datadog Agent at the lab

The Datadog Agent should run **on the same Ubuntu host**, not inside this compose file.

```bash
cd /opt/snmp-simulator
docker compose exec simulator sim export-datadog
docker compose cp simulator:/data/datadog/conf.yaml /tmp/snmp-conf.yaml
sudo mkdir -p /etc/datadog-agent/conf.d/snmp.d
sudo cp /tmp/snmp-conf.yaml /etc/datadog-agent/conf.d/snmp.d/conf.yaml
sudo systemctl restart datadog-agent
```

If the Agent cannot route to `10.200.1.0/24`, use the host-port file instead:

```bash
docker compose cp simulator:/data/datadog/conf.hostport.yaml /tmp/snmp-conf.yaml
sudo cp /tmp/snmp-conf.yaml /etc/datadog-agent/conf.d/snmp.d/conf.yaml
sudo systemctl restart datadog-agent
```

That file polls `127.0.0.1:1161` with community strings `device-001`, `device-002`, and so on.

Re-export after you add or remove devices.

## 9. Update the deployment

```bash
cd /opt/snmp-simulator
git pull
sudo systemctl restart snmp-simulator
```

`restart` runs `docker compose down` then `up -d --build`. Device data in the named volume is kept.

To wipe the lab (devices and SNMP data):

```bash
sudo systemctl stop snmp-simulator
cd /opt/snmp-simulator
docker compose down -v
sudo systemctl start snmp-simulator
docker compose exec simulator sim start
```

## 10. Uninstall

```bash
sudo systemctl disable --now snmp-simulator.service
sudo rm /etc/systemd/system/snmp-simulator.service
sudo systemctl daemon-reload
cd /opt/snmp-simulator
docker compose down -v
docker image rm snmp-network-device-simulator:latest || true
```

Remove the clone only if you no longer need the source:

```bash
sudo rm -rf /opt/snmp-simulator
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `systemctl start` fails | `sudo journalctl -u snmp-simulator -e` and `docker compose logs` |
| `docker compose` not found | Install `docker-compose-plugin` (not the old `docker-compose` package) |
| Permission denied talking to Docker | User must be in the `docker` group, or run compose via the systemd unit (root) |
| Empty lab after reboot | Volume is fine; you never ran `sim start`, or you used `down -v` |
| `snmpwalk` to `10.200.1.1` fails | Use `127.0.0.1:1161` and community `device-001` |
| UDP 1161 already in use | Change the published port in `docker-compose.yml` (`"1161:1161/udp"`) |
| Service file paths wrong | `WorkingDirectory` must be the clone directory; then `daemon-reload` |
