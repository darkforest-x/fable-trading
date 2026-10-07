import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXT_DIR = ROOT / "tools" / "discord_copier_extension"


def test_chrome_extension_manifest_is_loadable():
    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "background.js"
    # Whole site: Discord is a single-page app, a tab opened at /app never matched channels/*.
    assert manifest["content_scripts"][0]["matches"] == ["https://discord.com/*"]
    assert "http://127.0.0.1:8080/*" in manifest["host_permissions"]


def test_chrome_extension_required_files_exist():
    for name in (
        "background.js",
        "content.js",
        "popup.html",
        "popup.css",
        "popup.js",
    ):
        path = EXT_DIR / name
        assert path.exists()
        assert path.stat().st_size > 0


def test_content_script_supports_open_signal_batch_capture():
    content = (EXT_DIR / "content.js").read_text(encoding="utf-8")
    popup = (EXT_DIR / "popup.html").read_text(encoding="utf-8")

    assert "DISCORD_OKX_CAPTURE_VISIBLE_SIGNALS" in content
    assert "findVisibleOpenSignalPayloads" in content
    assert "looksLikeOpenSignal" in content
    assert "isWoodsShorthandOpenSignal" in content
    assert "(?:stop|sl)" in content
    assert "target hit|targets hit|all targets hit|trade closed" in content
    assert "提交当前可见全部开仓信号" in popup


def test_content_script_has_recent_visible_message_fallback():
    content = (EXT_DIR / "content.js").read_text(encoding="utf-8")
    background = (EXT_DIR / "background.js").read_text(encoding="utf-8")

    assert "VISIBLE_RECENT_SCAN_MS" in content
    assert "VISIBLE_RECENT_MAX_AGE_MS" in content
    assert "24 * 60 * 60 * 1000" in content
    assert "scanRecentVisibleMessages" in content
    assert "isRecentVisiblePayload" in content
    assert "visible_recent_scan" in content
    assert "discord-web-visible_recovery" in background


def test_content_script_never_reads_direct_messages():
    content = (EXT_DIR / "content.js").read_text(encoding="utf-8")
    background = (EXT_DIR / "background.js").read_text(encoding="utf-8")

    assert "const guildId = getGuildId();\n  if (!guildId) return null;" in content
    assert "function getGuildId()" in content
    assert "guild_id: payload.guild_id" in background
