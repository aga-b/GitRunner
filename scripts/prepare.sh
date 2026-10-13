#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
command -v docker >/dev/null || { echo 'Docker Engine is required on the target Linux VM.'; exit 1; }
docker compose version >/dev/null
arch="$(docker info --format '{{.Architecture}}')"
[[ "$arch" == x86_64 || "$arch" == amd64 ]] || { echo 'This package currently targets Linux x64.'; exit 1; }
mkdir -p secrets state
chmod 700 secrets state
if [[ ! -e .env ]]; then cp .env.example .env; fi
chmod 600 .env
echo 'Prepared. Set App IDs in .env and copy its PEM to secrets/github-app.pem.'
