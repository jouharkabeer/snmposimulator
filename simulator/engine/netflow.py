"""Build and export NetFlow v5 / v9 packets for simulated devices.

Reachable devices send UDP NetFlow to a collector (typically the Datadog Agent
on the Ubuntu host). Volume follows HIGH / MEDIUM / LOW traffic profiles.
Unreachable devices do not export.
"""

from __future__ import annotations

import ipaddress
import os
import random
import socket
import struct
import time
from dataclasses import dataclass
from typing import Iterable

from simulator.engine.config import load_simulator_config
from simulator.engine.ipam import parse_device_id
from simulator.engine.traffic import get_traffic_spec
from simulator.models import Device, Reachability, SimulationState

# Cisco NetFlow v9 field types used by Datadog / goflow collectors.
NF9_IN_BYTES = 1
NF9_IN_PKTS = 2
NF9_PROTOCOL = 4
NF9_TOS = 5
NF9_TCP_FLAGS = 6
NF9_L4_SRC_PORT = 7
NF9_IPV4_SRC_ADDR = 8
NF9_INPUT_SNMP = 10
NF9_L4_DST_PORT = 11
NF9_IPV4_DST_ADDR = 12
NF9_OUTPUT_SNMP = 14
NF9_IPV4_NEXT_HOP = 15
NF9_LAST_SWITCHED = 21
NF9_FIRST_SWITCHED = 22
NF9_DIRECTION = 61

TEMPLATE_ID = 256
TEMPLATE_FIELDS = (
    (NF9_IPV4_SRC_ADDR, 4),
    (NF9_IPV4_DST_ADDR, 4),
    (NF9_IPV4_NEXT_HOP, 4),
    (NF9_INPUT_SNMP, 2),
    (NF9_OUTPUT_SNMP, 2),
    (NF9_IN_PKTS, 4),
    (NF9_IN_BYTES, 4),
    (NF9_FIRST_SWITCHED, 4),
    (NF9_LAST_SWITCHED, 4),
    (NF9_L4_SRC_PORT, 2),
    (NF9_L4_DST_PORT, 2),
    (NF9_PROTOCOL, 1),
    (NF9_TOS, 1),
    (NF9_TCP_FLAGS, 1),
    (NF9_DIRECTION, 1),
)
TEMPLATE_RECORD_SIZE = sum(length for _kind, length in TEMPLATE_FIELDS)

# Well-known conversation endpoints so Datadog graphs look like a real network.
PEER_HOSTS = (
    "8.8.8.8",
    "1.1.1.1",
    "9.9.9.9",
    "10.0.10.10",
    "10.0.10.20",
    "10.0.20.5",
    "172.16.8.50",
    "172.16.8.80",
)
TCP_PORTS = (443, 80, 22, 8080, 3389, 25, 993)
UDP_PORTS = (53, 123, 161, 514, 443)

BOOT_MONOTONIC = time.monotonic()
_sequences: dict[str, int] = {}
_exports_since_template: dict[str, int] = {}
_last_tick_stats: dict[str, object] = {
    "packets": 0,
    "flows": 0,
    "devices": 0,
    "error": "",
}


@dataclass(frozen=True)
class Flow:
    src: str
    dst: str
    nexthop: str
    input_if: int
    output_if: int
    packets: int
    octets: int
    first_ms: int
    last_ms: int
    src_port: int
    dst_port: int
    protocol: int
    tos: int
    tcp_flags: int
    direction: int  # 0 ingress, 1 egress


def sys_uptime_ms() -> int:
    return int((time.monotonic() - BOOT_MONOTONIC) * 1000)


def ip_to_bytes(addr: str) -> bytes:
    return ipaddress.IPv4Address(addr).packed


def netflow_config() -> dict:
    cfg = load_simulator_config().get("netflow") or {}
    state = SimulationState.get()
    host = (
        state.netflow_collector_host
        or os.environ.get("NETFLOW_COLLECTOR_HOST")
        or cfg.get("collector_host")
        or "host.docker.internal"
    )
    port = (
        state.netflow_collector_port
        or int(os.environ.get("NETFLOW_COLLECTOR_PORT") or 0)
        or int(cfg.get("collector_port") or 2055)
    )
    version = state.netflow_version or int(os.environ.get("NETFLOW_VERSION") or cfg.get("version") or 9)
    interval = int(
        os.environ.get("NETFLOW_EXPORT_INTERVAL")
        or cfg.get("export_interval_seconds")
        or 5
    )
    return {
        "enabled": state.netflow_enabled,
        "host": host,
        "port": int(port),
        "version": int(version),
        "interval": max(1, interval),
    }


def next_sequence(device_id: str) -> int:
    _sequences[device_id] = _sequences.get(device_id, 0) + 1
    return _sequences[device_id]


def encode_netflow_v5(flows: list[Flow], *, uptime_ms: int, unix_secs: int, sequence: int, engine_id: int) -> bytes:
    count = len(flows)
    header = struct.pack(
        "!HHIIIIBBH",
        5,
        count,
        uptime_ms & 0xFFFFFFFF,
        unix_secs,
        0,
        sequence & 0xFFFFFFFF,
        0,
        engine_id & 0xFF,
        0,
    )
    records = bytearray()
    for flow in flows:
        records.extend(
            struct.pack(
                "!4s4s4sHHIIIIHHBBBBHHBBH",
                ip_to_bytes(flow.src),
                ip_to_bytes(flow.dst),
                ip_to_bytes(flow.nexthop),
                flow.input_if & 0xFFFF,
                flow.output_if & 0xFFFF,
                flow.packets & 0xFFFFFFFF,
                flow.octets & 0xFFFFFFFF,
                flow.first_ms & 0xFFFFFFFF,
                flow.last_ms & 0xFFFFFFFF,
                flow.src_port & 0xFFFF,
                flow.dst_port & 0xFFFF,
                0,
                flow.protocol & 0xFF,
                flow.tos & 0xFF,
                flow.tcp_flags & 0xFF,
                0,
                0,
                24,
                24,
                0,
            )
        )
    return header + bytes(records)


def _template_flowset() -> bytes:
    body = struct.pack("!HH", TEMPLATE_ID, len(TEMPLATE_FIELDS))
    for field_type, length in TEMPLATE_FIELDS:
        body += struct.pack("!HH", field_type, length)
    length = 4 + len(body)
    if length % 4:
        pad = 4 - (length % 4)
        body += b"\x00" * pad
        length += pad
    return struct.pack("!HH", 0, length) + body


def _data_flowset(flows: list[Flow]) -> bytes:
    payload = bytearray()
    for flow in flows:
        payload.extend(ip_to_bytes(flow.src))
        payload.extend(ip_to_bytes(flow.dst))
        payload.extend(ip_to_bytes(flow.nexthop))
        payload.extend(struct.pack("!HH", flow.input_if & 0xFFFF, flow.output_if & 0xFFFF))
        payload.extend(struct.pack("!II", flow.packets & 0xFFFFFFFF, flow.octets & 0xFFFFFFFF))
        payload.extend(struct.pack("!II", flow.first_ms & 0xFFFFFFFF, flow.last_ms & 0xFFFFFFFF))
        payload.extend(struct.pack("!HH", flow.src_port & 0xFFFF, flow.dst_port & 0xFFFF))
        payload.extend(
            struct.pack(
                "!BBBB",
                flow.protocol & 0xFF,
                flow.tos & 0xFF,
                flow.tcp_flags & 0xFF,
                flow.direction & 0xFF,
            )
        )
    length = 4 + len(payload)
    if length % 4:
        pad = 4 - (length % 4)
        payload.extend(b"\x00" * pad)
        length += pad
    return struct.pack("!HH", TEMPLATE_ID, length) + bytes(payload)


def encode_netflow_v9(
    flows: list[Flow],
    *,
    uptime_ms: int,
    unix_secs: int,
    sequence: int,
    source_id: int,
    include_template: bool,
) -> bytes:
    flowsets = bytearray()
    record_count = 0
    if include_template:
        flowsets.extend(_template_flowset())
        record_count += 1
    flowsets.extend(_data_flowset(flows))
    record_count += len(flows)
    header = struct.pack(
        "!HHIIII",
        9,
        record_count,
        uptime_ms & 0xFFFFFFFF,
        unix_secs,
        sequence & 0xFFFFFFFF,
        source_id & 0xFFFFFFFF,
    )
    return header + bytes(flowsets)


def _active_interfaces(device: Device) -> list[int]:
    indexes = [
        int(iface["index"])
        for iface in (device.interfaces or [])
        if iface.get("oper_status", 1) == 1 and iface.get("type") != 24
    ]
    return indexes or [1]


def generate_flows(device: Device, interval: int) -> list[Flow]:
    spec = get_traffic_spec(device.traffic_profile)
    seed = parse_device_id(device.device_id) if device.device_id else 1
    rng = random.Random(seed * 1009 + int(time.time() // max(interval, 1)))
    profile_flows = {"high": 24, "medium": 12, "low": 4}
    count = profile_flows.get(device.traffic_profile, 8)
    ifaces = _active_interfaces(device)
    uptime = sys_uptime_ms()
    total_bytes = int((spec.in_bytes_per_sec + spec.out_bytes_per_sec) * interval * 0.15)
    total_bytes = max(total_bytes, count * 800)
    flows: list[Flow] = []
    for index in range(count):
        outbound = index % 3 != 0
        peer = PEER_HOSTS[(seed + index) % len(PEER_HOSTS)]
        proto_roll = rng.random()
        if proto_roll < 0.08:
            protocol, src_port, dst_port, flags = 1, 0, 0, 0
        elif proto_roll < 0.28:
            protocol = 17
            src_port = rng.randint(1024, 60999)
            dst_port = UDP_PORTS[index % len(UDP_PORTS)]
            flags = 0
        else:
            protocol = 6
            src_port = rng.randint(1024, 60999)
            dst_port = TCP_PORTS[index % len(TCP_PORTS)]
            flags = 0x18  # PSH+ACK
        if outbound:
            src, dst = device.ip_address, peer
            in_if, out_if = ifaces[0], ifaces[index % len(ifaces)]
            direction = 1
        else:
            src, dst = peer, device.ip_address
            in_if, out_if = ifaces[index % len(ifaces)], ifaces[0]
            direction = 0
        share = 0.5 / count + (0.5 / count) * rng.random()
        octets = max(64, int(total_bytes * share))
        packets = max(1, octets // max(spec.avg_packet_bytes, 64))
        duration = rng.randint(200, max(interval * 1000 - 50, 250))
        last_ms = uptime
        first_ms = max(0, last_ms - duration)
        nexthop = "10.200.0.1" if outbound else device.ip_address
        flows.append(
            Flow(
                src=src,
                dst=dst,
                nexthop=nexthop,
                input_if=in_if,
                output_if=out_if,
                packets=packets,
                octets=octets,
                first_ms=first_ms,
                last_ms=last_ms,
                src_port=src_port,
                dst_port=dst_port,
                protocol=protocol,
                tos=0,
                tcp_flags=flags,
                direction=direction,
            )
        )
    return flows


def _bind_exporter_socket(device_ip: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    for candidate in ((device_ip, 0), ("0.0.0.0", 0)):
        try:
            sock.bind(candidate)
            return sock
        except OSError:
            continue
    return sock


def export_device(device: Device, cfg: dict) -> tuple[int, int]:
    """Return (packets_sent, flows_sent)."""
    if not device.is_reachable or not device.netflow_enabled:
        return 0, 0
    flows = generate_flows(device, cfg["interval"])
    if not flows:
        return 0, 0
    uptime = sys_uptime_ms()
    unix_secs = int(time.time())
    engine_id = parse_device_id(device.device_id) & 0xFF
    version = cfg["version"]
    chunks = [flows[i : i + 24] for i in range(0, len(flows), 24)]
    packets = 0
    sock = _bind_exporter_socket(device.ip_address)
    try:
        for chunk in chunks:
            sequence = next_sequence(device.device_id)
            if version == 5:
                payload = encode_netflow_v5(
                    chunk,
                    uptime_ms=uptime,
                    unix_secs=unix_secs,
                    sequence=sequence,
                    engine_id=engine_id,
                )
            else:
                _exports_since_template[device.device_id] = (
                    _exports_since_template.get(device.device_id, 0) + 1
                )
                include_template = _exports_since_template[device.device_id] % 3 == 1
                payload = encode_netflow_v9(
                    chunk,
                    uptime_ms=uptime,
                    unix_secs=unix_secs,
                    sequence=sequence,
                    source_id=engine_id,
                    include_template=include_template,
                )
            sock.sendto(payload, (cfg["host"], cfg["port"]))
            packets += 1
    finally:
        sock.close()
    return packets, len(flows)


def export_netflow_tick() -> dict:
    cfg = netflow_config()
    stats = {"packets": 0, "flows": 0, "devices": 0, "error": ""}
    if not cfg["enabled"]:
        _last_tick_stats.update(stats)
        return stats
    state = SimulationState.get()
    devices = Device.objects.filter(reachability=Reachability.UP, netflow_enabled=True)
    try:
        for device in devices:
            packets, flows = export_device(device, cfg)
            stats["packets"] += packets
            stats["flows"] += flows
            if packets:
                stats["devices"] += 1
        state.netflow_packets_sent = (state.netflow_packets_sent or 0) + stats["packets"]
        state.netflow_last_error = ""
        state.save(update_fields=["netflow_packets_sent", "netflow_last_error"])
    except OSError as exc:
        stats["error"] = str(exc)
        state.netflow_last_error = str(exc)
        state.save(update_fields=["netflow_last_error"])
    _last_tick_stats.update(stats)
    return stats


def last_tick_stats() -> dict:
    return dict(_last_tick_stats)


def exporting_devices() -> Iterable[Device]:
    return Device.objects.filter(reachability=Reachability.UP, netflow_enabled=True)
