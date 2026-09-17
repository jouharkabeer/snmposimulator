"""Generate SNMPSim .snmprec snapshots for a simulated device.

OID coverage is chosen so Datadog's generic IF-MIB / IP-MIB / TCP-MIB / UDP-MIB
collection and Cisco profiles (CPU, memory, chassis) have data to scrape.

Traffic counters use SNMPSim's ``numeric`` variation module so values increase
over wall-clock time instead of staying static.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from django.conf import settings

from simulator.engine.config import get_device_type
from simulator.engine.traffic import get_traffic_spec
from simulator.models import Device

# ASN.1 tags used by .snmprec
INTEGER = "2"
OCTET = "4"
OCTET_HEX = "4x"
OID = "6"
IPADDR = "64"
COUNTER32 = "65"
GAUGE = "66"
TIMETICKS = "67"
COUNTER64 = "70"


def oid_sort_key(line: str) -> tuple[int, ...]:
    oid = line.split("|", 1)[0]
    return tuple(int(part) for part in oid.split(".") if part)


def numeric(tag: str, **params) -> str:
    payload = ",".join(f"{key}={value}" for key, value in params.items() if value is not None)
    return f"{tag}:numeric|{payload}"


def rec(oid: str, tag_or_spec: str, value: str | int | float = "") -> str:
    if ":" in tag_or_spec and "|" in tag_or_spec:
        # Already a full "tag:module|params" spec passed as second arg.
        return f"{oid}|{tag_or_spec}"
    return f"{oid}|{tag_or_spec}|{value}"


def hex_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").lower()


def generate_records(device: Device) -> list[str]:
    spec = get_device_type(device.device_type)
    traffic = get_traffic_spec(device.traffic_profile)
    extra_mibs = device.extra.get("extra_mibs") or spec.get("extra_mibs") or []
    lines: list[str] = []
    lines.extend(_system_group(device))
    lines.extend(_if_tables(device, traffic))
    lines.extend(_ip_tables(device, traffic))
    lines.extend(_tcp_udp(device, traffic))
    if "cisco_cpu" in extra_mibs:
        lines.extend(_cisco_cpu(traffic))
    if "cisco_memory" in extra_mibs:
        lines.extend(_cisco_memory(traffic))
    if "cisco_chassis" in extra_mibs:
        lines.extend(_cisco_chassis(device))
    if "host_resources" in extra_mibs:
        lines.extend(_host_resources(device, traffic))
    if "ucd_snmp" in extra_mibs:
        lines.extend(_ucd_snmp(traffic))
    lines = [line for line in lines if line]
    lines.sort(key=oid_sort_key)
    return lines


def write_device_snmprec(device: Device, snmp_root: Path | None = None) -> Path:
    root = Path(snmp_root or settings.SIMULATOR_SNMP_DIR)
    device_dir = root / device.device_id
    device_dir.mkdir(parents=True, exist_ok=True)
    records = generate_records(device)
    public_path = device_dir / "public.snmprec"
    public_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    communities = root / "communities"
    communities.mkdir(parents=True, exist_ok=True)
    alias_path = communities / f"{device.community_alias}.snmprec"
    _replace_with_copy(public_path, alias_path)
    return public_path


def remove_device_snmprec(device_id: str, snmp_root: Path | None = None) -> None:
    root = Path(snmp_root or settings.SIMULATOR_SNMP_DIR)
    device_dir = root / device_id
    alias = root / "communities" / f"{device_id}.snmprec"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    if device_dir.exists():
        for child in device_dir.glob("*"):
            child.unlink()
        device_dir.rmdir()


def _replace_with_copy(src: Path, dest: Path) -> None:
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _system_group(device: Device) -> list[str]:
    return [
        rec("1.3.6.1.2.1.1.1.0", OCTET, device.sys_descr),
        rec("1.3.6.1.2.1.1.2.0", OID, device.sys_object_id),
        rec("1.3.6.1.2.1.1.3.0", numeric(TIMETICKS, rate=100, initial=8640000)),
        rec("1.3.6.1.2.1.1.4.0", OCTET, device.contact),
        rec("1.3.6.1.2.1.1.5.0", OCTET, device.hostname),
        rec("1.3.6.1.2.1.1.6.0", OCTET, device.location),
        rec("1.3.6.1.2.1.1.7.0", INTEGER, 78),  # forwarding + applications
    ]


def _if_tables(device: Device, traffic) -> list[str]:
    interfaces = device.interfaces or []
    lines = [rec("1.3.6.1.2.1.2.1.0", INTEGER, len(interfaces))]
    for iface in interfaces:
        idx = iface["index"]
        up = iface.get("oper_status", 1) == 1
        scale = float(iface.get("traffic_scale", 1.0))
        in_bps = int(traffic.in_bytes_per_sec * scale) if up else 0
        out_bps = int(traffic.out_bytes_per_sec * scale) if up else 0
        in_jitter = int(traffic.in_bytes_jitter * scale) if up else 0
        out_jitter = int(traffic.out_bytes_jitter * scale) if up else 0
        pkt_in = max(1, int(in_bps / max(traffic.avg_packet_bytes, 1))) if up else 0
        pkt_out = max(1, int(out_bps / max(traffic.avg_packet_bytes, 1))) if up else 0
        mac = iface.get("mac") or ""

        lines.extend(
            [
                rec(f"1.3.6.1.2.1.2.2.1.1.{idx}", INTEGER, idx),
                rec(f"1.3.6.1.2.1.2.2.1.2.{idx}", OCTET, iface["descr"]),
                rec(f"1.3.6.1.2.1.2.2.1.3.{idx}", INTEGER, iface["type"]),
                rec(f"1.3.6.1.2.1.2.2.1.4.{idx}", INTEGER, iface["mtu"]),
                rec(f"1.3.6.1.2.1.2.2.1.5.{idx}", GAUGE, min(int(iface["speed"]), 4294967295)),
                rec(
                    f"1.3.6.1.2.1.2.2.1.6.{idx}",
                    OCTET_HEX if mac else OCTET,
                    hex_mac(mac) if mac else "",
                ),
                rec(f"1.3.6.1.2.1.2.2.1.7.{idx}", INTEGER, iface.get("admin_status", 1)),
                rec(f"1.3.6.1.2.1.2.2.1.8.{idx}", INTEGER, iface.get("oper_status", 1)),
                rec(f"1.3.6.1.2.1.2.2.1.9.{idx}", TIMETICKS, 0),
                _counter32(f"1.3.6.1.2.1.2.2.1.10.{idx}", in_bps, in_jitter),
                _counter32(f"1.3.6.1.2.1.2.2.1.11.{idx}", pkt_in, max(1, pkt_in // 8)),
                rec(f"1.3.6.1.2.1.2.2.1.12.{idx}", COUNTER32, 0),
                _counter32(f"1.3.6.1.2.1.2.2.1.13.{idx}", traffic.in_discards_per_sec if up else 0),
                _counter32(f"1.3.6.1.2.1.2.2.1.14.{idx}", traffic.in_errors_per_sec if up else 0),
                rec(f"1.3.6.1.2.1.2.2.1.15.{idx}", COUNTER32, 0),
                _counter32(f"1.3.6.1.2.1.2.2.1.16.{idx}", out_bps, out_jitter),
                _counter32(f"1.3.6.1.2.1.2.2.1.17.{idx}", pkt_out, max(1, pkt_out // 8)),
                rec(f"1.3.6.1.2.1.2.2.1.18.{idx}", COUNTER32, 0),
                _counter32(f"1.3.6.1.2.1.2.2.1.19.{idx}", traffic.out_discards_per_sec if up else 0),
                _counter32(f"1.3.6.1.2.1.2.2.1.20.{idx}", traffic.out_errors_per_sec if up else 0),
                rec(f"1.3.6.1.2.1.2.2.1.21.{idx}", GAUGE, 0),
                rec(f"1.3.6.1.2.1.2.2.1.22.{idx}", OID, "0.0"),
                # ifXTable
                rec(f"1.3.6.1.2.1.31.1.1.1.1.{idx}", OCTET, iface["name"]),
                rec(f"1.3.6.1.2.1.31.1.1.1.2.{idx}", COUNTER32, 0),
                rec(f"1.3.6.1.2.1.31.1.1.1.3.{idx}", COUNTER32, 0),
                rec(f"1.3.6.1.2.1.31.1.1.1.4.{idx}", COUNTER32, 0),
                rec(f"1.3.6.1.2.1.31.1.1.1.5.{idx}", COUNTER32, 0),
                _counter64(f"1.3.6.1.2.1.31.1.1.1.6.{idx}", in_bps, in_jitter),
                _counter64(f"1.3.6.1.2.1.31.1.1.1.7.{idx}", pkt_in, max(1, pkt_in // 8)),
                rec(f"1.3.6.1.2.1.31.1.1.1.8.{idx}", COUNTER64, 0),
                rec(f"1.3.6.1.2.1.31.1.1.1.9.{idx}", COUNTER64, 0),
                _counter64(f"1.3.6.1.2.1.31.1.1.1.10.{idx}", out_bps, out_jitter),
                _counter64(f"1.3.6.1.2.1.31.1.1.1.11.{idx}", pkt_out, max(1, pkt_out // 8)),
                rec(f"1.3.6.1.2.1.31.1.1.1.12.{idx}", COUNTER64, 0),
                rec(f"1.3.6.1.2.1.31.1.1.1.13.{idx}", COUNTER64, 0),
                rec(f"1.3.6.1.2.1.31.1.1.1.14.{idx}", INTEGER, 1),  # ifLinkUpDownTrapEnable
                rec(f"1.3.6.1.2.1.31.1.1.1.15.{idx}", GAUGE, iface.get("high_speed", 0)),
                rec(f"1.3.6.1.2.1.31.1.1.1.16.{idx}", INTEGER, 1),  # ifPromiscuousMode false-ish
                rec(f"1.3.6.1.2.1.31.1.1.1.17.{idx}", INTEGER, 2),  # ifConnectorPresent true=1; 2=false for virtual
                rec(f"1.3.6.1.2.1.31.1.1.1.18.{idx}", OCTET, iface.get("alias", iface["name"])),
                rec(f"1.3.6.1.2.1.31.1.1.1.19.{idx}", TIMETICKS, 0),
            ]
        )
        if iface.get("type") == 6:
            # Physical ethernet should report connector present.
            lines[-2] = rec(f"1.3.6.1.2.1.31.1.1.1.17.{idx}", INTEGER, 1)
    return lines


def _counter32(oid: str, rate: float, jitter: float = 0) -> str:
    params = {
        "cumulative": 1,
        "wrap": 1,
        "rate": 1,
        "offset": max(rate, 0),
        "initial": 1000,
    }
    if jitter:
        params.update(function="sin", scale=jitter)
    return rec(oid, numeric(COUNTER32, **params))


def _counter64(oid: str, rate: float, jitter: float = 0) -> str:
    params = {
        "cumulative": 1,
        "wrap": 1,
        "rate": 1,
        "offset": max(rate, 0),
        "initial": 10000,
    }
    if jitter:
        params.update(function="sin", scale=jitter)
    return rec(oid, numeric(COUNTER64, **params))


def _ip_tables(device: Device, traffic) -> list[str]:
    in_rate = traffic.in_bytes_per_sec // 800
    out_rate = traffic.out_bytes_per_sec // 800
    lines = [
        rec("1.3.6.1.2.1.4.1.0", INTEGER, 1),  # ipForwarding forwarding
        rec("1.3.6.1.2.1.4.2.0", INTEGER, 64),
        _counter32("1.3.6.1.2.1.4.3.0", in_rate, in_rate // 5),
        rec("1.3.6.1.2.1.4.4.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.4.5.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.4.6.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.4.7.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.4.8.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.4.9.0", COUNTER32, 0),
        _counter32("1.3.6.1.2.1.4.10.0", out_rate, out_rate // 5),
        rec("1.3.6.1.2.1.4.11.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.4.12.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.4.17.0", GAUGE, 4),
    ]
    ip = device.ip_address
    lines.extend(
        [
            rec(f"1.3.6.1.2.1.4.20.1.1.{ip}", IPADDR, ip),
            rec(f"1.3.6.1.2.1.4.20.1.2.{ip}", INTEGER, 1),
            rec(f"1.3.6.1.2.1.4.20.1.3.{ip}", IPADDR, "255.255.255.0"),
        ]
    )
    return lines


def _tcp_udp(device: Device, traffic) -> list[str]:
    estab = {"high": 85, "medium": 32, "low": 8}[device.traffic_profile]
    return [
        rec("1.3.6.1.2.1.6.5.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=2, rate=1, initial=40)),
        rec("1.3.6.1.2.1.6.6.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=2, rate=1, initial=30)),
        rec("1.3.6.1.2.1.6.7.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=8, rate=1, initial=200)),
        rec("1.3.6.1.2.1.6.8.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=8, rate=1, initial=180)),
        rec("1.3.6.1.2.1.6.9.0", numeric(GAUGE, min=1, max=200, initial=estab, function="sin", rate=0.03, scale=10, offset=estab)),
        rec("1.3.6.1.2.1.6.10.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=20, rate=1, initial=500)),
        rec("1.3.6.1.2.1.6.11.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=20, rate=1, initial=500)),
        rec("1.3.6.1.2.1.6.12.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.6.14.0", GAUGE, 0),
        rec("1.3.6.1.2.1.6.15.0", GAUGE, 0),
        rec("1.3.6.1.2.1.7.1.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=15, rate=1, initial=300)),
        rec("1.3.6.1.2.1.7.2.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.7.3.0", COUNTER32, 0),
        rec("1.3.6.1.2.1.7.4.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=15, rate=1, initial=280)),
        rec("1.3.6.1.2.1.7.5.0", GAUGE, 0),
    ]


def _cisco_cpu(traffic) -> list[str]:
    cpu = traffic.cpu_offset
    scale = traffic.cpu_scale
    return [
        rec("1.3.6.1.4.1.9.9.109.1.1.1.1.2.1", INTEGER, 1),
        rec("1.3.6.1.4.1.9.9.109.1.1.1.1.3.1", numeric(GAUGE, min=1, max=99, initial=cpu, function="sin", rate=0.04, scale=scale, offset=cpu)),
        rec("1.3.6.1.4.1.9.9.109.1.1.1.1.4.1", numeric(GAUGE, min=1, max=99, initial=cpu, function="sin", rate=0.03, scale=scale, offset=cpu)),
        rec("1.3.6.1.4.1.9.9.109.1.1.1.1.5.1", numeric(GAUGE, min=1, max=99, initial=cpu, function="sin", rate=0.02, scale=max(scale - 4, 2), offset=cpu)),
        rec("1.3.6.1.4.1.9.9.109.1.1.1.1.6.1", numeric(GAUGE, min=1, max=99, initial=cpu, function="sin", rate=0.04, scale=scale, offset=cpu)),
        rec("1.3.6.1.4.1.9.9.109.1.1.1.1.7.1", numeric(GAUGE, min=1, max=99, initial=cpu, function="sin", rate=0.03, scale=scale, offset=cpu)),
        rec("1.3.6.1.4.1.9.9.109.1.1.1.1.8.1", numeric(GAUGE, min=1, max=99, initial=cpu, function="sin", rate=0.02, scale=max(scale - 4, 2), offset=cpu)),
    ]


def _cisco_memory(traffic) -> list[str]:
    total = 512_000_000
    used = int(total * traffic.memory_used_ratio)
    free = total - used
    used_jitter = int(used * 0.05)
    return [
        rec("1.3.6.1.4.1.9.9.48.1.1.1.2.1", OCTET, "Processor"),
        rec("1.3.6.1.4.1.9.9.48.1.1.1.2.2", OCTET, "I/O"),
        rec("1.3.6.1.4.1.9.9.48.1.1.1.3.1", GAUGE, 1),
        rec("1.3.6.1.4.1.9.9.48.1.1.1.3.2", GAUGE, 1),
        rec("1.3.6.1.4.1.9.9.48.1.1.1.5.1", numeric(GAUGE, min=1, max=total, initial=used, function="sin", rate=0.02, scale=used_jitter, offset=used)),
        rec("1.3.6.1.4.1.9.9.48.1.1.1.5.2", numeric(GAUGE, min=1, max=total // 4, initial=used // 4, function="sin", rate=0.02, scale=used_jitter // 4, offset=used // 4)),
        rec("1.3.6.1.4.1.9.9.48.1.1.1.6.1", numeric(GAUGE, min=1, max=total, initial=free, function="sin", rate=0.02, scale=used_jitter, offset=free)),
        rec("1.3.6.1.4.1.9.9.48.1.1.1.6.2", numeric(GAUGE, min=1, max=total // 4, initial=free // 4, function="sin", rate=0.02, scale=used_jitter // 4, offset=free // 4)),
    ]


def _cisco_chassis(device: Device) -> list[str]:
    return [
        rec("1.3.6.1.4.1.9.3.6.3.0", OCTET, device.serial_number),
        rec("1.3.6.1.4.1.9.3.6.1.0", OCTET, device.model),
        rec("1.3.6.1.4.1.9.3.6.6.0", INTEGER, 5),  # ram in MB-ish
    ]


def _host_resources(device: Device, traffic) -> list[str]:
    return [
        rec("1.3.6.1.2.1.25.1.1.0", numeric(TIMETICKS, rate=100, initial=8640000)),
        rec("1.3.6.1.2.1.25.1.2.0", OCTET_HEX, "07e80a0e0c1e00"),  # dummy date
        rec("1.3.6.1.2.1.25.1.3.0", INTEGER, 32),
        rec("1.3.6.1.2.1.25.1.4.0", OCTET, "BOOT_IMAGE=/vmlinuz ro"),
        rec("1.3.6.1.2.1.25.1.5.0", GAUGE, 3),
        rec("1.3.6.1.2.1.25.1.6.0", GAUGE, 128),
        rec("1.3.6.1.2.1.25.1.7.0", INTEGER, 0),
    ]


def _ucd_snmp(traffic) -> list[str]:
    idle = max(1, 100 - traffic.cpu_offset)
    mem_total = 8192000  # kB
    mem_avail = int(mem_total * (1 - traffic.memory_used_ratio))
    return [
        rec("1.3.6.1.4.1.2021.4.5.0", INTEGER, mem_total),
        rec("1.3.6.1.4.1.2021.4.6.0", numeric(INTEGER, min=1, max=mem_total, initial=mem_avail, function="sin", rate=0.02, scale=int(mem_total * 0.04), offset=mem_avail)),
        rec("1.3.6.1.4.1.2021.11.9.0", numeric(INTEGER, min=1, max=99, initial=traffic.cpu_offset, function="sin", rate=0.04, scale=traffic.cpu_scale, offset=traffic.cpu_offset)),
        rec("1.3.6.1.4.1.2021.11.10.0", INTEGER, 5),
        rec("1.3.6.1.4.1.2021.11.11.0", numeric(INTEGER, min=1, max=99, initial=idle, function="sin", rate=0.04, scale=traffic.cpu_scale, offset=idle)),
        rec("1.3.6.1.4.1.2021.11.50.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=20, rate=1, initial=1000)),
        rec("1.3.6.1.4.1.2021.11.52.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=5, rate=1, initial=200)),
        rec("1.3.6.1.4.1.2021.11.53.0", numeric(COUNTER32, cumulative=1, wrap=1, offset=300, rate=1, initial=5000)),
    ]


def iter_required_oids() -> Iterable[str]:
    return (
        "1.3.6.1.2.1.1.1.0",
        "1.3.6.1.2.1.1.2.0",
        "1.3.6.1.2.1.1.3.0",
        "1.3.6.1.2.1.1.5.0",
        "1.3.6.1.2.1.2.1.0",
        "1.3.6.1.2.1.2.2.1.2.1",
        "1.3.6.1.2.1.31.1.1.1.6.1",
        "1.3.6.1.2.1.31.1.1.1.10.1",
    )
