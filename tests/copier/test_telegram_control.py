import json
import tempfile
from pathlib import Path

import pytest

from yoyo.copier.config import ROOT as COPIER_HOME
from yoyo.copier.store.sqlite import Database
from yoyo.copier.telegram_control import (
    blogger_keyboard,
    control_keyboard,
    render_db_blogger_history,
    render_blogger_panel,
    render_blogger_losses,
    render_blogger_recent,
    render_blogger_signals,
    render_callback_response,
    render_channel_report,
    panel_text,
    render_orders,
    render_ping,
    render_status,
    render_stats,
    render_my_orders,
)

# These read the owner's scraped Discord history (runtime data under COPIER_HOME,
# never in git). The suite runs against an empty temp home, so they skip there.
requires_history = pytest.mark.skipif(
    not (COPIER_HOME / "data" / "chartprime_trades_2026.json").exists()
    or not (COPIER_HOME / "data" / "feiyang_raw_messages_2026.json").exists(),
    reason="needs scraped trader history under COPIER_HOME/data (runtime data, not in git)",
)



def test_control_keyboard_contains_expected_buttons():
    keyboard = control_keyboard()
    text = json.dumps(keyboard, ensure_ascii=False)

    assert "Ping" in text
    assert "ChartPrime" in text
    assert "Woods" in text
    assert "比特币飞扬" in text
    assert "Arthur行情" in text
    assert "最近信号" in text
    assert "我的订单" in text
    assert "持仓截图" in text
    assert "ctl:orders_card_snapshot" in text
    assert "全部统计" in text
    assert "总览状态" in text
    assert panel_text()

    cp_keyboard = json.dumps(blogger_keyboard("chartprime"), ensure_ascii=False)
    assert "开单历史" in cp_keyboard
    assert "频道分析" in cp_keyboard
    assert "亏损样本" in cp_keyboard


def test_panel_text_shows_route_balances_and_profit(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting(
            "exchange_channel_map",
            json.dumps(
                {
                    "1226095564073205780": "gate",
                    "1131521990814089276": "gate_mia",
                    "1356581750914027590": "gate_feiyang",
                    "988830102957736027": "okx",
                }
            ),
        )
        db.set_setting(
            "channel_leverage_map",
            json.dumps(
                {
                    "1226095564073205780": 12,
                    "1131521990814089276": 5,
                    "1356581750914027590": 15,
                    "988830102957736027": 20,
                }
            ),
        )
        monkeypatch.setattr(
            "yoyo.copier.telegram_control._exchange_snapshot_map",
            lambda exchanges: {
                "gate": {"balance": {"total_usdt": 81.15, "unrealized_pnl": 7.16}},
                "gate_mia": {"balance": {"total_usdt": 350, "unrealized_pnl": -1.2}},
                "gate_feiyang": {"balance": {"total_usdt": 135.29, "unrealized_pnl": 0}},
                "okx": {"balance": {"total_usdt": 500, "unrealized_pnl": 12.34}},
            },
        )

        text = panel_text(db)

    assert "Woods" in text and "Gate 12x" in text and "81.15U" in text and "+7.16U" in text
    assert "Arthur行情分析" in text and "Gate Arthur 5x" in text and "350.00U" in text and "-1.20U" in text
    assert "比特币飞扬" in text and "Gate 飞扬 15x" in text and "135.29U" in text and "+0.00U" in text
    assert "ChartPrime" in text and "OKX 20x" in text and "500.00U" in text and "+12.34U" in text
    assert "🟢" in text
    assert "🔴" not in text
    assert "可用" not in text


def test_panel_text_falls_back_to_last_exchange_snapshot(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("exchange_channel_map", json.dumps({"1226095564073205780": "gate"}))
        db.set_setting("channel_leverage_map", json.dumps({"1226095564073205780": 12}))
        db.set_setting(
            "telegram_panel_exchange_snapshot_gate",
            json.dumps({"balance": {"total_usdt": 94.75, "unrealized_pnl": 2.08}}),
        )
        monkeypatch.setattr("yoyo.copier.telegram_control._exchange_snapshot_map", lambda exchanges: {})

        text = panel_text(db)

    assert "Woods" in text
    assert "94.75U" in text
    assert "+2.08U" in text
    assert "🔴" not in text


def test_render_stats_and_orders_from_database():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        message_id = db.insert_message("tg-panel-1", "ch", "kol", "BTC 多单")
        signal_id = db.insert_signal(
            message_id=message_id,
            intent="open",
            should_act=True,
            confidence=0.91,
            ai_json=json.dumps({"symbol": "BTC", "side": "long"}),
            reject_reason=None,
        )
        db.insert_order(
            message_id=message_id,
            signal_id=signal_id,
            inst_id="BTC-USDT-SWAP",
            side="buy",
            ord_type="limit",
            sz="1",
            px="60000",
            okx_ord_id="123",
            sl_trigger="59000",
            tp_trigger="62000",
            status="live",
            response_json=json.dumps({"plan": {"margin_usdt": 5}}),
        )

        assert "Pong" in render_ping()
        stats = render_stats(db, refresh=False)
        assert "我的跟单数据" in stats
        assert "按博主归属" in stats
        assert "信号回测" in stats
        orders = render_orders(db)
        assert "我的订单" in orders
        assert "当前持仓" in orders
        assert "等待成交" in orders
        assert "历史记录" in orders
        assert "BTC-USDT-SWAP" in orders
        assert "名义仓位" in orders
        assert "数量" not in orders
        assert "合约数" not in orders


def test_render_stats_groups_my_follow_results_by_blogger():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        open_message = db.insert_message(
            "newblogger-open",
            "1226095564073205780",
            "Woods",
            "longed VELVET at 0.4 sl: 0.36",
        )
        signal_id = db.insert_signal(
            message_id=open_message,
            intent="open",
            should_act=True,
            confidence=0.99,
            ai_json=json.dumps({"symbol": "VELVET", "side": "long"}),
            reject_reason=None,
        )
        db.insert_order(
            message_id=open_message,
            signal_id=signal_id,
            inst_id="VELVET-USDT-SWAP",
            side="buy",
            ord_type="limit",
            status="closed",
            response_json="{}",
        )
        db.insert_message(
            "newblogger-close",
            "1226095564073205780",
            "Woods",
            "VELVET: Closed in small profit (100%)",
        )

        text = render_stats(db, refresh=False)

    assert "Woods" in text
    assert "成功跟单 <code>1</code>" in text
    assert "胜 <code>1</code> / 亏 <code>0</code>" in text
    assert "胜率 <code>100.0%</code>" in text
    assert "回测是博主历史信号表现，不等同于你的实盘利润" in text


def test_my_orders_groups_history_by_blogger_and_marks_missing_live_orders(monkeypatch):
    monkeypatch.setattr(
        "yoyo.copier.telegram_control._live_order_snapshot",
        lambda db: ([], [], [], []),
    )
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        new_blogger_msg = db.insert_message(
            "new-blogger-order",
            "1131521990814089276",
            "Arthur行情分析",
            "DOGE limit 0.0879 0.085 stop 0.0819",
        )
        woods_msg = db.insert_message(
            "woods-order",
            "1226095564073205780",
            "Woods",
            "longed VELVET at 0.4 sl: 0.36",
        )
        db.insert_order(
            message_id=new_blogger_msg,
            signal_id=None,
            inst_id="DOGE-USDT-SWAP",
            side="buy",
            ord_type="limit",
            px="0.08645",
            okx_ord_id="doge-local",
            status="live",
            response_json=json.dumps({"plan": {"notional_usdt": 1000}}),
        )
        db.insert_order(
            message_id=woods_msg,
            signal_id=None,
            inst_id="VELVET-USDT-SWAP",
            side="buy",
            ord_type="limit",
            px="0.4",
            okx_ord_id="velvet-local",
            status="live",
            response_json=json.dumps({"plan": {"notional_usdt": 500}}),
        )

        text = render_my_orders(db, refresh=True)

    assert "历史记录 · 按博主" in text
    assert "<b>Arthur行情分析</b>" in text
    assert "<b>Woods</b>" in text
    assert "交易所无持仓/挂单" in text
    assert "DOGE-USDT-SWAP" in text
    assert "VELVET-USDT-SWAP" in text


def test_blogger_panel_and_recent_are_channel_scoped():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        msg_id = db.insert_message(
            "new-blogger-open",
            "1131521990814089276",
            "Arthur行情分析",
            "Xpl limit 0.0782 0.075 stop 0.067",
        )
        db.insert_signal(
            message_id=msg_id,
            intent="open",
            should_act=True,
            confidence=0.96,
            ai_json=json.dumps({"symbol": "BTC", "side": "long"}),
        )

        panel = render_blogger_panel(db, "mia", refresh=False)
        recent = render_blogger_recent(db, "mia")

    assert "Arthur行情分析" in panel
    assert "1131521990814089276" in panel
    assert "开仓信号 <code>1</code>" in panel
    assert "Xpl limit" in recent


def test_render_status_hides_internal_flags_and_uses_order_records(monkeypatch):
    monkeypatch.setattr("yoyo.copier.telegram_control.routed_exchange_names", lambda db: [])
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.insert_order(
            message_id=None,
            signal_id=None,
            inst_id="BTC-USDT-SWAP",
            side="buy",
            ord_type="limit",
            sz="1",
            px="60000",
            okx_ord_id="123",
            sl_trigger="59000",
            tp_trigger="62000",
            status="live",
            response_json="{}",
        )
        status = render_status(db)

    assert "dry_run" not in status
    assert "kill_switch" not in status
    assert "今日成功下单记录" in status
    assert "累计成功下单记录: <code>1</code>" in status


def test_render_channel_report_contains_chartprime_summary():
    report = render_channel_report()

    assert "ChartPrime 频道分析" in report
    assert "988830102957736027" in report
    assert "已结算胜率" in report
    assert "500U 全仓 5x 复利回测" in report
    assert "期末权益" in report
    assert "最大回撤" in report
    assert "旧口径" not in report
    assert "比特币飞扬频道分析" in report
    assert "明确盈亏胜率" in report


@requires_history
def test_render_blogger_history_lists_saved_chartprime_samples():
    signals = render_blogger_signals(page=3)
    losses = render_blogger_losses()

    assert "开单历史" in signals
    assert "ChartPrime | 3/8 | 90 trades" in signals
    assert "LINK LONG" in signals
    assert "TP1" in signals
    assert "博主明确亏损样本" in losses
    assert "SUI SHORT" in losses
    assert "SL 1.44" in losses


@requires_history
def test_history_callback_has_pagination_keyboard():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        response = render_callback_response("ctl:history:1", db)
        keyboard = json.dumps(response["reply_markup"], ensure_ascii=False)

    assert "开单历史" in response["text"]
    assert "1/8" in response["text"]
    assert "上一页" in keyboard
    assert "下一页" in keyboard
    assert "频道分析" in keyboard
    assert "我的订单" in keyboard
    assert "刷新" in keyboard


@requires_history
def test_feiyang_history_callback_has_statistics():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        response = render_callback_response("ctl:history:feiyang:1", db)
        keyboard = json.dumps(response["reply_markup"], ensure_ascii=False)

    assert "比特币飞扬" in response["text"]
    assert "38 trades" in response["text"]
    assert "比特币飞扬 统计" in keyboard
    assert "开单历史" in keyboard


def test_woods_database_history_lists_open_signals():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        message_id = db.insert_message(
            "woods-open-1",
            "1226095564073205780",
            "Woods",
            "@Woods粉 Btc limit 65.4/65.3 stop 63.5",
        )
        db.insert_signal(
            message_id=message_id,
            intent="open",
            should_act=True,
            confidence=0.95,
            ai_json=json.dumps(
                {
                    "symbol": "BTC",
                    "side": "long",
                    "entry_low": 65.3,
                    "entry_high": 65.4,
                    "stop_loss": 63.5,
                }
            ),
        )
        db.insert_signal(
            message_id=message_id,
            intent="open",
            should_act=True,
            confidence=0.95,
            ai_json=json.dumps(
                {
                    "symbol": "BTC",
                    "side": "long",
                    "entry_low": 65300,
                    "entry_high": 65400,
                    "stop_loss": 63500,
                }
            ),
        )

        text, pages = render_db_blogger_history(db, "woods", page=1)

    assert pages == 1
    assert "Woods | 1/1 | 1 trades" in text
    assert "BTC LONG" in text
    assert "entry 65300-65400" in text
    assert "SL 63500" in text


def test_woods_history_callback_lists_saved_history(monkeypatch):
    monkeypatch.setattr(
        "yoyo.copier.telegram_control.load_woods_trades",
        lambda: [
            {
                "time": "2026-06-16T13:32:59.706Z",
                "symbol": "BTC",
                "side": "long",
                "entry_low": 65300,
                "entry_high": 65400,
                "stop_loss": 63500,
                "result": "breakeven",
            }
        ],
    )
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        response = render_callback_response("ctl:history:woods:1", db)
        keyboard = json.dumps(response["reply_markup"], ensure_ascii=False)

    assert "Woods | 1/1 | 1 trades" in response["text"]
    assert "BTC LONG" in response["text"]
    assert "Woods 统计" in keyboard
    assert "开单历史" in keyboard
