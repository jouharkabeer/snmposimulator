"""Container entrypoint. Starts Django migrations, optional auto-lab, then the supervisor."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snmp_lab.settings")

import django
from django.core.management import call_command, execute_from_command_line


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    os.chdir("/app" if Path("/app/manage.py").exists() else str(Path(__file__).resolve().parents[1]))
    django.setup()
    call_command("migrate", interactive=False, verbosity=1)

    if _truthy(os.environ.get("SIMULATOR_AUTO_START")) and os.environ.get("SIMULATOR_TOTAL"):
        print("SIMULATOR_AUTO_START is enabled; creating lab from environment.", flush=True)
        argv = [
            "manage.py",
            "simulator",
            "start",
            "--non-interactive",
            "--replace",
            "--total",
            os.environ.get("SIMULATOR_TOTAL", "0"),
            "--high",
            os.environ.get("SIMULATOR_HIGH", "0"),
            "--medium",
            os.environ.get("SIMULATOR_MEDIUM", "0"),
            "--low",
            os.environ.get("SIMULATOR_LOW", "0"),
            "--unreachable",
            os.environ.get("SIMULATOR_UNREACHABLE", "0"),
        ]
        try:
            execute_from_command_line(argv)
        except Exception as exc:  # noqa: BLE001
            print(f"Auto-start failed: {exc}", file=sys.stderr, flush=True)

    execute_from_command_line(["manage.py", "simulator", "daemon"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
