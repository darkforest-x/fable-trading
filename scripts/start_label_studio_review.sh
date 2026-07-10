#!/bin/bash
# Start Label Studio for dense_15m_full visual review (local only).
# Port 8081 (8080 often taken). Credentials written to untracked access file.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p label_studio_data output/offline_tasks output/label_studio

# Ensure import pack exists
python3 scripts/label_studio_prepare_import.py --split val --limit 80 --seed 20260709 --stratify

# Local-only bootstrap user (NOT for VPS/public). File is gitignored via output/ or local.
USER_EMAIL="${LABEL_STUDIO_USERNAME:-fable-review@example.com}"
USER_PASS="${LABEL_STUDIO_PASSWORD:-fable-review-local}"

cat > output/offline_tasks/LABEL_STUDIO_ACCESS.md <<EOF
# Label Studio 本地访问（仅本机）

- URL: http://127.0.0.1:8081
- 用户: ${USER_EMAIL}
- 密码: ${USER_PASS}
- 项目: 启动后若未自动导入，见下方「手动导入」

## 手动导入（备用）

1. 打开 http://127.0.0.1:8081 登录
2. Create Project → 名称 \`dense_15m_val_audit\`
3. Labeling Interface → 粘贴 \`output/label_studio/label_config.xml\`
4. Import → 上传 \`output/label_studio/tasks_val.json\`
5. Settings → Cloud Storage 一般不需要（tasks 里已是 local-files URL）

数据只读挂载: datasets/dense_15m_full
生成时间: $(date)
EOF

export LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true
export LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT="${ROOT}/datasets"
export LABEL_STUDIO_USERNAME="${USER_EMAIL}"
export LABEL_STUDIO_PASSWORD="${USER_PASS}"
export LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true

# Prefer Docker if daemon up
if docker info >/dev/null 2>&1; then
  # compose file volumes are relative to scripts/
  docker compose -f scripts/label_studio_compose.yml down 2>/dev/null || true
  # inject user into compose via env file
  cat > scripts/.label_studio.env <<EOF
LABEL_STUDIO_USERNAME=${USER_EMAIL}
LABEL_STUDIO_PASSWORD=${USER_PASS}
LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true
LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=/label-studio/files
EOF
  # patch compose to use env_file if not already
  docker compose -f scripts/label_studio_compose.yml --env-file scripts/.label_studio.env up -d
  echo "Label Studio docker starting on :8081"
  for i in $(seq 1 40); do
    if curl -sf -o /dev/null http://127.0.0.1:8081/; then
      echo "Label Studio ready http://127.0.0.1:8081"
      exit 0
    fi
    sleep 3
  done
  echo "WARN: docker up but health check slow; still try :8081"
  exit 0
fi

# Fallback: pip venv
VENV="${ROOT}/.venv_label_studio/bin/label-studio"
if [ ! -x "$VENV" ]; then
  python3 -m venv .venv_label_studio
  .venv_label_studio/bin/pip install -q 'label-studio>=1.13'
fi
echo "Starting Label Studio (venv) on :8081"
exec .venv_label_studio/bin/label-studio start -b --host 127.0.0.1 --port 8081
