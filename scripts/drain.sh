#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
action="${1:-on}"
case "$action" in
  on) docker compose exec -T controller python -c 'from pathlib import Path; Path("/state/drain").touch()' ;;
  off) docker compose exec -T controller python -c 'from pathlib import Path; Path("/state/drain").unlink(missing_ok=True)' ;;
  *) echo 'Usage: scripts/drain.sh [on|off]'; exit 1 ;;
esac
