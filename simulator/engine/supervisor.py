"""Manage SNMPSim processes, container IP aliases, and reload signalling."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from simulator.engine.config import load_simulator_config
from simulator.engine.snmprec import remove_device_snmprec, write_device_snmprec
from simulator.models import Device, Reachability, SimulationState


class SupervisorError(RuntimeError):
    pass


def run_dir() -> Path:
    path = Path(settings.SIMULATOR_RUN_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def snmp_dir() -> Path:
    path = Path(settings.SIMULATOR_SNMP_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def reload_flag_path() -> Path:
    return run_dir() / load_simulator_config()["engine"]["reload_flag"]


def snmpsim_pid_path() -> Path:
    return run_dir() / load_simulator_config()["engine"]["pid_file"]


def args_file_path() -> Path:
    return run_dir() / load_simulator_config()["engine"]["args_file"]


def cache_dir() -> Path:
    path = run_dir() / load_simulator_config()["engine"]["cache_dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def request_reload() -> None:
    path = reload_flag_path()
    path.write_text(str(time.time()), encoding="utf-8")
    state = SimulationState.get()
    state.generation += 1
    state.save(update_fields=["generation"])


def consume_reload() -> bool:
    path = reload_flag_path()
    if path.exists():
        path.unlink()
        return True
    return False


def find_snmpsim() -> str:
    exe = shutil.which("snmpsim-command-responder")
    if not exe:
        raise SupervisorError(
            "snmpsim-command-responder was not found on PATH. "
            "Install snmpsim (see requirements.txt)."
        )
    return exe


def rebuild_all_snmprec() -> None:
    root = snmp_dir()
    communities = root / "communities"
    communities.mkdir(parents=True, exist_ok=True)
    keep_ids = set()
    for device in Device.objects.all():
        write_device_snmprec(device, root)
        keep_ids.add(device.device_id)
        if not device.is_reachable:
            alias = communities / f"{device.community_alias}.snmprec"
            if alias.exists():
                alias.unlink()
    for child in root.iterdir():
        if child.name in {"communities", "cache"}:
            continue
        if child.is_dir() and child.name not in keep_ids:
            remove_device_snmprec(child.name, root)


def primary_nic() -> str:
    env_nic = os.environ.get("SIMULATOR_NIC")
    if env_nic:
        return env_nic
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "route", "show", "default"],
            check=False,
            capture_output=True,
            text=True,
        )
        parts = result.stdout.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    except OSError:
        pass
    return "eth0"


def _ip_cmd(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ip", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def configured_alias_ips() -> set[str]:
    return {device.ip_address for device in Device.objects.filter(reachability=Reachability.UP)}


def listed_alias_ips(nic: str) -> set[str]:
    result = _ip_cmd("-o", "-4", "addr", "show", "dev", nic)
    if result.returncode != 0:
        return set()
    found: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if "inet" in parts:
            cidr = parts[parts.index("inet") + 1]
            ip = cidr.split("/", 1)[0]
            found.add(ip)
    return found


def reconcile_ip_aliases() -> list[str]:
    """Add / remove secondary IPs so reachable devices are present on the NIC.

    Returns warning messages (empty when everything succeeded).
    """
    warnings: list[str] = []
    if shutil.which("ip") is None:
        return ["iproute2 is not available; IP-per-device mode is disabled."]
    nic = primary_nic()
    desired = configured_alias_ips()
    current = listed_alias_ips(nic)
    cfg = load_simulator_config()
    management_ip = cfg["network"]["management_ip"]
    gateway = cfg["network"]["gateway"]
    prefix = cfg["network"]["subnet"].split("/")[-1]

    protected = {management_ip, gateway}
    # Never delete the container's primary address.
    result = _ip_cmd("-o", "-4", "addr", "show", "dev", nic)
    primary_ips = set()
    first = True
    for line in result.stdout.splitlines():
        parts = line.split()
        if "inet" in parts:
            ip = parts[parts.index("inet") + 1].split("/", 1)[0]
            if first:
                primary_ips.add(ip)
                first = False

    for ip in sorted(desired - current):
        added = _ip_cmd("addr", "add", f"{ip}/{prefix}", "dev", nic)
        if added.returncode != 0 and "File exists" not in (added.stderr or ""):
            warnings.append(f"Could not add {ip} on {nic}: {added.stderr.strip()}")

    removable = current - desired - protected - primary_ips
    # Only remove addresses that belong to the lab device CIDR.
    from simulator.engine.ipam import device_network
    network = device_network()
    for ip in sorted(removable):
        try:
            import ipaddress

            if ipaddress.ip_address(ip) not in network:
                continue
        except ValueError:
            continue
        _ip_cmd("addr", "del", f"{ip}/{prefix}", "dev", nic)
    return warnings


def build_snmpsim_args() -> list[str]:
    cfg = load_simulator_config()
    snmp_port = int(cfg["snmp"]["port"])
    host_port = int(cfg["snmp"]["host_port"])
    max_varbinds = int(cfg["snmp"].get("max_varbinds", 128))
    args: list[str] = [
        "--logging-method",
        "stdout",
        "--process-user",
        "snmp",
        "--process-group",
        "snmp",
        "--log-level",
        os.environ.get("SNMPSIM_LOG_LEVEL", "info"),
        "--cache-dir",
        str(cache_dir()),
        "--max-var-binds",
        str(max_varbinds),
    ]
    variation_dir = Path(settings.SIMULATOR_VARIATION_DIR)
    if variation_dir.is_dir() and any(variation_dir.glob("*.py")):
        args.extend(["--variation-modules-dir", str(variation_dir)])

    reachable = list(Device.objects.filter(reachability=Reachability.UP))
    bound_ips = listed_alias_ips(primary_nic()) if shutil.which("ip") else set()
    for device in reachable:
        if device.ip_address not in bound_ips:
            continue
        args.extend(
            [
                "--v3-engine-id",
                "auto",
                "--data-dir",
                str(snmp_dir() / device.device_id),
                "--agent-udpv4-endpoint",
                f"{device.ip_address}:{snmp_port}",
            ]
        )

    communities = snmp_dir() / "communities"
    communities.mkdir(parents=True, exist_ok=True)
    # Host-port multiplexer: community string = device-id, even for DOWN devices
    # the file is removed so Datadog/snmpwalk time out.
    args.extend(
        [
            "--v3-engine-id",
            "auto",
            "--data-dir",
            str(communities),
            "--agent-udpv4-endpoint",
            f"0.0.0.0:{host_port}",
        ]
    )
    return args


def write_args_file(args: list[str]) -> Path:
    path = args_file_path()
    # One argument per line so snmpsim --args-from-file can load it.
    path.write_text("\n".join(args) + "\n", encoding="utf-8")
    return path


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    return pid


def pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop_snmpsim() -> None:
    pid = read_pid(snmpsim_pid_path())
    if pid and pid_is_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(30):
            if not pid_is_running(pid):
                break
            time.sleep(0.1)
        if pid_is_running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
    if snmpsim_pid_path().exists():
        snmpsim_pid_path().unlink()


def start_snmpsim() -> subprocess.Popen:
    rebuild_all_snmprec()
    warnings = reconcile_ip_aliases()
    args = build_snmpsim_args()
    write_args_file(args)
    exe = find_snmpsim()

    reachable_count = Device.objects.filter(reachability=Reachability.UP).count()
    if reachable_count == 0:
        # Still start the host-port engine so the process is alive.
        pass

    command = [exe, *args]
    log_path = Path(settings.SIMULATOR_LOG_DIR) / "snmpsim.log"
    log_handle = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        cwd=str(settings.BASE_DIR),
    )
    time.sleep(0.6)
    if process.poll() is not None:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            tail = ""
        raise SupervisorError(
            f"SNMPSim exited immediately with code {process.returncode}.\n{tail}"
        )
    snmpsim_pid_path().write_text(str(process.pid), encoding="utf-8")
    state = SimulationState.get()
    state.engine_enabled = True
    state.last_started_at = timezone.now()
    state.last_error = "\n".join(warnings)
    state.save(update_fields=["engine_enabled", "last_started_at", "last_error"])
    return process


def restart_snmpsim() -> None:
    stop_snmpsim()
    start_snmpsim()


def engine_status() -> dict:
    pid = read_pid(snmpsim_pid_path())
    running = pid_is_running(pid)
    state = SimulationState.get()
    total = Device.objects.count()
    reachable = Device.objects.filter(reachability=Reachability.UP).count()
    return {
        "engine_enabled": state.engine_enabled,
        "snmpsim_running": running,
        "snmpsim_pid": pid if running else None,
        "last_started_at": state.last_started_at,
        "last_stopped_at": state.last_stopped_at,
        "last_error": state.last_error,
        "generation": state.generation,
        "device_count": total,
        "reachable_count": reachable,
        "unreachable_count": total - reachable,
        "host_port": load_simulator_config()["snmp"]["host_port"],
        "community": load_simulator_config()["snmp"]["community"],
        "device_cidr": load_simulator_config()["network"]["device_cidr"],
        "netflow_enabled": state.netflow_enabled,
        "netflow_version": state.netflow_version,
        "netflow_collector": f"{state.netflow_collector_host}:{state.netflow_collector_port}",
        "netflow_packets_sent": state.netflow_packets_sent,
        "netflow_last_error": state.netflow_last_error,
        "netflow_exporters": Device.objects.filter(
            reachability=Reachability.UP, netflow_enabled=True
        ).count(),
    }


def mark_stopped() -> None:
    stop_snmpsim()
    state = SimulationState.get()
    state.engine_enabled = False
    state.last_stopped_at = timezone.now()
    state.save(update_fields=["engine_enabled", "last_stopped_at"])


def daemon_loop() -> None:
    """PID 1 inside Docker: keep SNMPSim aligned with the Django inventory."""
    pid_path = run_dir() / load_simulator_config()["engine"]["daemon_pid_file"]
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    stopping = {"flag": False}

    def handle_stop(signum, _frame):
        stopping["flag"] = True

    def handle_reload(signum, _frame):
        request_reload()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, handle_reload)

    state = SimulationState.get()
    if Device.objects.exists() and state.engine_enabled:
        try:
            start_snmpsim()
            consume_reload()
        except Exception as exc:  # noqa: BLE001
            state.last_error = str(exc)
            state.save(update_fields=["last_error"])
            print(f"Failed to start SNMPSim: {exc}", file=sys.stderr)

    print("SNMP simulator supervisor is running. Use `python manage.py simulator`.", flush=True)

    last_netflow = 0.0
    while not stopping["flag"]:
        time.sleep(1)
        state = SimulationState.get()
        pid = read_pid(snmpsim_pid_path())
        alive = pid_is_running(pid)
        reload_needed = consume_reload()

        if state.engine_enabled:
            if reload_needed or not alive:
                try:
                    restart_snmpsim()
                except Exception as exc:  # noqa: BLE001
                    state.last_error = str(exc)
                    state.save(update_fields=["last_error"])
                    print(f"SNMPSim restart failed: {exc}", file=sys.stderr)
            now = time.time()
            try:
                from simulator.engine.netflow import export_netflow_tick, netflow_config

                interval = netflow_config()["interval"]
                if state.netflow_enabled and now - last_netflow >= interval:
                    stats = export_netflow_tick()
                    last_netflow = now
                    if stats.get("error"):
                        print(f"NetFlow export error: {stats['error']}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"NetFlow export failed: {exc}", file=sys.stderr)
        else:
            if alive:
                stop_snmpsim()

    mark_stopped()
    print("Supervisor stopped.", flush=True)
