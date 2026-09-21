"""Persistent device inventory and simulation engine state."""

from django.db import models


class DeviceType(models.TextChoices):
    ROUTER = "router", "Router"
    SWITCH = "switch", "Switch"
    FIREWALL = "firewall", "Firewall"
    GENERIC = "generic", "Generic SNMP device"


class TrafficProfile(models.TextChoices):
    HIGH = "high", "HIGH"
    MEDIUM = "medium", "MEDIUM"
    LOW = "low", "LOW"


class Reachability(models.TextChoices):
    UP = "up", "UP / REACHABLE"
    DOWN = "down", "DOWN / UNREACHABLE"


class SimulationState(models.Model):
    """Singleton row describing the current lab."""

    engine_enabled = models.BooleanField(default=False)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_stopped_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    generation = models.PositiveIntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True, default="")
    netflow_enabled = models.BooleanField(default=True)
    netflow_version = models.PositiveSmallIntegerField(default=9)
    netflow_collector_host = models.CharField(max_length=255, default="host.docker.internal")
    netflow_collector_port = models.PositiveIntegerField(default=2055)
    netflow_packets_sent = models.PositiveIntegerField(default=0)
    netflow_last_error = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "simulation state"

    def __str__(self) -> str:
        return "running" if self.engine_enabled else "stopped"

    @classmethod
    def get(cls) -> "SimulationState":
        obj, created = cls.objects.get_or_create(pk=1)
        if created:
            import os

            host = os.environ.get("NETFLOW_COLLECTOR_HOST")
            port = os.environ.get("NETFLOW_COLLECTOR_PORT")
            version = os.environ.get("NETFLOW_VERSION")
            if host:
                obj.netflow_collector_host = host
            if port:
                obj.netflow_collector_port = int(port)
            if version:
                obj.netflow_version = int(version)
            obj.save()
        return obj


class Device(models.Model):
    device_id = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=128)
    hostname = models.CharField(max_length=128)
    ip_address = models.GenericIPAddressField(protocol="IPv4", unique=True)
    snmp_port = models.PositiveIntegerField(default=161)
    snmp_version = models.CharField(max_length=8, default="2c")
    community = models.CharField(max_length=64, default="public")
    community_alias = models.CharField(
        max_length=64,
        help_text="Alternate community equal to the device id for host-port access.",
    )
    device_type = models.CharField(max_length=32, choices=DeviceType.choices)
    vendor = models.CharField(max_length=64)
    model = models.CharField(max_length=64)
    sys_object_id = models.CharField(max_length=128)
    sys_descr = models.TextField()
    location = models.CharField(max_length=128, default="Datacenter Lab / Rack A")
    contact = models.CharField(max_length=128, default="netops@lab.local")
    serial_number = models.CharField(max_length=64, blank=True, default="")
    traffic_profile = models.CharField(
        max_length=16, choices=TrafficProfile.choices, default=TrafficProfile.MEDIUM
    )
    reachability = models.CharField(
        max_length=8, choices=Reachability.choices, default=Reachability.UP
    )
    interface_count = models.PositiveIntegerField(default=4)
    interfaces = models.JSONField(default=list, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    netflow_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["device_id"]

    def __str__(self) -> str:
        return f"{self.device_id} ({self.ip_address})"

    @property
    def is_reachable(self) -> bool:
        return self.reachability == Reachability.UP

    @property
    def status_label(self) -> str:
        return "UP / REACHABLE" if self.is_reachable else "DOWN / UNREACHABLE"
