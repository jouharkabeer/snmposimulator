#!/usr/bin/env python3
"""Convenience wrapper used inside the container: `sim status`."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snmp_lab.settings")

from django.core.management import execute_from_command_line


def main() -> int:
    args = sys.argv[1:]
    execute_from_command_line(["manage.py", "simulator", *args])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
