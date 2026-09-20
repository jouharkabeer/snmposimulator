FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=snmp_lab.settings \
    SIMULATOR_DATA_DIR=/data \
    MIBS=

RUN apt-get update && apt-get install -y --no-install-recommends \
        iproute2 \
        iputils-ping \
        procps \
        snmp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app


RUN groupadd --gid 1001 snmp \
    && useradd --uid 1001 --gid 1001 --create-home --shell /bin/sh snmp \
    && mkdir -p /data/db /data/snmp /data/run /data/logs /data/datadog \
    && sed -i 's/\r$//' /app/scripts/*.py \
    && cp /app/scripts/sim.py /usr/local/bin/sim \
    && chmod +x /usr/local/bin/sim /app/scripts/entrypoint.py /app/manage.py


EXPOSE 161/udp 1161/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "from pathlib import Path; raise SystemExit(0 if Path('/data/run/supervisor.pid').exists() else 1)"

ENTRYPOINT ["python", "/app/scripts/entrypoint.py"]
