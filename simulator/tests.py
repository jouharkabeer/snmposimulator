"""Django tests for the SNMP simulator control plane."""

from django.test import TestCase

from simulator.engine.factory import build_device_payload, create_lab_devices
from simulator.engine.ipam import format_device_id, next_device_number, next_ip_address
from simulator.engine.snmprec import generate_records, iter_required_oids
from simulator.engine.validation import ValidationError, validate_counts
from simulator.models import Device, Reachability, TrafficProfile


class ValidationTests(TestCase):
    def test_counts_must_add_up(self):
        counts = validate_counts(50, 10, 20, 15, 5)
        self.assertEqual(counts.total, 50)
        self.assertEqual(counts.reachable, 45)

    def test_mismatch_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_counts(10, 2, 2, 2, 2)

    def test_negative_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_counts(5, -1, 3, 2, 1)

    def test_zero_total_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_counts(0, 0, 0, 0, 0)

    def test_all_unreachable_is_allowed(self):
        counts = validate_counts(3, 0, 0, 0, 3)
        self.assertEqual(counts.unreachable, 3)


class IpamTests(TestCase):
    def test_device_id_format(self):
        self.assertEqual(format_device_id(1), "device-001")
        self.assertEqual(format_device_id(42), "device-042")

    def test_next_number(self):
        self.assertEqual(next_device_number([]), 1)
        self.assertEqual(next_device_number(["device-001", "device-007"]), 8)

    def test_ip_allocation_skips_used(self):
        ip = next_ip_address(["10.200.1.1", "10.200.1.2"])
        self.assertEqual(ip, "10.200.1.3")


class SnmprecTests(TestCase):
    def test_generated_records_include_core_oids(self):
        payload = build_device_payload(
            device_id="device-001",
            ip_address="10.200.1.1",
            device_type="router",
            traffic_profile=TrafficProfile.HIGH,
            reachability=Reachability.UP,
            sequence=1,
        )
        device = Device(**payload)
        records = generate_records(device)
        oids = {line.split("|", 1)[0] for line in records}
        for oid in iter_required_oids():
            self.assertIn(oid, oids)
        self.assertTrue(any("numeric" in line for line in records))
        self.assertTrue(any(line.startswith("1.3.6.1.2.1.31.1.1.1.6.1") for line in records))

    def test_oids_are_sorted_numerically(self):
        payload = build_device_payload(
            device_id="device-002",
            ip_address="10.200.1.2",
            device_type="switch",
            traffic_profile=TrafficProfile.LOW,
            reachability=Reachability.UP,
            sequence=2,
        )
        records = generate_records(Device(**payload))
        keys = [tuple(int(p) for p in line.split("|", 1)[0].split(".")) for line in records]
        self.assertEqual(keys, sorted(keys))


class FactoryTests(TestCase):
    def test_create_lab_assigns_profiles(self):
        counts = validate_counts(8, 2, 3, 2, 1)
        devices = create_lab_devices(counts)
        self.assertEqual(len(devices), 8)
        self.assertEqual(Device.objects.filter(traffic_profile="high").count(), 2)
        self.assertEqual(Device.objects.filter(traffic_profile="medium").count(), 3)
        self.assertEqual(Device.objects.filter(traffic_profile="low", reachability="up").count(), 2)
        self.assertEqual(Device.objects.filter(reachability="down").count(), 1)
        self.assertEqual(Device.objects.filter(device_id="device-001").count(), 1)
        types = set(Device.objects.values_list("device_type", flat=True))
        self.assertTrue({"router", "switch", "firewall", "generic"} <= types)
