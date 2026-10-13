#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || { echo 'Run scripts/prepare.sh first.'; exit 1; }
runner_image="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["controller"]["environment"]["RUNNER_IMAGE"])')"
# Resolve current official images at install/update time; keep immutable digests.
docker pull ghcr.io/actions/actions-runner:latest
docker pull docker:dind
runner_base="$(docker image inspect ghcr.io/actions/actions-runner:latest --format '{{index .RepoDigests 0}}')"
dind_image="$(docker image inspect docker:dind --format '{{index .RepoDigests 0}}')"
[[ "$runner_base" == *@sha256:* && "$dind_image" == *@sha256:* ]]
docker build --pull --build-arg "RUNNER_BASE=$runner_base" -f runner/Dockerfile -t "$runner_image" .
docker compose build --pull controller
# Update only these keys. Never source .env as executable shell code.
python3 - "$runner_base" "$dind_image" <<'PY'
import sys
from pathlib import Path
path=Path('.env')
replacements={'RUNNER_BASE':sys.argv[1], 'DIND_IMAGE':sys.argv[2]}
lines=path.read_text().splitlines()
seen=set()
for i,line in enumerate(lines):
    key=line.partition('=')[0]
    if key in replacements:
        lines[i]=key+'='+replacements[key]
        seen.add(key)
lines += [key+'='+value for key,value in replacements.items() if key not in seen]
path.write_text('\n'.join(lines)+'\n')
path.chmod(0o600)
PY
echo 'Images built. Run scripts/preflight.sh before starting the controller.'
