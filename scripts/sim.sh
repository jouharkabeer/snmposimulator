#!/bin/sh
# Host-side helper for Ubuntu. Run from the project directory.
# Usage: ./scripts/sim.sh status
set -e
exec docker compose exec simulator sim "$@"
