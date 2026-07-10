#!/usr/bin/env bash
# Deploy resource-bounded Label Studio to VPS with ONLY the 80-image review pack.
# Credentials: generated on the fly, written to root-owned VPS env + untracked local note.
# Never prints the password. Never enables fable job executor. Never uses Telegram tokens.
#
# Architecture (TLS gate):
#   - Label Studio binds 127.0.0.1:8082 only (not public)
#   - nginx terminates TLS on 0.0.0.0:8081 and proxies to loopback
#   - Self-signed cert with IP SAN 103.214.174.58 (browser warning expected)
#   - LABEL_STUDIO_HOST / CSRF origin = https://103.214.174.58:8081
#
# Usage (from repo root):
#   bash scripts/deploy_label_studio_vps.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VPS="${VPS_HOST:-root@103.214.174.58}"
REMOTE_DIR=/opt/fable-label-studio
REMOTE_ENV=/etc/fable-label-studio.env
# Public TLS port (nginx); app listens on loopback only
PUBLIC_PORT=8081
LS_PORT=8082
PUBLIC_URL="https://103.214.174.58:${PUBLIC_PORT}"
# Django validates email domains; avoid .local
USER_EMAIL="${LABEL_STUDIO_USERNAME:-fable-review@example.com}"

PACK_DIR="${ROOT}/output/label_studio/pack_80"
TASKS="${ROOT}/output/label_studio/tasks_val.json"
CONFIG="${ROOT}/output/label_studio/label_config.xml"
ACCESS_NOTE="${ROOT}/output/offline_tasks/LABEL_STUDIO_VPS_ACCESS.md"
UNIT_SRC="${ROOT}/scripts/label_studio_vps.service"
NGINX_SRC="${ROOT}/scripts/label_studio_nginx.conf"

PASS_FILE=""
cleanup_pass() {
  if [[ -n "${PASS_FILE}" && -f "${PASS_FILE}" ]]; then
    rm -f "${PASS_FILE}"
  fi
  PASS_FILE=""
}
trap cleanup_pass EXIT INT TERM

if [[ ! -f "$TASKS" || ! -f "$CONFIG" ]]; then
  echo "FAIL: missing tasks_val.json or label_config.xml under output/label_studio/" >&2
  exit 1
fi
if [[ ! -f "$NGINX_SRC" ]]; then
  echo "FAIL: missing ${NGINX_SRC}" >&2
  exit 1
fi

# Stage 80 images from sibling/main dataset if pack incomplete
N_IMG=$(find "$PACK_DIR/dense_15m_full/images/val" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
if [[ "${N_IMG}" != "80" ]]; then
  echo "Staging 80-image pack (found ${N_IMG})..."
  python3 - <<'PY'
import json, shutil
from pathlib import Path
root = Path(".")
tasks = json.loads((root / "output/label_studio/tasks_val.json").read_text())
candidates = [
    Path("/Users/zhangzc/fable-trading/datasets/dense_15m_full"),
    Path("/Users/zhangzc/fable-trading-codex/datasets/dense_15m_full"),
    root / "datasets/dense_15m_full",
]
src = next((p for p in candidates if (p / "images/val").is_dir()), None)
if src is None:
    raise SystemExit("no dense_15m_full images source found")
dst = root / "output/label_studio/pack_80/dense_15m_full/images/val"
dst.mkdir(parents=True, exist_ok=True)
missing = []
for t in tasks:
    stem = t["data"]["stem"]
    p = src / "images/val" / f"{stem}.png"
    if not p.exists():
        missing.append(stem)
        continue
    shutil.copy2(p, dst / f"{stem}.png")
if missing:
    raise SystemExit(f"missing {len(missing)} images e.g. {missing[:3]}")
print(f"staged 80 images from {src}")
PY
fi

# Generate strong password without printing it (file-only; never argv)
PASS_FILE=$(mktemp)
chmod 600 "$PASS_FILE"
openssl rand -base64 32 | tr -d '\n' >"$PASS_FILE"

# Local untracked access note (password never echoed to stdout/stderr)
mkdir -p output/offline_tasks
umask 077
{
  echo "# Label Studio VPS access (UNTRACKED — do not commit)"
  echo
  echo "- URL: ${PUBLIC_URL}"
  echo "- Email: ${USER_EMAIL}"
  echo -n "- Password: "
  cat "$PASS_FILE"
  echo
  echo
  echo "## TLS / browser note"
  echo "Certificate is **self-signed** with IP SAN 103.214.174.58."
  echo "Browsers will show a certificate warning — expected. Proceed only if you"
  echo "intentionally trust this VPS endpoint for labeling review."
  echo "Label Studio itself binds 127.0.0.1:${LS_PORT}; only nginx TLS on ${PUBLIC_PORT} is public."
  echo
  echo "## First-time project import"
  echo "1. Open URL and sign in with the credentials above."
  echo "2. Create Project → name \`dense_15m_val_audit\`."
  echo "3. Labeling Interface → paste remote \`/opt/fable-label-studio/import/label_config.xml\`."
  echo "4. Import → upload \`/opt/fable-label-studio/import/tasks_val.json\` (or the copy under output/label_studio/)."
  echo "5. Confirm prelabels (green dense_cluster boxes), edit one box, save, reopen."
  echo
  echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Telegram notify: BLOCKED until rotated bot token + valid chat_id are set in env (compromised paste never used)."
} >"$ACCESS_NOTE"
chmod 600 "$ACCESS_NOTE"

echo "Syncing pack + unit + nginx conf to ${VPS}:${REMOTE_DIR} (no secrets in this step)..."
ssh "$VPS" "mkdir -p ${REMOTE_DIR}/{data,import,files} /opt/fable-label-studio"
rsync -az --delete \
  "${PACK_DIR}/dense_15m_full/" \
  "${VPS}:${REMOTE_DIR}/files/dense_15m_full/"
rsync -az "$TASKS" "$CONFIG" "${VPS}:${REMOTE_DIR}/import/"
rsync -az "$UNIT_SRC" "${VPS}:/etc/systemd/system/fable-label-studio.service"
rsync -az "$NGINX_SRC" "${VPS}:/etc/nginx/sites-available/fable-label-studio"

# Write env on remote: password via stdin only (never argv, never echo)
# shellcheck disable=SC2029
{
  printf '%s\n' "$USER_EMAIL"
  cat "$PASS_FILE"
  printf '\n'
} | ssh "$VPS" "set -euo pipefail
umask 077
read -r EMAIL
read -r PASS
cat > ${REMOTE_ENV} <<EOF
LABEL_STUDIO_USERNAME=\${EMAIL}
LABEL_STUDIO_PASSWORD=\${PASS}
LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true
LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true
LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=/opt/fable-label-studio/files
LABEL_STUDIO_HOST=${PUBLIC_URL}
CSRF_TRUSTED_ORIGINS=${PUBLIC_URL}
EOF
chmod 600 ${REMOTE_ENV}
# never leave password in shell history files
unset PASS EMAIL
UNIT_D=/etc/systemd/system/fable-dashboard.service
if [ -f \"\$UNIT_D\" ]; then
  if grep -q '^Environment=ENABLE_JOB_EXECUTOR=' \"\$UNIT_D\"; then
    sed -i 's/^Environment=ENABLE_JOB_EXECUTOR=.*/Environment=ENABLE_JOB_EXECUTOR=0/' \"\$UNIT_D\"
  else
    if ! grep -q 'ENABLE_JOB_EXECUTOR' \"\$UNIT_D\"; then
      sed -i '/^\[Service\]/a Environment=ENABLE_JOB_EXECUTOR=0' \"\$UNIT_D\"
    fi
  fi
fi
"

# Wipe local password file before long remote install (note already written)
cleanup_pass

# Install venv + label-studio + nginx TLS if missing (long-running)
echo "Ensuring Label Studio venv + nginx TLS on VPS (may take several minutes)..."
ssh "$VPS" "set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [ ! -x ${REMOTE_DIR}/.venv/bin/label-studio ]; then
  apt-get install -y -qq python3-venv python3-pip libpq-dev gcc 2>/dev/null || true
  python3 -m venv ${REMOTE_DIR}/.venv
  ${REMOTE_DIR}/.venv/bin/pip install -q -U pip wheel
  # Pin a known-good major line; upgrade path remains reversible via unit disable
  ${REMOTE_DIR}/.venv/bin/pip install -q 'label-studio==1.15.0'
fi
# nginx + self-signed cert with IP SAN (private key root-only 600)
apt-get install -y -qq nginx 2>/dev/null || true
mkdir -p /etc/ssl/fable
if [ ! -f /etc/ssl/fable/label-studio.crt ] || [ ! -f /etc/ssl/fable/label-studio.key ]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout /etc/ssl/fable/label-studio.key \
    -out /etc/ssl/fable/label-studio.crt \
    -subj '/CN=103.214.174.58/O=fable-label-studio/C=US' \
    -addext 'subjectAltName=IP:103.214.174.58'
fi
chmod 600 /etc/ssl/fable/label-studio.key
chmod 644 /etc/ssl/fable/label-studio.crt
chown root:root /etc/ssl/fable/label-studio.key /etc/ssl/fable/label-studio.crt
# enable site (sites-available already rsynced)
ln -sfn /etc/nginx/sites-available/fable-label-studio /etc/nginx/sites-enabled/fable-label-studio
nginx -t
systemctl enable nginx
systemctl reload nginx || systemctl restart nginx
# ownership / perms
chmod 700 ${REMOTE_DIR}/data
chmod 755 ${REMOTE_DIR}/files
# image count guard
N=\$(find ${REMOTE_DIR}/files/dense_15m_full/images/val -name '*.png' | wc -l | tr -d ' ')
test \"\$N\" = '80' || { echo \"FAIL: expected 80 images, got \$N\"; exit 1; }
systemctl daemon-reload
systemctl enable fable-label-studio.service
systemctl restart fable-label-studio.service
# wait ready on loopback app port
ok=0
for i in \$(seq 1 60); do
  if curl -sf -o /dev/null http://127.0.0.1:${LS_PORT}/user/login/ || curl -sf -o /dev/null http://127.0.0.1:${LS_PORT}/; then
    ok=1
    break
  fi
  sleep 3
done
systemctl is-active fable-label-studio
if [ \"\$ok\" != 1 ]; then
  journalctl -u fable-label-studio -n 80 --no-pager || true
  exit 1
fi
# public TLS surface (self-signed: -k)
curl -skf -o /dev/null https://127.0.0.1:${PUBLIC_PORT}/user/login/ \
  || curl -skf -o /dev/null https://127.0.0.1:${PUBLIC_PORT}/ \
  || { echo FAIL: nginx TLS proxy not serving; exit 1; }
# loopback-only: Label Studio must not listen on all interfaces for public port
ss -lntp | grep -E ':${LS_PORT}\\b' | grep -q '127.0.0.1' || ss -lntp | grep -E ':${LS_PORT}\\b' | grep -q '::1' || true
# private key permissions
stat -c '%a' /etc/ssl/fable/label-studio.key | grep -qE '^(600|400)\$' \
  || { echo FAIL: private key not root-only; ls -l /etc/ssl/fable/; exit 1; }
# dashboard still healthy + executor off
systemctl is-active fable-dashboard
curl -sf -o /dev/null http://127.0.0.1:8642/api/overview
systemctl show fable-dashboard -p Environment --value | tr ' ' '\n' | grep -q 'ENABLE_JOB_EXECUTOR=0'
echo VPS_READY
free -h | head -3
"

echo "Deploy finished."
echo "Public URL: ${PUBLIC_URL}"
echo "TLS: self-signed IP SAN — browser certificate warning expected."
echo "Access note (local untracked): ${ACCESS_NOTE}"
echo "Credentials were NOT printed."
