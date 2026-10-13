#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose config --quiet
[[ -s secrets/github-app.pem ]] || { echo 'Missing secrets/github-app.pem'; exit 1; }
chmod 600 secrets/github-app.pem
docker compose run --rm --no-deps controller python preflight.py
