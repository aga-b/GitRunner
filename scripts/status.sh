#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose ps
python3 - <<'PY'
import json
import time
from pathlib import Path

path = Path('state/status.json')
if not path.exists():
    print('No status yet. Check: docker compose logs --tail=100 controller')
else:
    data = json.loads(path.read_text())
    age = max(0, int(time.time()) - data.get('updated_at', 0))
    print(f'Last update: {age}s ago (this may be stale if the controller is stopped)')
    print(f'Active: {sum(data.get("active", {}).values())} / {data.get("max_runners", "?")}')
    print(f'Queued: {sum(data.get("queued", {}).values())} | Draining: {data.get("draining", False)}')
    m = data.get('metrics') or {}
    if m:
        print('Metrics:', ', '.join(f'{k}={v}' for k, v in m.items()))
    print(json.dumps(data, indent=2))
PY
