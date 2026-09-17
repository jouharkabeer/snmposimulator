#!/usr/bin/env python3
"""Query a simulated device with Net-SNMP tools (must run where snmpget exists)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="SNMP smoke test against a simulated device.")
    parser.add_argument("--community", default="device-001")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="1161")
    parser.add_argument("--oid", default="1.3.6.1.2.1.1")
    args = parser.parse_args()

    snmpwalk = shutil.which("snmpwalk")
    if not snmpwalk:
        print("snmpwalk not found. Install snmp (Debian/Ubuntu: apt install snmp).", file=sys.stderr)
        return 2

    target = f"{args.host}:{args.port}"
    cmd = [
        snmpwalk,
        "-v2c",
        "-c",
        args.community,
        "-On",
        "-t",
        "2",
        "-r",
        "1",
        target,
        args.oid,
    ]
    print(" ".join(cmd))
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
