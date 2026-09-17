"""Allocate unique device IDs and IPv4 addresses for simulated agents."""

from __future__ import annotations

import ipaddress
from typing import Iterable

from simulator.engine.config import load_simulator_config


def format_device_id(number: int, width: int | None = None) -> str:
    cfg = load_simulator_config()
    prefix = cfg["identity"]["id_prefix"]
    width = width or int(cfg["identity"]["id_width"])
    return f"{prefix}{number:0{width}d}"


def parse_device_id(device_id: str) -> int:
    cfg = load_simulator_config()
    prefix = cfg["identity"]["id_prefix"]
    if not device_id.startswith(prefix):
        raise ValueError(f"Device id '{device_id}' does not start with '{prefix}'.")
    return int(device_id[len(prefix) :])


def next_device_number(existing_ids: Iterable[str]) -> int:
    numbers = []
    for device_id in existing_ids:
        try:
            numbers.append(parse_device_id(device_id))
        except ValueError:
            continue
    return (max(numbers) + 1) if numbers else 1


def device_network() -> ipaddress.IPv4Network:
    cfg = load_simulator_config()
    return ipaddress.IPv4Network(cfg["network"]["device_cidr"], strict=False)


def iter_device_ips():
    network = device_network()
    for host in network.hosts():
        yield str(host)


def next_ip_address(existing_ips: Iterable[str]) -> str:
    used = {str(ipaddress.ip_address(ip)) for ip in existing_ips}
    for candidate in iter_device_ips():
        if candidate not in used:
            return candidate
    raise RuntimeError(
        f"No free IP addresses left in {device_network()}. "
        "Increase device_cidr in config/simulator.yaml."
    )


def mac_from_ip(ip_address: str, nic_index: int = 1) -> str:
    """Deterministic locally-administered MAC derived from the device IP."""
    octets = [int(part) for part in ip_address.split(".")]
    return "02:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}".format(
        octets[1] & 0xFF,
        octets[2] & 0xFF,
        octets[3] & 0xFF,
        nic_index & 0xFF,
        (octets[3] + nic_index) & 0xFF,
    )


def serial_from_device_id(device_id: str, vendor: str) -> str:
    number = parse_device_id(device_id)
    prefix = "".join(ch for ch in vendor.upper() if ch.isalnum())[:3] or "LAB"
    return f"{prefix}{number:08d}"
