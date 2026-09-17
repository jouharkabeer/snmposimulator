"""Django settings for the SNMP network-device simulator.

This project is CLI-only. Django is used for configuration, persistence, and
management commands. No web server is started in Docker.
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "snmp-simulator-dev-key-change-in-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "0") in ("1", "true", "True")

ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "simulator.apps.SimulatorConfig",
]

MIDDLEWARE: list[str] = []

ROOT_URLCONF = "snmp_lab.urls"

TEMPLATES: list[dict] = []

WSGI_APPLICATION = "snmp_lab.wsgi.application"

DATA_DIR = Path(os.environ.get("SIMULATOR_DATA_DIR", BASE_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SIMULATOR_DB_PATH", str(DATA_DIR / "db" / "simulator.sqlite3")),
    }
}

Path(DATABASES["default"]["NAME"]).parent.mkdir(parents=True, exist_ok=True)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# Simulator paths (overridable via environment for Docker volumes)
SIMULATOR_CONFIG_FILE = Path(
    os.environ.get("SIMULATOR_CONFIG_FILE", BASE_DIR / "config" / "simulator.yaml")
)
SIMULATOR_DEVICE_TYPES_FILE = Path(
    os.environ.get(
        "SIMULATOR_DEVICE_TYPES_FILE", BASE_DIR / "config" / "device_types.yaml"
    )
)
SIMULATOR_SNMP_DIR = Path(
    os.environ.get("SIMULATOR_SNMP_DIR", DATA_DIR / "snmp")
)
SIMULATOR_RUN_DIR = Path(os.environ.get("SIMULATOR_RUN_DIR", DATA_DIR / "run"))
SIMULATOR_LOG_DIR = Path(os.environ.get("SIMULATOR_LOG_DIR", DATA_DIR / "logs"))
SIMULATOR_DATADOG_DIR = Path(
    os.environ.get("SIMULATOR_DATADOG_DIR", DATA_DIR / "datadog")
)
SIMULATOR_VARIATION_DIR = Path(
    os.environ.get(
        "SIMULATOR_VARIATION_DIR", BASE_DIR / "simulator" / "variation"
    )
)

for _path in (
    SIMULATOR_SNMP_DIR,
    SIMULATOR_RUN_DIR,
    SIMULATOR_LOG_DIR,
    SIMULATOR_DATADOG_DIR,
):
    _path.mkdir(parents=True, exist_ok=True)
