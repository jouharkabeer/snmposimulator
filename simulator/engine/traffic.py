"""Predefined HIGH / MEDIUM / LOW traffic profiles.

SNMPSim's numeric variation module is used so values change over time:

  v = f(time * rate) * scale + offset
  if cumulative: increment += offset * elapsed_seconds * rate

For counters, ``offset`` is therefore bytes (or packets) per second.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator.models import TrafficProfile


@dataclass(frozen=True)
class TrafficSpec:
    name: str
    # Approximate interface speed advertised via ifSpeed / ifHighSpeed.
    if_speed_bps: int
    if_high_speed_mbps: int
    # Counter64 ifHCInOctets / ifHCOutOctets average bytes per second.
    in_bytes_per_sec: int
    out_bytes_per_sec: int
    in_bytes_jitter: int
    out_bytes_jitter: int
    avg_packet_bytes: int
    in_errors_per_sec: float
    out_errors_per_sec: float
    in_discards_per_sec: float
    out_discards_per_sec: float
    cpu_offset: int
    cpu_scale: int
    memory_used_ratio: float
    description: str


PROFILES: dict[str, TrafficSpec] = {
    TrafficProfile.HIGH: TrafficSpec(
        name="HIGH",
        if_speed_bps=1_000_000_000,
        if_high_speed_mbps=1000,
        in_bytes_per_sec=75_000_000,  # ~600 Mbps
        out_bytes_per_sec=55_000_000,  # ~440 Mbps
        in_bytes_jitter=12_000_000,
        out_bytes_jitter=10_000_000,
        avg_packet_bytes=900,
        in_errors_per_sec=0.4,
        out_errors_per_sec=0.2,
        in_discards_per_sec=0.6,
        out_discards_per_sec=0.3,
        cpu_offset=72,
        cpu_scale=18,
        memory_used_ratio=0.78,
        description="Busy core/edge links, high utilization, occasional errors.",
    ),
    TrafficProfile.MEDIUM: TrafficSpec(
        name="MEDIUM",
        if_speed_bps=1_000_000_000,
        if_high_speed_mbps=1000,
        in_bytes_per_sec=12_000_000,  # ~96 Mbps
        out_bytes_per_sec=9_000_000,  # ~72 Mbps
        in_bytes_jitter=3_000_000,
        out_bytes_jitter=2_000_000,
        avg_packet_bytes=800,
        in_errors_per_sec=0.05,
        out_errors_per_sec=0.03,
        in_discards_per_sec=0.08,
        out_discards_per_sec=0.04,
        cpu_offset=38,
        cpu_scale=12,
        memory_used_ratio=0.52,
        description="Normal production traffic with modest variation.",
    ),
    TrafficProfile.LOW: TrafficSpec(
        name="LOW",
        if_speed_bps=100_000_000,
        if_high_speed_mbps=100,
        in_bytes_per_sec=700_000,  # ~5.6 Mbps
        out_bytes_per_sec=450_000,  # ~3.6 Mbps
        in_bytes_jitter=180_000,
        out_bytes_jitter=120_000,
        avg_packet_bytes=640,
        in_errors_per_sec=0.0,
        out_errors_per_sec=0.0,
        in_discards_per_sec=0.01,
        out_discards_per_sec=0.0,
        cpu_offset=12,
        cpu_scale=6,
        memory_used_ratio=0.28,
        description="Quiet access/management links.",
    ),
}


def get_traffic_spec(profile: str) -> TrafficSpec:
    key = profile.lower().strip()
    if key not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise KeyError(f"Unknown traffic profile '{profile}'. Known: {known}")
    return PROFILES[key]
