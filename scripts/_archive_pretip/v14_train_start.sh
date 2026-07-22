#!/usr/bin/env bash
# Install + load v14 pad200 train under launchd (survives Cursor agent shell death).
# Does NOT promote. Unloads stale v13 train agent if still registered.
# Usage: bash scripts/v14_train_start.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.fable.owner-v14-pad200-train
PLIST_SRC="$ROOT/scripts/${LABEL}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "ERROR: missing $ROOT/.venv/bin/python" >&2
  exit 1
fi
if [[ ! -f "$ROOT/datasets/dense_owner_v14_pad200/data.yaml" ]]; then
  echo "ERROR: missing datasets/dense_owner_v14_pad200/data.yaml" >&2
  exit 1
fi
if [[ ! -f "$PLIST_SRC" ]]; then
  echo "ERROR: missing $PLIST_SRC" >&2
  exit 1
fi
if [[ ! -x "$ROOT/scripts/train_owner_v14_pad200.sh" ]]; then
  chmod +x "$ROOT/scripts/train_owner_v14_pad200.sh"
fi

# Refuse if another detection.train already holds MPS
if pgrep -f 'src.detection.train' >/dev/null 2>&1; then
  echo "ERROR: src.detection.train already running — check MPS occupancy first:" >&2
  pgrep -lf 'src.detection.train' >&2 || true
  exit 1
fi

# Drop finished v13 train agent so it cannot be kickstarted into MPS by accident
V13_LABEL=com.fable.owner-v13-pad200-train
if launchctl print "$DOMAIN/$V13_LABEL" >/dev/null 2>&1; then
  echo "bootout stale $V13_LABEL"
  launchctl bootout "$DOMAIN/$V13_LABEL" 2>/dev/null || true
fi

# Truncate prior log so status "rolling" is unambiguous for this run
: >"$ROOT/logs/owner_v14_pad200_train.log"
: >"$ROOT/logs/owner_v14_pad200_train.launchd.err.log"

cp "$PLIST_SRC" "$PLIST_DST"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST_DST"
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null || true

sleep 3
bash "$ROOT/scripts/v14_train_status.sh"
echo "stop: launchctl bootout $DOMAIN/$LABEL"
echo "log:  tail -f $ROOT/logs/owner_v14_pad200_train.log"
