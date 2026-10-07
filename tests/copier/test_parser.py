import asyncio

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.ai.deepseek_client import DeepseekClient


OPEN_SIGNAL = """飞扬合约策略
具体产品：BTC
进行方向：做空
进场点位：78000-78300
止损点位：80000
止盈点位：74631"""

NOISE_SIGNAL = "今日没有入场不用急，明天继续"


def test_mock_open_signal():
    client = DeepseekClient()
    r = client._mock_analyze(OPEN_SIGNAL)
    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "BTC"
    assert r.side == "short"
    assert r.stop_loss == 80000
    assert r.take_profit == 74631


def test_mock_noise():
    client = DeepseekClient()
    r = client._mock_analyze(NOISE_SIGNAL)
    assert r.intent == "noise"
    assert r.should_act is False


def test_zec_signal():
    text = """飞扬合约策略
具体产品: ZEC
进行方向: 做空
进场点位: 568-570
止损点位: 599
止盈点位: 521"""
    r = DeepseekClient()._mock_analyze(text)
    assert r.symbol == "ZEC"
    assert r.entry_low == 568
    assert r.entry_high == 570


def test_chartprime_english_open_signal():
    text = (
        "LINK (Blofin) @Crypto Signal LONG: 5-10x,"
        "ENTRY: 7.75-7.88,"
        "EXIT: 7.91/7.95/7.99/8.06/8.16/8.30,"
        "SL: 7.65,"
        "THIS IS A SHORT-TERM TRADE"
    )
    r = DeepseekClient()._mock_analyze(text)

    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "LINK"
    assert r.side == "long"
    assert r.entry_low == 7.75
    assert r.entry_high == 7.88
    assert r.stop_loss == 7.65
    assert r.take_profit == 7.91
    assert r.leverage_hint == 5
    assert r.strategy_name == "ChartPrime Crypto Signal"
    assert r.entry_note == "market"


def test_woods_limit_short_signal_with_comma_prices():
    text = "Btc limit short 64,154-64,797 stop 66,142"
    r = DeepseekClient()._mock_analyze(text)

    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "BTC"
    assert r.side == "short"
    assert r.entry_low == 64154
    assert r.entry_high == 64797
    assert r.stop_loss == 66142
    assert r.strategy_name == "Woods Signal"


def test_woods_btc_k_shorthand_scales_entries_and_stop():
    r = DeepseekClient()._mock_analyze("Btc limit 65.4/65.3 stop 63.5")

    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "BTC"
    assert r.side == "long"
    assert r.entry_low == 65300
    assert r.entry_high == 65400
    assert r.stop_loss == 63500


def test_woods_shorthand_signal_infers_long_from_stop_below_entry():
    r = DeepseekClient()._mock_analyze("Near 1.998 stop 1.88")

    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "NEAR"
    assert r.side == "long"
    assert r.entry_low == 1.998
    assert r.entry_high == 1.998
    assert r.stop_loss == 1.88


def test_woods_doge_missing_decimal_zero_is_normalized():
    r = DeepseekClient()._mock_analyze("Doge .8775 stop .856")

    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "DOGE"
    assert r.side == "long"
    assert r.entry_low == 0.08775
    assert r.entry_high == 0.08775
    assert r.stop_loss == 0.0856


def test_woods_shorthand_is_parsed_before_ai_translation_noise():
    async def run():
        client = DeepseekClient()
        client.client = object()
        return await client.analyze(
            "@Woods粉 Hype 58.76 stop 57.76 .5% risk "
            "------------------------------- Hype 58.76 平仓 57.76，0.5%的风险"
        )

    r = asyncio.run(run())

    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "HYPE"
    assert r.side == "long"
    assert r.entry_low == 58.76
    assert r.stop_loss == 57.76


def test_eliz_limit_signal_with_space_separated_range():
    result = DeepseekClient()._mock_analyze("Xpl limit 0.0782 0.075 stop 0.067")

    assert result.intent == "open"
    assert result.should_act is True
    assert result.symbol == "XPL"
    assert result.side == "long"
    assert result.entry_low == 0.075
    assert result.entry_high == 0.0782
    assert result.stop_loss == 0.067


def test_eliz_long_signal_with_direction_before_symbol_and_hidden_stop_chars():
    result = DeepseekClient()._mock_analyze("Long useless 0.1 0.093 s\u2060\u2063top 0.088")

    assert result.intent == "open"
    assert result.should_act is True
    assert result.symbol == "USELESS"
    assert result.side == "long"
    assert result.entry_low == 0.093
    assert result.entry_high == 0.1
    assert result.stop_loss == 0.088


def test_eliz_limit_short_signal_with_direction_before_symbol():
    result = DeepseekClient()._mock_analyze("Limit short link 9.58 9.75 stop 10.13")

    assert result.intent == "open"
    assert result.should_act is True
    assert result.symbol == "LINK"
    assert result.side == "short"
    assert result.entry_low == 9.58
    assert result.entry_high == 9.75
    assert result.stop_loss == 10.13


def test_eliz_btc_k_shorthand_normalizes_entry_scale():
    result = DeepseekClient()._mock_analyze("Long BTC 91- 89 stop 86000")

    assert result.intent == "open"
    assert result.should_act is True
    assert result.symbol == "BTC"
    assert result.side == "long"
    assert result.entry_low == 89000
    assert result.entry_high == 91000
    assert result.stop_loss == 86000


def test_eliz_stopped_out_is_trade_update_not_open_signal():
    async def run():
        client = DeepseekClient()
        client.client = None
        return await client.analyze("ENA: Stopped out • Realized R/R: -1.00")

    result = asyncio.run(run())

    assert result.intent == "close"
    assert result.should_act is True
    assert result.symbol == "ENA"
    assert result.close_pct == 100


def test_mia_chinese_strategy_open_signal():
    text = """米娅BTC短线合约交易策略
做多
进场点位：62700附近
止损点位：61500
止盈点位：64600"""
    r = DeepseekClient()._mock_analyze(text)

    assert r.intent == "open"
    assert r.should_act is True
    assert r.symbol == "BTC"
    assert r.side == "long"
    assert r.entry_low == 62700
    assert r.entry_high == 62700
    assert r.stop_loss == 61500
    assert r.take_profit == 64600
    assert r.strategy_name == "比特币米娅"


def test_mia_stop_loss_and_close_updates_are_executable():
    client = DeepseekClient()
    context = "米娅BTC短线合约交易策略 做多 进场点位：62700附近 止损点位：61500"

    sl = client._mia_trade_action("BTC多单现在浮盈中，继续持有，止损位移至62700，做好成本保护！", context)
    close = client._mia_trade_action("BTC多单止盈出局吧，获利1000点，空仓等待下笔信号！", context)

    assert sl is not None
    assert sl.intent == "update_sl"
    assert sl.symbol == "BTC"
    assert sl.stop_loss == 62700
    assert close is not None
    assert close.intent == "close"
    assert close.should_act is True
    assert close.close_pct == 100


def test_mia_partial_take_profit_with_stop_move_is_not_full_close():
    client = DeepseekClient()
    context = "米娅BTC短线合约交易策略 做空 进场点位：77000附近 止损点位：78100"
    content = (
        "恭喜跟上BTC空单的朋友，BTC现价76400附近，获利600点，"
        "短线稳健可止盈出局，中长线止盈50%，剩余仓位止损位移至77000，做好成本保护！"
    )

    result = client._mia_trade_action(content, context)

    assert result is not None
    assert result.intent == "partial_close"
    assert result.should_act is True
    assert result.symbol == "BTC"
    assert result.close_pct == 50
    assert result.stop_loss == 77000


def test_mia_empty_position_close_stays_full_close():
    client = DeepseekClient()
    context = "米娅BTC短线合约交易策略 做多 进场点位：62700附近 止损点位：61500"

    result = client._mia_trade_action("BTC多单止盈出局吧，获利1000点，空仓等待下笔信号！", context)

    assert result is not None
    assert result.intent == "close"
    assert result.should_act is True
    assert result.close_pct == 100


def test_mia_take_profit_update_can_use_recent_context_symbol():
    result = DeepseekClient()._mia_trade_action(
        "止盈点位调整至63000，易触发！",
        "米娅BTC短线合约交易策略 做多 进场点位：62700附近",
    )

    assert result is not None
    assert result.intent == "update_tp"
    assert result.symbol == "BTC"
    assert result.take_profit == 63000


def test_stop_loss_adjustment_uses_quoted_position_context_symbol_before_noise_symbols():
    content = (
        "Stop loss adjusted to 1812 on the sweep Looking slight bullish so will just offer 0.2R "
        "Risk on it Still have BTC/CRV Longs active from above "
        "------------------------------- 止损调整至1812，扫损触发 走势略显看涨，所以仅投入0.2R的风险参与 "
        "此前布局的BTC/CRV做多持仓仍有效 Arthur 1970-01-01 08:00ETH SHORT 1.5% risk (已编辑)"
    )

    result = DeepseekClient()._mock_analyze(content)

    assert result.intent == "update_sl"
    assert result.should_act is True
    assert result.symbol == "ETH"
    assert result.stop_loss == 1812
    assert result.strategy_name is None


def test_manual_filled_signal_without_stop_gets_no_stop_marker():
    content = "Longed BTC 1% ------------------------------- 做多 BTC 1%"
    result = DeepseekClient()._normalize_result(
        content,
        IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="BTC",
            side="long",
            entry_note="market",
            stop_loss=None,
            summary="做多BTC 1%仓位",
        ),
    )

    assert result.symbol == "BTC"
    assert result.side == "long"
    assert "allow_no_stop_loss" in (result.entry_note or "")


def test_manual_filled_signal_without_stop_is_parsed_before_ai():
    async def run():
        client = DeepseekClient()
        client.client = object()
        return await client.analyze("Longed BTC 1% ------------------------------- 做多 BTC 1%")

    result = asyncio.run(run())

    assert result.intent == "open"
    assert result.should_act is True
    assert result.symbol == "BTC"
    assert result.side == "long"
    assert result.stop_loss is None
    assert "market" in (result.entry_note or "")
    assert "allow_no_stop_loss" in (result.entry_note or "")


def test_arthur_structured_swing_long_uses_real_symbol_not_swing():
    content = (
        "CRV Swing Long Risk: 3% Entry: 0.2144 TP: 0.4 SL: 0.1780 "
        "Position Closed: No Position Filled: No Reasoning: -Bids at weekly demand"
    )

    result = DeepseekClient()._mock_analyze(content)

    assert result.intent == "open"
    assert result.should_act is True
    assert result.symbol == "CRV"
    assert result.side == "long"
    assert result.entry_low == 0.2144
    assert result.take_profit == 0.4
    assert result.stop_loss == 0.178
    assert result.entry_note == "limit"


def test_arthur_position_closed_no_is_not_treated_as_close():
    content = (
        "CRV Swing Long Risk: 3% Entry: 0.2144 TP: 0.4 SL: 0.1780 "
        "Position Closed: No Position Filled: No Reasoning: -Bids at weekly demand"
    )

    result = DeepseekClient()._trade_action_update(content)

    assert result is None


def test_arthur_structured_positional_k_target_parses_k_notation():
    content = (
        "BTC Positional Long Risk: 3% Entry: 63680 TP: 97k SL: 56900 "
        "Position Closed: No Position Filled: Yes Reasoning: monthly retrace"
    )

    result = DeepseekClient()._mock_analyze(content)

    assert result.intent == "open"
    assert result.symbol == "BTC"
    assert result.entry_note == "market"
    assert result.take_profit == 97000


def test_timestamp_attached_symbol_normalizes_without_00_prefix():
    content = (
        "bids adjusted to weekly npoc sl adjusted "
        "Arthur 1970-01-01 08:00BTC Swing Long Risk: 1.5% Entry: 63590 "
        "TP: 69000 SL: 62200 Position Closed: No Position Filled: No"
    )

    result = DeepseekClient()._normalize_result(
        content,
        IntentResult(
            should_act=False,
            intent="noise",
            confidence=0.9,
            symbol="00BTC",
            summary="状态更新",
        ),
    )

    assert result.symbol == "BTC"


def test_chartprime_target_update_is_not_open_signal():
    text = (
        "LINK (Blofin) @Crypto Signal LONG: 5-10x,"
        "ENTRY: 7.75-7.88,"
        "EXIT: 7.91/7.95/7.99,"
        "SL: 7.65,"
        "THIS IS A SHORT-TERM TRADE\n"
        "#LINK 1st TARGET HIT at 7.91\n"
        "Duration: 20 mins\n"
        "@Crypto Signal"
    )
    r = DeepseekClient()._mock_analyze(text)

    assert r.should_act is False
    assert r.intent == "partial_close"
    assert "战报" in (r.reject_reason or "")


def test_chartprime_target_update_is_filtered_before_ai():
    text = (
        "#ICP (Blofin) @Crypto Signal SHORT: 5-10x,ENTRY: 2.41-2.34,"
        "EXIT: 2.33/2.12,SL: 2.45,THIS IS A SHORT-TERM TRADE "
        "#ICP 4th TARGET HIT at 2.26"
    )
    result = DeepseekClient()._result_update(text)

    assert result is not None
    assert result.should_act is False
    assert result.intent == "partial_close"


def test_candle_close_stop_uses_threshold_not_candle_count():
    text = (
        "NEAR spot limit: 1.926-1.885 "
        "SL: 2 H1 candle closures below 1.8 (4% risk)"
    )
    client = DeepseekClient()
    result = client._normalize_result(text, client._mock_analyze(text))

    assert result.symbol == "NEAR"
    assert result.side == "long"
    assert result.entry_low == 1.926
    assert result.entry_high == 1.885
    assert result.stop_loss == 1.8
    assert "2 H1 candle closures below 1.8" in (result.entry_note or "")
    assert "spot" in (result.entry_note or "")


def test_limit_order_filled_is_trade_update_not_open_signal():
    text = "ETH: Limit order filled ---------------- ETH：限价订单已成交"
    result = DeepseekClient()._result_update(text)

    assert result is not None
    assert result.intent == "hold"
    assert result.should_act is False
    assert result.symbol == "ETH"
    assert "不是新开仓" in (result.reject_reason or "")


def test_tareeq_longed_symbol_overrides_ai_misclassification():
    text = "@Tareeq粉 longed btw at 0.06947 sl: 0.0648 (0.5% risk)"
    client = DeepseekClient()
    wrong = client._mock_analyze(text)
    wrong.symbol = "WLD"

    result = client._normalize_result(text, wrong)

    assert result.symbol == "BTW"


def test_tareeq_tp_and_close_updates_are_executable_without_ai():
    client = DeepseekClient()

    tp = client._trade_action_update("BTW: TP1 (50%) - 50% remaining")
    closed = client._trade_action_update("VELVET: Closed in small profit (100%)")

    assert tp is not None
    assert tp.intent == "partial_close"
    assert tp.should_act is True
    assert tp.close_pct == 50
    assert closed is not None
    assert closed.intent == "close"
    assert closed.should_act is True
    assert closed.close_pct == 100


def test_tareeq_move_stop_to_be_uses_latest_entry_context():
    result = DeepseekClient()._trade_action_update(
        "ETH: Stops moved to BE",
        "@Tareeq粉 ETH limit long: 1642.3-1627 SL: 1596",
    )

    assert result is not None
    assert result.intent == "update_sl"
    assert result.should_act is True
    assert result.stop_loss == 1634.65


def test_tareeq_move_stop_to_be_without_context_uses_exchange_later():
    result = DeepseekClient()._trade_action_update("ETH: Stops moved to BE")

    assert result is not None
    assert result.intent == "update_sl"
    assert result.should_act is True
    assert result.stop_loss is None
    assert result.entry_note == "break_even"


def test_tareeq_natural_tp_and_break_even_phrases():
    client = DeepseekClient()
    tp = client._trade_action_update("ETH: TP1 reached, close 50%")
    be = client._trade_action_update("ETH: move stop to entry")

    assert tp is not None
    assert tp.intent == "partial_close"
    assert tp.close_pct == 50
    assert be is not None
    assert be.intent == "update_sl"
    assert be.entry_note == "break_even"


def test_single_letter_symbol_trade_updates_are_supported():
    result = DeepseekClient()._trade_action_update("H: Stops moved to 0.242")

    assert result is not None
    assert result.symbol == "H"
    assert result.intent == "update_sl"
    assert result.stop_loss == 0.242


def test_chartprime_target_hit_is_executable_and_tracks_level():
    result = DeepseekClient()._chartprime_target_action("#LINK 2nd TARGET HIT at 7.95")

    assert result is not None
    assert result.should_act is True
    assert result.intent == "partial_close"
    assert result.symbol == "LINK"
    assert result.entry_note == "target_level:2"
