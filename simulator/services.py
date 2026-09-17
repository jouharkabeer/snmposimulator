"""High-level operations used by the Django management command."""

from __future__ import annotations

from simulator.engine.config import get_device_type
from simulator.engine.datadog_export import write_datadog_exports
from simulator.engine.factory import create_lab_devices, create_single_device
from simulator.engine.snmprec import write_device_snmprec
from simulator.engine.supervisor import (
    engine_status,
    mark_stopped,
    request_reload,
)
from simulator.engine.validation import CategoryCounts, validate_counts
from simulator.models import Device, Reachability, SimulationState, TrafficProfile


def start_simulation(counts: CategoryCounts, *, replace: bool = False) -> list[Device]:
    if Device.objects.exists() and not replace:
        raise RuntimeError(
            "A lab already exists. Use `simulator start --replace` or `simulator reset` "
            "before creating a new one, or use `simulator add` to grow it."
        )
    if replace and Device.objects.exists():
        Device.objects.all().delete()
    devices = create_lab_devices(counts)
    state = SimulationState.get()
    state.engine_enabled = True
    state.last_error = ""
    state.save(update_fields=["engine_enabled", "last_error"])
    for device in devices:
        write_device_snmprec(device)
    write_datadog_exports()
    request_reload()
    return devices


def stop_simulation() -> None:
    mark_stopped()


def reset_simulation() -> None:
    Device.objects.all().delete()
    mark_stopped()
    from simulator.engine.supervisor import rebuild_all_snmprec

    rebuild_all_snmprec()
    write_datadog_exports()


def add_device(**kwargs) -> Device:
    device = create_single_device(**kwargs)
    write_device_snmprec(device)
    write_datadog_exports()
    if SimulationState.get().engine_enabled:
        request_reload()
    return device


def remove_device(device: Device) -> str:
    device_id = device.device_id
    from simulator.engine.snmprec import remove_device_snmprec

    remove_device_snmprec(device_id)
    device.delete()
    write_datadog_exports()
    if SimulationState.get().engine_enabled:
        request_reload()
    return device_id


def set_traffic(device: Device, profile: str) -> Device:
    key = profile.lower()
    if key not in TrafficProfile.values:
        raise ValueError(f"Traffic profile must be one of: {', '.join(TrafficProfile.values)}")
    device.traffic_profile = key
    # Rebuild interfaces so speeds / unused ports match the new profile.
    from simulator.engine.factory import build_device_payload

    payload = build_device_payload(
        device_id=device.device_id,
        ip_address=device.ip_address,
        device_type=device.device_type,
        traffic_profile=key,
        reachability=device.reachability,
        sequence=int("".join(ch for ch in device.device_id if ch.isdigit()) or "1"),
    )
    device.interfaces = payload["interfaces"]
    device.interface_count = payload["interface_count"]
    device.save()
    write_device_snmprec(device)
    write_datadog_exports()
    if SimulationState.get().engine_enabled:
        request_reload()
    return device


def set_reachability(device: Device, state: str) -> Device:
    normalized = state.lower().strip()
    mapping = {
        "up": Reachability.UP,
        "reachable": Reachability.UP,
        "down": Reachability.DOWN,
        "unreachable": Reachability.DOWN,
    }
    if normalized not in mapping:
        raise ValueError("Reachability must be up/reachable or down/unreachable.")
    device.reachability = mapping[normalized]
    device.save(update_fields=["reachability", "updated_at"])
    write_device_snmprec(device)
    write_datadog_exports()
    if SimulationState.get().engine_enabled:
        request_reload()
    return device


def update_device(device: Device, **changes) -> Device:
    rebuild_payload = False
    if "device_type" in changes and changes["device_type"]:
        spec = get_device_type(changes["device_type"])
        device.device_type = spec["key"]
        device.vendor = spec["vendor"]
        device.model = spec["model"]
        device.sys_object_id = spec["sys_object_id"]
        device.sys_descr = " ".join(str(spec["sys_descr"]).split())
        device.extra = {**(device.extra or {}), "extra_mibs": spec.get("extra_mibs", [])}
        rebuild_payload = True
    if changes.get("name"):
        device.name = changes["name"]
        if "." not in changes["name"]:
            from simulator.engine.config import load_simulator_config

            device.hostname = f"{changes['name']}.{load_simulator_config()['identity']['domain']}"
        else:
            device.hostname = changes["name"]
    if changes.get("location"):
        device.location = changes["location"]
    if changes.get("contact"):
        device.contact = changes["contact"]
    if rebuild_payload:
        from simulator.engine.factory import build_device_payload

        payload = build_device_payload(
            device_id=device.device_id,
            ip_address=device.ip_address,
            device_type=device.device_type,
            traffic_profile=changes.get("traffic") or device.traffic_profile,
            reachability=device.reachability,
            sequence=int("".join(ch for ch in device.device_id if ch.isdigit()) or "1"),
        )
        device.interfaces = payload["interfaces"]
        device.interface_count = payload["interface_count"]
        if not changes.get("name"):
            device.name = payload["name"]
            device.hostname = payload["hostname"]
    device.save()
    if changes.get("traffic"):
        device = set_traffic(device, changes["traffic"])
    if changes.get("reachability"):
        device = set_reachability(device, changes["reachability"])
    if not changes.get("traffic") and not changes.get("reachability"):
        write_device_snmprec(device)
        write_datadog_exports()
        if SimulationState.get().engine_enabled:
            request_reload()
    return device


def reload_engine() -> dict:
    state = SimulationState.get()
    state.engine_enabled = True
    state.save(update_fields=["engine_enabled"])
    write_datadog_exports()
    request_reload()
    return engine_status()


def counts_from_values(total, high, medium, low, unreachable) -> CategoryCounts:
    return validate_counts(total, high, medium, low, unreachable)
