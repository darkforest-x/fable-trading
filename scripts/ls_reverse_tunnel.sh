#!/usr/bin/env bash
# Deprecated alias — use scripts/tunnel_labelstudio.sh.
# Kept so old docs/muscle-memory keep working.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# single-port mode matches historical ls_reverse_tunnel behaviour
export ONLY_PRIMARY="${ONLY_PRIMARY:-1}"
exec bash "$ROOT/tunnel_labelstudio.sh"
