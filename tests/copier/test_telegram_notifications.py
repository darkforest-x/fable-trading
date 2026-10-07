import asyncio
from types import SimpleNamespace

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.notifications.telegram import _styled_title, notify_channel_message, notify_trade_pipeline
from yoyo.copier.store.sqlite import Database


def test_notification_titles_have_consistent_color_symbols():
    assert _styled_title("Woods 频道消息").startswith("🔵")
    assert _styled_title("开单信号 · ETH LONG").startswith("🟡")
    assert _styled_title("Gate 实盘下单成功").startswith("🟢")
    assert _styled_title("Gate 限价单提交成功").startswith("🟢")
    assert _styled_title("风控跳过").startswith("🟠")
    assert _styled_title("Gate 下单失败").startswith("🔴")
    assert _styled_title("交易更新 · ETH").startswith("🟣")


def test_notification_title_does_not_duplicate_existing_symbol():
    assert _styled_title("🟢 Gate 实盘下单成功") == "🟢 Gate 实盘下单成功"


def test_risk_rejection_notification_says_no_position(monkeypatch, tmp_path):
    sent = []

    async def fake_send(db, title, lines):
        sent.append({"title": title, "lines": lines})
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.notifications.telegram.send_telegram_notification", fake_send)
    db = Database(tmp_path / "t.db")

    asyncio.run(
        notify_trade_pipeline(
            db,
            title="风控拒绝",
            message_id=105,
            author="Nasdaq75 [Prime]",
            content="#ATOM SHORT",
            result=IntentResult(
                should_act=True,
                intent="open",
                confidence=0.95,
                symbol="ATOM",
                side="short",
                entry_low=1.753,
                entry_high=1.8,
                stop_loss=1.81,
            ),
            detail="已达最大持仓数 3",
        )
    )

    assert sent[-1]["title"] == "风控拒绝"
    assert "状态 · 未下单，无新增持仓" in sent[-1]["lines"]


def test_dry_run_trade_pipeline_does_not_send_telegram(monkeypatch, tmp_path):
    async def fail_send(*args, **kwargs):
        raise AssertionError("dry_run notifications should stay out of Telegram")

    monkeypatch.setattr("yoyo.copier.notifications.telegram.send_telegram_notification", fail_send)
    db = Database(tmp_path / "t.db")

    result = asyncio.run(
        notify_trade_pipeline(
            db,
            title="模拟下单已记录",
            message_id=1,
            author="kol",
            content="飞扬合约策略 具体产品：BTC",
            result=IntentResult(
                should_act=True,
                intent="open",
                confidence=0.95,
                symbol="BTC",
                side="short",
                entry_low=78000,
                entry_high=78300,
                stop_loss=80000,
                take_profit=74631,
            ),
            detail="dry_run",
            exec_result={"ok": True, "dry_run": True},
        )
    )

    assert result == {"ok": False, "skipped": True, "reason": "dry_run_notification_disabled"}


def test_limit_order_notification_names_order_type(monkeypatch, tmp_path):
    sent = []

    async def fake_send(db, title, lines):
        sent.append({"title": title, "lines": lines})
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.notifications.telegram.send_telegram_notification", fake_send)
    db = Database(tmp_path / "t.db")

    asyncio.run(
        notify_trade_pipeline(
            db,
            title="Gate 限价单提交成功",
            message_id=192,
            author="Woods",
            content="Hype limit 62.63/62.29 stop 60.20",
            result=IntentResult(
                should_act=True,
                intent="open",
                confidence=0.95,
                symbol="HYPE",
                side="long",
                entry_low=62.29,
                entry_high=62.63,
                entry_note="limit order",
                stop_loss=60.2,
            ),
            detail="限价单与止盈止损已提交",
            exec_result={
                "ok": True,
                "plan": {
                    "exchange": "gate",
                    "td_mode": "cross",
                    "position_pct": 1,
                    "leverage": 75,
                    "notional_leverage": 12,
                    "margin_usdt": 13.2324,
                    "notional_usdt": 992.4295,
                    "px": 62.46,
                    "entry_note": "limit order",
                },
            },
        )
    )

    assert sent[-1]["title"] == "Gate 限价单提交成功"
    assert "订单类型 限价单 · 限价委托价 62.46" in sent[-1]["lines"]
    assert "结果 · 限价单与止盈止损已提交" in sent[-1]["lines"]


def test_raw_channel_image_uses_send_photo_and_refreshes_panel(monkeypatch, tmp_path):
    calls = []

    async def fake_post(client, method, payload, **kwargs):
        calls.append({"method": method, "payload": payload})
        return SimpleNamespace(status_code=200, text="ok"), {
            "ok": True,
            "result": {"message_id": 321},
        }

    async def fake_refresh(client, db):
        calls.append({"method": "refresh", "payload": {}})
        return {"ok": True, "message_id": 322}

    monkeypatch.setattr("yoyo.copier.notifications.telegram.telegram_notifications_enabled", lambda db: True)
    monkeypatch.setattr("yoyo.copier.notifications.telegram._post_telegram", fake_post)
    monkeypatch.setattr("yoyo.copier.notifications.telegram._refresh_control_panel", fake_refresh)

    db = Database(tmp_path / "t.db")
    result = asyncio.run(
        notify_channel_message(
            db,
            channel_id="1131521990814089276",
            author="Arthur",
            content="[图片消息]",
            attachments=[
                {
                    "url": "https://cdn.discordapp.com/attachments/1/2/chart.png",
                    "filename": "chart.png",
                    "content_type": "image/png",
                }
            ],
            source="discord-web",
        )
    )

    assert result["ok"] is True
    assert calls[0]["method"] == "sendPhoto"
    assert calls[0]["payload"]["photo"] == "https://cdn.discordapp.com/attachments/1/2/chart.png"
    assert "Arthur行情分析 频道图片" in calls[0]["payload"]["caption"]
    assert calls[-1]["method"] == "refresh"
