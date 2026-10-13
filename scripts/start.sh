#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
./scripts/preflight.sh
docker compose up -d controller
docker compose ps
