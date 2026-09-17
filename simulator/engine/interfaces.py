"""Build realistic interface inventories per device type and traffic profile."""

from __future__ import annotations

import random
from typing import Any

from simulator.engine.ipam import mac_from_ip
from simulator.engine.traffic import get_traffic_spec


def build_interfaces(
    *,
    device_type: str,
    style: str,
    ip_address: str,
    traffic_profile: str,
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    spec = get_traffic_spec(traffic_profile)
    names = _interface_names(style, count)
    interfaces: list[dict[str, Any]] = []

    for index, name in enumerate(names, start=1):
        is_loopback = name.lower().startswith("lo") or name.lower().startswith("loopback")
        is_mgmt = "mgmt" in name.lower() or name.lower().startswith("management")
        # Leave one edge port down so Datadog shows mixed interface status.
        forced_down = index == count and count > 3 and not is_loopback and not is_mgmt
        oper_up = not forced_down
        admin_up = oper_up or rng.random() > 0.3

        if is_loopback:
            if_type = 24  # softwareLoopback
            speed = 0
            high_speed = 0
            mtu = 1514
            mac = ""
        elif is_mgmt:
            if_type = 6
            speed = 1_000_000_000
            high_speed = 1000
            mtu = 1500
            mac = mac_from_ip(ip_address, index)
        else:
            if_type = 6  # ethernetCsmacd
            speed = spec.if_speed_bps
            high_speed = spec.if_high_speed_mbps
            mtu = 1500
            mac = mac_from_ip(ip_address, index)

        aliases = {
            1: "uplink-to-core",
            2: "downlink-access",
            3: "wan-circuit",
        }
        alias = "loopback" if is_loopback else aliases.get(index, f"{name}")
        if is_mgmt:
            alias = "management"
        if forced_down:
            alias = "unused-port"

        interfaces.append(
            {
                "index": index,
                "name": name,
                "descr": name,
                "alias": alias,
                "type": if_type,
                "mtu": mtu,
                "speed": speed,
                "high_speed": high_speed,
                "mac": mac,
                "admin_status": 1 if admin_up else 2,
                "oper_status": 1 if oper_up else 2,
                "ip_address": ip_address if index == 1 and not is_loopback else "",
                "netmask": "255.255.255.0" if index == 1 and not is_loopback else "",
                "traffic_scale": round(0.55 + rng.random() * 0.7, 3),
            }
        )
    return interfaces


def _interface_names(style: str, count: int) -> list[str]:
    if style == "cisco_router":
        names = ["Loopback0"]
        names.extend(f"GigabitEthernet0/{i}" for i in range(0, max(count - 1, 1)))
        return names[:count]
    if style == "cisco_switch":
        names = ["Vlan1"]
        names.extend(f"GigabitEthernet1/0/{i}" for i in range(1, max(count, 2)))
        return names[:count]
    if style == "cisco_firewall":
        named = ["Management0/0", "GigabitEthernet0/0", "GigabitEthernet0/1", "GigabitEthernet0/2"]
        extras = [f"GigabitEthernet0/{i}" for i in range(3, count)]
        return (named + extras)[:count]
    # linux / generic
    names = ["lo"]
    names.extend(f"eth{i}" for i in range(0, max(count - 1, 1)))
    return names[:count]
