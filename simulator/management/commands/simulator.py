"""
Django management command: python manage.py simulator <subcommand>

Terminal-only control plane for the virtual SNMP network lab.
"""

from __future__ import annotations

import argparse
import json
import sys

from django.core.management.base import BaseCommand, CommandError

from simulator.engine.config import available_device_types
from simulator.engine.datadog_export import write_datadog_exports
from simulator.engine.supervisor import daemon_loop, engine_status
from simulator.engine.validation import ValidationError, parse_non_negative_int, validate_counts
from simulator.models import Device, DeviceType, Reachability, TrafficProfile
from simulator import services


HELP = """
SNMP Network Device Simulator

  python manage.py simulator start
  python manage.py simulator status
  python manage.py simulator list
  python manage.py simulator show device-001
  python manage.py simulator add --type router --traffic high
  python manage.py simulator remove device-001
  python manage.py simulator set-traffic device-001 high
  python manage.py simulator set-reachability device-005 down
  python manage.py simulator netflow status
  python manage.py simulator netflow collector host.docker.internal 2055
  python manage.py simulator set-netflow device-001 on
  python manage.py simulator stop
  python manage.py simulator reset --force
"""


def prompt_int(question: str) -> int:
    while True:
        try:
            raw = input(f"{question} ").strip()
        except EOFError as exc:
            raise CommandError("Input aborted.") from exc
        try:
            return parse_non_negative_int(raw, question)
        except ValidationError as exc:
            print(f"  Error: {exc}")


class Command(BaseCommand):
    help = "Manage the SNMP network-device simulator (CLI only, no web UI)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="subcommand", metavar="command")
        sub.required = False

        start = sub.add_parser("start", help="Create a lab and start SNMPSim.")
        start.add_argument("--non-interactive", action="store_true")
        start.add_argument("--total", type=int)
        start.add_argument("--high", type=int)
        start.add_argument("--medium", type=int)
        start.add_argument("--low", type=int)
        start.add_argument("--unreachable", type=int)
        start.add_argument(
            "--replace",
            action="store_true",
            help="Destroy the existing lab before creating a new one.",
        )

        sub.add_parser("stop", help="Stop SNMPSim. Device inventory is kept.")
        sub.add_parser("status", help="Show engine and inventory summary.")
        sub.add_parser("reload", help="Regenerate SNMP data and restart SNMPSim.")

        listing = sub.add_parser("list", help="List simulated devices.")
        listing.add_argument("--json", action="store_true")

        show = sub.add_parser("show", help="Inspect one device.")
        show.add_argument("device_id")
        show.add_argument("--json", action="store_true")

        add = sub.add_parser("add", help="Add one device.")
        add.add_argument("--type", dest="device_type", choices=available_device_types())
        add.add_argument("--traffic", choices=list(TrafficProfile.values))
        add.add_argument(
            "--reachability",
            choices=["up", "down", "reachable", "unreachable"],
        )
        add.add_argument("--name")

        remove = sub.add_parser("remove", help="Remove a device.")
        remove.add_argument("device_id")

        update = sub.add_parser("update", help="Update device metadata.")
        update.add_argument("device_id")
        update.add_argument("--name")
        update.add_argument("--type", dest="device_type", choices=available_device_types())
        update.add_argument("--traffic", choices=list(TrafficProfile.values))
        update.add_argument("--location")
        update.add_argument("--contact")
        update.add_argument(
            "--reachability",
            choices=["up", "down", "reachable", "unreachable"],
        )

        traffic = sub.add_parser("set-traffic", help="Change a device traffic profile.")
        traffic.add_argument("device_id")
        traffic.add_argument("profile", choices=list(TrafficProfile.values))

        reach = sub.add_parser("set-reachability", help="Mark a device up or down.")
        reach.add_argument("device_id")
        reach.add_argument("state", choices=["up", "down", "reachable", "unreachable"])

        netflow = sub.add_parser("netflow", help="NetFlow exporter controls.")
        nf_sub = netflow.add_subparsers(dest="netflow_cmd", metavar="action")
        nf_sub.add_parser("status", help="Show NetFlow exporter status.")
        nf_sub.add_parser("enable", help="Enable NetFlow export.")
        nf_sub.add_parser("disable", help="Disable NetFlow export.")
        nf_col = nf_sub.add_parser("collector", help="Set collector host and UDP port.")
        nf_col.add_argument("host")
        nf_col.add_argument("port", type=int)
        nf_ver = nf_sub.add_parser("version", help="Set NetFlow version 5 or 9.")
        nf_ver.add_argument("version", type=int, choices=[5, 9])

        set_nf = sub.add_parser("set-netflow", help="Enable or disable NetFlow on one device.")
        set_nf.add_argument("device_id")
        set_nf.add_argument("state", choices=["on", "off"])

        reset = sub.add_parser("reset", help="Delete all devices and stop the engine.")
        reset.add_argument("--force", action="store_true")

        sub.add_parser("export-datadog", help="Write Datadog Agent config files.")
        sub.add_parser("types", help="List available device types.")
        sub.add_parser("daemon", help=argparse.SUPPRESS)

        # Aliases matching the original spec wording.
        sub.add_parser("list-devices", help=argparse.SUPPRESS)
        sub.add_parser("add-device", help=argparse.SUPPRESS)
        sub.add_parser("remove-device", help=argparse.SUPPRESS)
        sub.add_parser("update-device", help=argparse.SUPPRESS)
        sub.add_parser("show-device", help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        command = options.get("subcommand")
        aliases = {
            "list-devices": "list",
            "add-device": "add",
            "remove-device": "remove",
            "update-device": "update",
            "show-device": "show",
        }
        command = aliases.get(command, command)
        if not command:
            self.stdout.write(HELP.strip())
            return

        handlers = {
            "start": self._start,
            "stop": self._stop,
            "status": self._status,
            "reload": self._reload,
            "list": self._list,
            "show": self._show,
            "add": self._add,
            "remove": self._remove,
            "update": self._update,
            "set-traffic": self._set_traffic,
            "set-reachability": self._set_reachability,
            "netflow": self._netflow,
            "set-netflow": self._set_netflow,
            "reset": self._reset,
            "export-datadog": self._export_datadog,
            "types": self._types,
            "daemon": self._daemon,
        }
        handler = handlers.get(command)
        if handler is None:
            raise CommandError(f"Unknown command '{command}'.")
        try:
            handler(options)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        except KeyError as exc:
            raise CommandError(str(exc)) from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

    def _get_device(self, device_id: str) -> Device:
        try:
            return Device.objects.get(device_id=device_id)
        except Device.DoesNotExist as exc:
            raise CommandError(f"Device '{device_id}' not found.") from exc

    def _start(self, options):
        if options.get("non_interactive"):
            missing = [
                name
                for name in ("total", "high", "medium", "low", "unreachable")
                if options.get(name) is None
            ]
            if missing:
                raise CommandError(
                    "Non-interactive start requires --total --high --medium "
                    f"--low --unreachable (missing: {', '.join(missing)})."
                )
            total = options["total"]
            high = options["high"]
            medium = options["medium"]
            low = options["low"]
            unreachable = options["unreachable"]
        else:
            self.stdout.write("Starting a new SNMP simulation lab.")
            self.stdout.write("Category counts must add up to the total.\n")
            total = prompt_int("How many devices do you want?")
            high = prompt_int("How many HIGH traffic devices?")
            medium = prompt_int("How many MEDIUM traffic devices?")
            low = prompt_int("How many LOW traffic devices?")
            unreachable = prompt_int("How many UNREACHABLE devices?")

        counts = validate_counts(total, high, medium, low, unreachable)
        self.stdout.write("")
        self.stdout.write(f"Total devices:  {counts.total}")
        self.stdout.write(f"High traffic:   {counts.high}")
        self.stdout.write(f"Medium traffic: {counts.medium}")
        self.stdout.write(f"Low traffic:    {counts.low}")
        self.stdout.write(f"Unreachable:    {counts.unreachable}")
        self.stdout.write("")

        devices = services.start_simulation(counts, replace=bool(options.get("replace")))
        self.stdout.write(self.style.SUCCESS(f"Created {len(devices)} simulated devices."))
        self.stdout.write("SNMPSim reload requested. Check status with:")
        self.stdout.write("  python manage.py simulator status")
        self._print_table(devices[: min(len(devices), 15)])
        if len(devices) > 15:
            self.stdout.write(f"... {len(devices) - 15} more. Use `simulator list`.")

    def _stop(self, _options):
        services.stop_simulation()
        self.stdout.write(self.style.WARNING("SNMP engine stopped. Inventory was kept."))

    def _status(self, _options):
        status = engine_status()
        running = "running" if status["snmpsim_running"] else "stopped"
        enabled = "yes" if status["engine_enabled"] else "no"
        self.stdout.write(f"Engine enabled:     {enabled}")
        self.stdout.write(f"SNMPSim process:    {running}  pid={status['snmpsim_pid']}")
        self.stdout.write(f"Devices:            {status['device_count']}")
        self.stdout.write(f"  reachable:        {status['reachable_count']}")
        self.stdout.write(f"  unreachable:      {status['unreachable_count']}")
        self.stdout.write(f"Device subnet:      {status['device_cidr']}")
        self.stdout.write(f"SNMP community:     {status['community']}")
        self.stdout.write(f"Host UDP port:      {status['host_port']}")
        self.stdout.write(
            f"NetFlow:            {'on' if status.get('netflow_enabled') else 'off'}  "
            f"v{status.get('netflow_version')} -> {status.get('netflow_collector')}  "
            f"exporters={status.get('netflow_exporters')}  "
            f"packets={status.get('netflow_packets_sent')}"
        )
        self.stdout.write(f"Config generation:  {status['generation']}")
        if status["last_started_at"]:
            self.stdout.write(f"Last started:       {status['last_started_at']}")
        if status["last_error"]:
            self.stdout.write(self.style.WARNING(f"Warnings:\n{status['last_error']}"))
        if status.get("netflow_last_error"):
            self.stdout.write(self.style.WARNING(f"NetFlow error: {status['netflow_last_error']}"))

    def _reload(self, _options):
        services.reload_engine()
        self.stdout.write(self.style.SUCCESS("Reload requested. SNMPSim will restart shortly."))

    def _list(self, options):
        devices = list(Device.objects.all())
        if options.get("json"):
            self.stdout.write(json.dumps([self._device_dict(d) for d in devices], indent=2, default=str))
            return
        if not devices:
            self.stdout.write("No devices. Run: python manage.py simulator start")
            return
        self._print_table(devices)

    def _show(self, options):
        device = self._get_device(options["device_id"])
        if options.get("json"):
            self.stdout.write(json.dumps(self._device_dict(device, detail=True), indent=2, default=str))
            return
        self.stdout.write(f"ID:             {device.device_id}")
        self.stdout.write(f"Name:           {device.name}")
        self.stdout.write(f"Hostname:       {device.hostname}")
        self.stdout.write(f"IP address:     {device.ip_address}")
        self.stdout.write(f"SNMP:           v{device.snmp_version} community={device.community} port={device.snmp_port}")
        self.stdout.write(f"Community alias:{device.community_alias}  (use with host UDP 1161)")
        self.stdout.write(f"Type:           {device.device_type} ({device.vendor} {device.model})")
        self.stdout.write(f"sysObjectID:    {device.sys_object_id}")
        self.stdout.write(f"sysDescr:       {device.sys_descr}")
        self.stdout.write(f"Serial:         {device.serial_number}")
        self.stdout.write(f"Location:       {device.location}")
        self.stdout.write(f"Contact:        {device.contact}")
        self.stdout.write(f"Traffic:        {device.traffic_profile.upper()}")
        self.stdout.write(f"Status:         {device.status_label}")
        self.stdout.write(f"NetFlow:        {'on' if device.netflow_enabled and device.is_reachable else 'off'}")
        self.stdout.write(f"Interfaces:     {device.interface_count}")
        self.stdout.write("")
        self.stdout.write(f"{'IDX':<5}{'NAME':<24}{'STATUS':<10}{'SPEED':<12}{'ALIAS'}")
        for iface in device.interfaces or []:
            status = "up" if iface.get("oper_status") == 1 else "down"
            speed = iface.get("high_speed") or 0
            speed_s = f"{speed} Mbps" if speed else "-"
            self.stdout.write(
                f"{iface['index']:<5}{iface['name']:<24}{status:<10}{speed_s:<12}{iface.get('alias', '')}"
            )
        self.stdout.write("")
        self.stdout.write("Query (inside Docker network):")
        self.stdout.write(
            f"  snmpwalk -v2c -c {device.community} {device.ip_address}:{device.snmp_port} 1.3.6.1.2.1.1"
        )
        self.stdout.write("Query (from Ubuntu host via published port):")
        self.stdout.write(
            f"  snmpwalk -v2c -c {device.community_alias} 127.0.0.1:1161 1.3.6.1.2.1.1"
        )

    def _add(self, options):
        device_type = options.get("device_type")
        if not device_type and sys.stdin.isatty():
            types = available_device_types()
            self.stdout.write("Device types: " + ", ".join(types))
            device_type = input("Type [router]: ").strip() or DeviceType.ROUTER
        traffic = options.get("traffic") or TrafficProfile.MEDIUM
        reachability = options.get("reachability") or "up"
        reachability = {
            "up": Reachability.UP,
            "reachable": Reachability.UP,
            "down": Reachability.DOWN,
            "unreachable": Reachability.DOWN,
        }[reachability]
        device = services.add_device(
            device_type=device_type or DeviceType.ROUTER,
            traffic_profile=traffic,
            reachability=reachability,
            name=options.get("name"),
        )
        self.stdout.write(self.style.SUCCESS(f"Added {device.device_id}  {device.name}  {device.ip_address}"))

    def _remove(self, options):
        device = self._get_device(options["device_id"])
        device_id = services.remove_device(device)
        self.stdout.write(self.style.WARNING(f"Removed {device_id}."))

    def _update(self, options):
        device = self._get_device(options["device_id"])
        device = services.update_device(
            device,
            name=options.get("name"),
            device_type=options.get("device_type"),
            traffic=options.get("traffic"),
            location=options.get("location"),
            contact=options.get("contact"),
            reachability=options.get("reachability"),
        )
        self.stdout.write(self.style.SUCCESS(f"Updated {device.device_id}."))

    def _set_traffic(self, options):
        device = self._get_device(options["device_id"])
        device = services.set_traffic(device, options["profile"])
        self.stdout.write(
            self.style.SUCCESS(f"{device.device_id} traffic profile is now {device.traffic_profile.upper()}.")
        )

    def _set_reachability(self, options):
        device = self._get_device(options["device_id"])
        device = services.set_reachability(device, options["state"])
        self.stdout.write(
            self.style.SUCCESS(f"{device.device_id} is now {device.status_label}.")
        )

    def _netflow(self, options):
        action = options.get("netflow_cmd")
        if not action:
            raise CommandError("Usage: simulator netflow status|enable|disable|collector|version")
        if action == "status":
            status = engine_status()
            self.stdout.write(f"Enabled:     {'yes' if status['netflow_enabled'] else 'no'}")
            self.stdout.write(f"Version:     NetFlow v{status['netflow_version']}")
            self.stdout.write(f"Collector:   {status['netflow_collector']}")
            self.stdout.write(f"Exporters:   {status['netflow_exporters']} reachable devices")
            self.stdout.write(f"Packets sent:{status['netflow_packets_sent']}")
            if status.get("netflow_last_error"):
                self.stdout.write(self.style.WARNING(f"Last error:  {status['netflow_last_error']}"))
            return
        if action == "enable":
            services.configure_netflow(enabled=True)
            self.stdout.write(self.style.SUCCESS("NetFlow export enabled."))
            return
        if action == "disable":
            services.configure_netflow(enabled=False)
            self.stdout.write(self.style.WARNING("NetFlow export disabled."))
            return
        if action == "collector":
            state = services.configure_netflow(
                collector_host=options["host"],
                collector_port=options["port"],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"NetFlow collector set to {state.netflow_collector_host}:{state.netflow_collector_port}."
                )
            )
            return
        if action == "version":
            state = services.configure_netflow(version=options["version"])
            self.stdout.write(self.style.SUCCESS(f"NetFlow version set to v{state.netflow_version}."))
            return
        raise CommandError(f"Unknown netflow action '{action}'.")

    def _set_netflow(self, options):
        device = self._get_device(options["device_id"])
        enabled = options["state"] == "on"
        device = services.set_device_netflow(device, enabled)
        label = "on" if device.netflow_enabled else "off"
        self.stdout.write(self.style.SUCCESS(f"{device.device_id} NetFlow export is {label}."))

    def _reset(self, options):
        if not options.get("force"):
            confirm = input("Delete ALL simulated devices? Type 'yes' to continue: ").strip()
            if confirm.lower() != "yes":
                self.stdout.write("Cancelled.")
                return
        services.reset_simulation()
        self.stdout.write(self.style.WARNING("Lab reset. Inventory is empty."))

    def _export_datadog(self, _options):
        paths = write_datadog_exports()
        self.stdout.write("Wrote Datadog configuration:")
        for name, path in paths.items():
            self.stdout.write(f"  {name}: {path}")

    def _types(self, _options):
        from simulator.engine.config import load_device_types

        for key, spec in load_device_types().items():
            self.stdout.write(
                f"{key:<12} {spec['vendor']} {spec['model']:<22} sysObjectID={spec['sys_object_id']}"
            )

    def _daemon(self, _options):
        daemon_loop()

    def _device_dict(self, device: Device, detail: bool = False) -> dict:
        data = {
            "device_id": device.device_id,
            "name": device.name,
            "hostname": device.hostname,
            "ip_address": device.ip_address,
            "port": device.snmp_port,
            "community": device.community,
            "community_alias": device.community_alias,
            "device_type": device.device_type,
            "vendor": device.vendor,
            "model": device.model,
            "sys_object_id": device.sys_object_id,
            "traffic_profile": device.traffic_profile,
            "reachability": device.reachability,
            "netflow_enabled": device.netflow_enabled,
            "interface_count": device.interface_count,
        }
        if detail:
            data["sys_descr"] = device.sys_descr
            data["serial_number"] = device.serial_number
            data["location"] = device.location
            data["contact"] = device.contact
            data["interfaces"] = device.interfaces
        return data

    def _print_table(self, devices: list[Device]) -> None:
        header = f"{'ID':<14}{'NAME':<20}{'IP':<16}{'TYPE':<10}{'TRAFFIC':<8}{'STATUS':<6}{'NETFLOW'}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for device in devices:
            status = "UP" if device.is_reachable else "DOWN"
            netflow = "on" if device.netflow_enabled and device.is_reachable else "off"
            self.stdout.write(
                f"{device.device_id:<14}{device.name:<20}{device.ip_address:<16}"
                f"{device.device_type:<10}{device.traffic_profile.upper():<8}{status:<6}{netflow}"
            )
