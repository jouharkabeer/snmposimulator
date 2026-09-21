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


class NetflowTests(TestCase):
    def _device(self, traffic=TrafficProfile.HIGH, reachability=Reachability.UP) -> Device:
        payload = build_device_payload(
            device_id="device-001",
            ip_address="10.200.1.1",
            device_type="router",
            traffic_profile=traffic,
            reachability=reachability,
            sequence=1,
        )
        return Device(**payload)

    def test_v5_packet_structure(self):
        from simulator.engine.netflow import Flow, encode_netflow_v5

        flow = Flow(
            src="10.200.1.1",
            dst="8.8.8.8",
            nexthop="10.200.0.1",
            input_if=1,
            output_if=2,
            packets=10,
            octets=1500,
            first_ms=100,
            last_ms=200,
            src_port=45000,
            dst_port=443,
            protocol=6,
            tos=0,
            tcp_flags=0x18,
            direction=1,
        )
        packet = encode_netflow_v5([flow], uptime_ms=1000, unix_secs=1700000000, sequence=1, engine_id=1)
        self.assertEqual(packet[:2], b"\x00\x05")
        self.assertEqual(int.from_bytes(packet[2:4], "big"), 1)
        self.assertEqual(len(packet), 24 + 48)

    def test_v9_packet_contains_template_and_data(self):
        from simulator.engine.netflow import Flow, encode_netflow_v9

        flow = Flow(
            src="10.200.1.1",
            dst="1.1.1.1",
            nexthop="10.200.0.1",
            input_if=1,
            output_if=2,
            packets=4,
            octets=640,
            first_ms=10,
            last_ms=50,
            src_port=53,
            dst_port=53,
            protocol=17,
            tos=0,
            tcp_flags=0,
            direction=0,
        )
        packet = encode_netflow_v9(
            [flow],
            uptime_ms=1000,
            unix_secs=1700000000,
            sequence=3,
            source_id=1,
            include_template=True,
        )
        self.assertEqual(packet[:2], b"\x00\x09")
        self.assertGreater(len(packet), 20)
        self.assertIn(b"\x01\x00", packet)  # template id 256

    def test_flow_count_follows_profile(self):
        from simulator.engine.netflow import generate_flows

        high = generate_flows(self._device(TrafficProfile.HIGH), interval=5)
        low = generate_flows(self._device(TrafficProfile.LOW), interval=5)
        self.assertEqual(len(high), 24)
        self.assertEqual(len(low), 4)
        self.assertTrue(all(flow.octets >= 64 for flow in high))

