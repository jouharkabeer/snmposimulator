"""Create realistic device records from operator-supplied counts."""

from __future__ import annotations

import random
from typing import Any

from simulator.engine.config import available_device_types, get_device_type, load_simulator_config
from simulator.engine.interfaces import build_interfaces
from simulator.engine.ipam import (
    format_device_id,
    mac_from_ip,
    next_device_number,
    next_ip_address,
    serial_from_device_id,
)
from simulator.engine.validation import CategoryCounts
from simulator.models import Device, DeviceType, Reachability, TrafficProfile


def default_type_rotation() -> list[str]:
    known = available_device_types()
    preferred = [DeviceType.ROUTER, DeviceType.SWITCH, DeviceType.FIREWALL, DeviceType.GENERIC]
    ordered = [item for item in preferred if item in known]
    ordered.extend(item for item in known if item not in ordered)
    return ordered or known


def assign_profiles(counts: CategoryCounts) -> list[tuple[str, str]]:
    """Return (traffic_profile, reachability) assignments for ``counts.total`` devices."""
    assignments: list[tuple[str, str]] = (
        [(TrafficProfile.HIGH, Reachability.UP)] * counts.high
        + [(TrafficProfile.MEDIUM, Reachability.UP)] * counts.medium
        + [(TrafficProfile.LOW, Reachability.UP)] * counts.low
        + [(TrafficProfile.LOW, Reachability.DOWN)] * counts.unreachable
    )
    if len(assignments) != counts.total:
        raise RuntimeError("Internal error: assignment length mismatch.")
    return assignments


def build_device_payload(
    *,
    device_id: str,
    ip_address: str,
    device_type: str,
    traffic_profile: str,
    reachability: str,
    sequence: int,
) -> dict[str, Any]:
    spec = get_device_type(device_type)
    cfg = load_simulator_config()
    domain = cfg["identity"]["domain"]
    patterns = spec.get("name_patterns") or [f"{device_type}-{{nn}}"]
    pattern = patterns[(sequence - 1) % len(patterns)]
    short_name = pattern.format(nn=f"{sequence:02d}")
    hostname = f"{short_name}.{domain}"
    count_range = spec.get("interface_count") or [4, 6]
    if isinstance(count_range, int):
        iface_count = count_range
    else:
        low, high = int(count_range[0]), int(count_range[-1])
        rng = random.Random(parse_seed(device_id))
        iface_count = rng.randint(low, high)

    interfaces = build_interfaces(
        device_type=device_type,
        style=spec.get("interface_style", "linux"),
        ip_address=ip_address,
        traffic_profile=traffic_profile,
        count=iface_count,
        seed=parse_seed(device_id),
    )
    return {
        "device_id": device_id,
        "name": short_name,
        "hostname": hostname,
        "ip_address": ip_address,
        "snmp_port": int(cfg["snmp"]["port"]),
        "snmp_version": cfg["snmp"]["version"],
        "community": cfg["snmp"]["community"],
        "community_alias": device_id,
        "device_type": spec["key"],
        "vendor": spec["vendor"],
        "model": spec["model"],
        "sys_object_id": spec["sys_object_id"],
        "sys_descr": " ".join(str(spec["sys_descr"]).split()),
        "location": cfg["identity"]["location"],
        "contact": cfg["identity"]["contact"],
        "serial_number": serial_from_device_id(device_id, spec["vendor"]),
        "traffic_profile": traffic_profile,
        "reachability": reachability,
        "interface_count": len(interfaces),
        "interfaces": interfaces,
        "extra": {
            "mac": mac_from_ip(ip_address, 1),
            "extra_mibs": spec.get("extra_mibs", []),
            "metadata_type": spec.get("metadata_type", spec["key"]),
        },
    }


def parse_seed(device_id: str) -> int:
    digits = "".join(ch for ch in device_id if ch.isdigit())
    return int(digits) if digits else 1


def create_lab_devices(counts: CategoryCounts) -> list[Device]:
    existing_ids = list(Device.objects.values_list("device_id", flat=True))
    existing_ips = list(Device.objects.values_list("ip_address", flat=True))
    start_number = next_device_number(existing_ids)
    type_order = default_type_rotation()
    assignments = assign_profiles(counts)
    created: list[Device] = []

    used_ips = set(existing_ips)
    for index, (traffic, reachability) in enumerate(assignments):
        number = start_number + index
        device_id = format_device_id(number)
        ip_address = next_ip_address(used_ips)
        used_ips.add(ip_address)
        device_type = type_order[index % len(type_order)]
        payload = build_device_payload(
            device_id=device_id,
            ip_address=ip_address,
            device_type=device_type,
            traffic_profile=traffic,
            reachability=reachability,
            sequence=number,
        )
        created.append(Device.objects.create(**payload))
    return created


def create_single_device(
    *,
    device_type: str | None = None,
    traffic_profile: str = TrafficProfile.MEDIUM,
    reachability: str = Reachability.UP,
    name: str | None = None,
) -> Device:
    existing_ids = list(Device.objects.values_list("device_id", flat=True))
    existing_ips = list(Device.objects.values_list("ip_address", flat=True))
    number = next_device_number(existing_ids)
    device_id = format_device_id(number)
    ip_address = next_ip_address(existing_ips)
    chosen_type = device_type or default_type_rotation()[(number - 1) % len(default_type_rotation())]
    payload = build_device_payload(
        device_id=device_id,
        ip_address=ip_address,
        device_type=chosen_type,
        traffic_profile=traffic_profile,
        reachability=reachability,
        sequence=number,
    )
    if name:
        payload["name"] = name
        domain = load_simulator_config()["identity"]["domain"]
        payload["hostname"] = f"{name}.{domain}" if "." not in name else name
    return Device.objects.create(**payload)
