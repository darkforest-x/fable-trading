#!/bin/bash
# 一键把看板部署/更新到 VPS（需已配置 SSH 密钥）
# 用法: bash scripts/deploy_vps.sh
set -euo pipefail
VPS=root@103.214.174.58
DIR=/opt/fable-trading
cd "$(dirname "$0")/.."

rsync -az --exclude='__pycache__' --exclude='*.pyc' \
  src analysis models requirements.txt "$VPS:$DIR/"
rsync -az \
  data/judgment_dataset_v2_expanded.csv data/forward_log.csv \
  data/scored_signals*.csv data/scored_signals*.json \
  "$VPS:$DIR/data/"
rsync -az data/sweep_v3 data/swap_replication data/kline_fetched "$VPS:$DIR/data/"
# Hard red line: never leave job executor enabled on public VPS.
# Also re-assert EnvironmentFile for root-only ops auth (token never written here).
ssh "$VPS" "set -euo pipefail
UNIT=/etc/systemd/system/fable-dashboard.service
ENVF=/etc/fable-trading/ops.env
if [ -f \"\$UNIT\" ]; then
  if grep -q '^Environment=ENABLE_JOB_EXECUTOR=' \"\$UNIT\"; then
    sed -i 's/^Environment=ENABLE_JOB_EXECUTOR=.*/Environment=ENABLE_JOB_EXECUTOR=0/' \"\$UNIT\"
  else
    # Insert after [Service] if missing
    if ! grep -q 'ENABLE_JOB_EXECUTOR' \"\$UNIT\"; then
      sed -i '/^\[Service\]/a Environment=ENABLE_JOB_EXECUTOR=0' \"\$UNIT\"
    fi
  fi
  # Wire optional root-only env file without creating/overwriting secrets.
  if ! grep -q 'EnvironmentFile=-/etc/fable-trading/ops.env' \"\$UNIT\"; then
    sed -i '/^\[Service\]/a EnvironmentFile=-/etc/fable-trading/ops.env' \"\$UNIT\"
  fi
  systemctl daemon-reload
fi
systemctl restart fable-dashboard
for i in 1 2 3 4 5 6; do
  if systemctl is-active fable-dashboard >/dev/null \
    && curl -s -o /dev/null -w 'http:%{http_code}\n' http://127.0.0.1:8642/api/overview | grep -q 'http:200'; then
    systemctl is-active fable-dashboard
    echo http:200
    # surface executor flag for operators (never dump OPS_API_TOKEN)
    systemctl show fable-dashboard -p Environment --value 2>/dev/null | tr ' ' '\n' | grep ENABLE_JOB || true
    if [ -f \"\$ENVF\" ]; then
      echo \"ops_env:present mode=\$(stat -c %a \"\$ENVF\" 2>/dev/null || echo ?)\"
    else
      echo 'ops_env:missing (public /api/ops/* will be open unless token mode is set)'
    fi
    exit 0
  fi
  sleep 2
done
systemctl status fable-dashboard --no-pager
journalctl -u fable-dashboard -n 80 --no-pager
exit 1"
echo "done -> http://103.214.174.58:8642"
