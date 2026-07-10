"""Safety contract tests for Label Studio VPS deploy scripts (static, no SSH).

Pass criteria (Todo 4A / TLS-hardening iteration):
- Django-valid default bootstrap email (example.com, not .local)
- Password never printed / never in argv / cleaned via trap
- Signup disabled, pack limited to 80, memory bounds, dashboard executor forced 0
- Compromised Telegram token never embedded
- Public URL is HTTPS; Label Studio binds loopback only; nginx TLS proxy
- Private key root-only; no inline secrets in unit/nginx
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy_label_studio_vps.sh"
UNIT = ROOT / "scripts" / "label_studio_vps.service"
NGINX = ROOT / "scripts" / "label_studio_nginx.conf"
GITIGNORE = ROOT / ".gitignore"

# Pattern of the compromised token form (never hardcode the full token in tests).
# Matches bot-style tokens if accidentally pasted: digits:base64ish
TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")


@pytest.fixture(scope="module")
def deploy_src() -> str:
    assert DEPLOY.is_file(), f"missing {DEPLOY}"
    return DEPLOY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def unit_src() -> str:
    assert UNIT.is_file(), f"missing {UNIT}"
    return UNIT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nginx_src() -> str:
    assert NGINX.is_file(), f"missing {NGINX}"
    return NGINX.read_text(encoding="utf-8")


def test_default_email_is_django_valid(deploy_src: str) -> None:
    assert "fable-review@example.com" in deploy_src
    assert "@fable.local" not in deploy_src
    assert re.search(r"@[\w.-]+\.local\b", deploy_src) is None


def test_password_never_echoed_to_stdout(deploy_src: str) -> None:
    # No echo/printf of PASS or PASS_FILE contents to console
    assert "echo \"$PASS\"" not in deploy_src
    assert "echo $PASS" not in deploy_src
    assert "echo \"$(cat \"$PASS_FILE\")\"" not in deploy_src
    assert "cat \"$PASS_FILE\"" in deploy_src  # only into redirected access note / stdin pipe
    assert "Credentials were NOT printed." in deploy_src
    # Password file content must not appear as ssh remote command argument
    assert not re.search(r'ssh\s+[^\n]*"\$\(cat', deploy_src)
    assert not re.search(r"ssh\s+[^\n]*PASS_FILE", deploy_src)


def test_pass_file_cleaned_on_all_exits(deploy_src: str) -> None:
    assert "trap cleanup_pass EXIT" in deploy_src or re.search(
        r"trap\s+cleanup_pass\s+EXIT", deploy_src
    )
    assert "rm -f" in deploy_src
    assert "PASS_FILE=$(mktemp)" in deploy_src or 'PASS_FILE=$(mktemp)' in deploy_src
    assert "chmod 600 \"$PASS_FILE\"" in deploy_src


def test_signup_disabled(deploy_src: str) -> None:
    assert "LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true" in deploy_src


def test_pack_limited_to_80(deploy_src: str) -> None:
    assert 'test "$N" = \'80\'' in deploy_src or "test \\\"\\$N\\\" = '80'" in deploy_src
    assert "pack_80" in deploy_src
    assert "expected 80 images" in deploy_src


def test_dashboard_executor_forced_zero(deploy_src: str) -> None:
    assert "ENABLE_JOB_EXECUTOR=0" in deploy_src
    assert "fable-dashboard" in deploy_src


def test_unit_memory_bounded(unit_src: str) -> None:
    assert "MemoryMax=1400M" in unit_src
    assert "MemoryHigh=1100M" in unit_src
    assert "CPUQuota=150%" in unit_src
    assert "EnvironmentFile=/etc/fable-label-studio.env" in unit_src
    # No secrets inline in unit file
    assert "PASSWORD" not in unit_src
    assert "TOKEN" not in unit_src.upper() or "TOKEN" not in unit_src


def test_no_telegram_token_embedded(deploy_src: str, unit_src: str, nginx_src: str) -> None:
    for src in (deploy_src, unit_src, nginx_src):
        assert TELEGRAM_BOT_TOKEN_RE.search(src) is None
        assert "TELEGRAM_BOT_TOKEN=" not in src
    assert "bot" not in deploy_src.lower() or "compromised paste never used" in deploy_src


def test_gitignore_covers_secrets_and_preserves_data_rule() -> None:
    text = GITIGNORE.read_text(encoding="utf-8")
    assert "/data/" in text  # root-only data ignore preserved
    assert "scripts/.label_studio.env" in text
    assert "LABEL_STUDIO_VPS_ACCESS.md" in text
    assert "LABEL_STUDIO_ACCESS.md" in text
    assert "output/label_studio/pack_80/" in text
    assert "label_studio_data/" in text
    assert ".venv_label_studio/" in text
    # core product ignores still present
    assert "datasets/" in text
    assert "runs/" in text
    assert "*.pt" in text


def test_unit_is_labeling_only(unit_src: str) -> None:
    assert "label-studio start" in unit_src
    assert "ENABLE_JOB_EXECUTOR" not in unit_src
    assert "8642" not in unit_src  # dashboard port not started by this unit


def test_public_url_is_https(deploy_src: str) -> None:
    assert 'PUBLIC_URL="https://103.214.174.58:8081"' in deploy_src or (
        "https://103.214.174.58" in deploy_src and "PUBLIC_PORT=8081" in deploy_src
    )
    assert "http://103.214.174.58:8081" not in deploy_src
    assert "LABEL_STUDIO_HOST=${PUBLIC_URL}" in deploy_src
    assert "CSRF_TRUSTED_ORIGINS=${PUBLIC_URL}" in deploy_src


def test_label_studio_loopback_only(unit_src: str, deploy_src: str) -> None:
    assert "--internal-host 127.0.0.1" in unit_src
    assert "--port 8082" in unit_src
    assert "--internal-host 0.0.0.0" not in unit_src
    assert "LS_PORT=8082" in deploy_src
    assert "FAIL: Label Studio backend is not loopback-only" in deploy_src
    # Public HTTP to app port must not be the public URL surface
    assert "0.0.0.0" not in unit_src


def test_nginx_tls_proxy(nginx_src: str, deploy_src: str) -> None:
    assert "listen 8081 ssl" in nginx_src
    assert "ssl_certificate" in nginx_src
    assert "ssl_certificate_key" in nginx_src
    assert "proxy_pass http://127.0.0.1:8082" in nginx_src
    assert "X-Forwarded-Proto" in nginx_src
    assert "103.214.174.58" in nginx_src
    assert "label_studio_nginx.conf" in deploy_src
    assert "subjectAltName=IP:103.214.174.58" in deploy_src
    assert "self-signed" in deploy_src.lower() or "self-signed" in deploy_src


def test_nginx_config_directories_exist_before_rsync(deploy_src: str) -> None:
    mkdir_marker = "/etc/nginx/sites-available /etc/nginx/sites-enabled"
    rsync_marker = 'rsync -az "$NGINX_SRC"'
    assert mkdir_marker in deploy_src
    assert rsync_marker in deploy_src
    assert deploy_src.index(mkdir_marker) < deploy_src.index(rsync_marker)


def test_private_key_permissions_enforced(deploy_src: str) -> None:
    assert "chmod 600 /etc/ssl/fable/label-studio.key" in deploy_src
    assert "/etc/ssl/fable/label-studio.key" in deploy_src
    assert "browser certificate warning" in deploy_src.lower() or (
        "certificate warning" in deploy_src.lower()
    )


def test_no_inline_secrets_in_proxy_and_unit(unit_src: str, nginx_src: str) -> None:
    for src in (unit_src, nginx_src):
        assert "LABEL_STUDIO_PASSWORD" not in src
        assert "BEGIN PRIVATE KEY" not in src
        assert "BEGIN RSA PRIVATE KEY" not in src
        assert TELEGRAM_BOT_TOKEN_RE.search(src) is None
