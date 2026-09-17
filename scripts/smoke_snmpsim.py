"""One-shot smoke test: serve a generated .snmprec and GET sysName."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snmp_lab.settings")

import django

django.setup()

from pysnmp.hlapi.v1arch.asyncio import (  # noqa: E402
    CommunityData,
    ObjectIdentity,
    ObjectType,
    SnmpDispatcher,
    UdpTransportTarget,
    get_cmd,
)
from simulator.engine.factory import build_device_payload  # noqa: E402
from simulator.engine.snmprec import generate_records  # noqa: E402
from simulator.models import Device  # noqa: E402


async def snmp_get(port: int, oid: str) -> str:
    dispatcher = SnmpDispatcher()
    error_indication, error_status, _error_index, var_binds = await get_cmd(
        dispatcher,
        CommunityData("public"),
        await UdpTransportTarget.create(("127.0.0.1", port), timeout=2, retries=1),
        ObjectType(ObjectIdentity(oid)),
    )
    dispatcher.transport_dispatcher.close_dispatcher()
    if error_indication:
        raise RuntimeError(str(error_indication))
    if error_status:
        raise RuntimeError(str(error_status))
    return " | ".join(f"{n.prettyPrint()}={v.prettyPrint()}" for n, v in var_binds)


def main() -> int:
    payload = build_device_payload(
        device_id="device-001",
        ip_address="10.200.1.1",
        device_type="router",
        traffic_profile="high",
        reachability="up",
        sequence=1,
    )
    records = generate_records(Device(**payload))
    tmp = Path(tempfile.mkdtemp())
    (tmp / "public.snmprec").write_text("\n".join(records) + "\n", encoding="utf-8")
    cache = tmp / "cache"
    cache.mkdir()
    port = 11611
    cmd = [
        sys.executable,
        "-m",
        "snmpsim.commands.responder",
        "--logging-method",
        "stdout",
        "--log-level",
        "error",
        "--cache-dir",
        str(cache),
        "--v3-engine-id",
        "auto",
        "--data-dir",
        str(tmp),
        "--agent-udpv4-endpoint",
        f"127.0.0.1:{port}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(2.5)
        if proc.poll() is not None:
            print(proc.stdout.read() if proc.stdout else "no output")
            print("SNMPSim exited early", file=sys.stderr)
            return 1
        for oid in (
            "1.3.6.1.2.1.1.5.0",
            "1.3.6.1.2.1.1.2.0",
            "1.3.6.1.2.1.1.3.0",
            "1.3.6.1.2.1.31.1.1.1.6.2",
        ):
            print(asyncio.run(snmp_get(port, oid)))
        first = asyncio.run(snmp_get(port, "1.3.6.1.2.1.1.3.0"))
        time.sleep(1.2)
        second = asyncio.run(snmp_get(port, "1.3.6.1.2.1.1.3.0"))
        print("uptime_1", first)
        print("uptime_2", second)
        print("OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
