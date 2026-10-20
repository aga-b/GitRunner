#!/usr/bin/env bash
# Weekly: refresh runner/DinD images, rebuild, prune unused Docker data. Safe while jobs run:
# running containers keep their image and the controller adopts them after a restart.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
./scripts/build.sh --update
docker compose up -d controller mirror
docker image prune -f --filter "until=168h"
docker builder prune -f --filter "until=168h"
# Never prune volumes: the Docker Hub mirror and Playwright caches are named volumes.
docker system df
