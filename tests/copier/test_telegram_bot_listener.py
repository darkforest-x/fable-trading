import pytest

from yoyo.copier import telegram_bot_listener
from yoyo.copier.telegram_bot_listener import _feedback_payload, _is_bug_report, _is_panel_command


def test_empty_message_is_not_panel_command():
    assert _is_panel_command("") is False
    assert _is_panel_command("   ") is False


def test_panel_commands_are_recognized():
    assert _is_panel_command("/start") is True
    assert _is_panel_command("/panel@autotrading_zhangbb_bot") is True


def test_bug_reports_are_recognized():
    assert _is_bug_report("#bug 为什么没有调用 api") is True
    assert _is_bug_report("/bug TP1 没有执行") is True
    assert _is_bug_report("普通消息") is False


def test_feedback_payload_keeps_replied_notification():
    payload = _feedback_payload(
        {
            "message_id": 88,
            "reply_to_message": {
                "message_id": 77,
                "text": "交易更新失败：余额不足",
            },
        },
        "-1001",
        explicit_bug=False,
    )

    assert payload["message_id"] == 88
    assert payload["reply_to_message_id"] == 77
    assert payload["reply_text"] == "交易更新失败：余额不足"
    assert payload["explicit_bug"] is False


@pytest.mark.asyncio
async def test_orders_card_snapshot_callback(monkeypatch):
    calls = []

    async def fake_api(_client, method, **_params):
        calls.append(method)
        return {}

    async def fake_snapshot(_db):
        calls.append("snapshot")
        return {"ok": True}

    monkeypatch.setattr(telegram_bot_listener.env, "telegram_notify_chat_id", "-1001")
    monkeypatch.setattr(telegram_bot_listener, "_api", fake_api)
    monkeypatch.setattr(telegram_bot_listener, "send_orders_card_snapshot", fake_snapshot)

    handled = await telegram_bot_listener._handle_callback(
        None,
        None,
        {
            "id": "cb-1",
            "data": "ctl:orders_card_snapshot",
            "message": {"message_id": 9, "chat": {"id": -1001}},
        },
    )

    assert handled is True
    assert "answerCallbackQuery" in calls
    assert "snapshot" in calls
