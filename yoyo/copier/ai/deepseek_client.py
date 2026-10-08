from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from openai import AsyncOpenAI

from yoyo.copier.ai.prefilter import obviously_not_a_signal
from yoyo.copier.ai.prompts import SYSTEM_PROMPT, build_user_message
from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.ai.text import split_own_text
from yoyo.copier.config import app_config, env


class DeepseekClient:
    def __init__(self) -> None:
        self.client = (
            # No timeout used to mean the client default (10 minutes): one stuck call
            # held the ingest request and every message queued behind it.
            AsyncOpenAI(api_key=env.deepseek_api_key, base_url=app_config.deepseek.base_url,
                        timeout=20.0, max_retries=1)
            if env.deepseek_api_key
            else None
        )

    async def analyze(
        self,
        content: str,
        author: str = "",
        image_text: str | None = None,
        context: str | None = None,
    ) -> IntentResult:
        # Intent comes from the author's own words; translations are dropped and
        # reply/forward previews only inform the context (yoyo/copier/ai/text.py).
        content, quoted = split_own_text(content)
        content = _clean_signal_text(content)
        context = _clean_signal_text("\n".join(
            part for part in (f"被回复或转发的消息: {quoted}" if quoted else "", context or "") if part))
        target_action = self._chartprime_target_action(content)
        if target_action:
            return target_action
        structured_open = _arthur_structured_open(content)
        if structured_open:
            return self._normalize_result(content, structured_open)
        mia_action = self._mia_trade_action(content, context)
        if mia_action:
            return self._normalize_result(content, mia_action)
        trade_action = self._trade_action_update(content, context)
        if trade_action:
            return self._normalize_result(content, trade_action)
        filled_with_levels = _filled_open_with_levels(content)
        if filled_with_levels:
            return self._normalize_result(content, filled_with_levels)
        manual_filled_open = _manual_filled_open(content)
        if manual_filled_open:
            return self._normalize_result(content, manual_filled_open)
        result_update = self._result_update(content)
        if result_update:
            return result_update
        shorthand = _woods_shorthand_open(" ".join((content or "").replace("：", ":").split()))
        if shorthand:
            return self._normalize_result(content, shorthand)
        if not image_text and obviously_not_a_signal(content):
            return IntentResult(intent="noise", confidence=1.0, summary="本地预过滤：无币种、数字或交易用语，未调用 AI")
        if not self.client:
            return self._normalize_result(content, self._mock_analyze(content, context))
        try:
            response = await self.client.chat.completions.create(
                model=app_config.deepseek.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_message(content, author, image_text, context)},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = response.choices[0].message.content or "{}"
            return self._normalize_result(content, IntentResult.model_validate(json.loads(raw)))
        except Exception:
            return self._normalize_result(content, self._mock_analyze(content, context))

    def _chartprime_target_action(self, content: str) -> IntentResult | None:
        text = " ".join((content or "").replace("：", ":").split())
        lower = text.lower()
        symbol_match = re.search(r"#([A-Z0-9]{1,15})", text, re.I)
        if not symbol_match or "target" not in lower:
            return None
        symbol = symbol_match.group(1).upper()
        if re.search(r"all\s+targets\s+hit|trade\s+closed|targets\s+hit\s*:\s*6", lower):
            return IntentResult(
                should_act=True,
                intent="close",
                confidence=0.99,
                symbol=symbol,
                close_pct=100,
                entry_note="target_level:6",
                summary=f"{symbol} 全部 TP 命中，全部平仓",
            )
        levels = [
            value
            for word, value in {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6}.items()
            if re.search(rf"\b{word}\s+target\s+hit\b", lower)
        ]
        numeric = re.search(r"targets?\s+hit\s*:\s*(\d+)", lower)
        if numeric:
            levels.append(min(int(numeric.group(1)), 6))
        if not levels:
            return None
        level = max(levels)
        return IntentResult(
            should_act=True,
            intent="partial_close" if level < 6 else "close",
            confidence=0.99,
            symbol=symbol,
            close_pct=100 / max(1, 7 - level) if level < 6 else 100,
            entry_note=f"target_level:{level}",
            summary=f"{symbol} TP{level} 命中",
        )

    def _trade_action_update(self, content: str, context: str | None = None) -> IntentResult | None:
        text = " ".join((content or "").replace("：", ":").split())
        lower = text.lower()
        if re.search(r"\bposition\s+closed\s*:\s*no\b", lower):
            return None
        symbol = _leading_update_symbol(text)
        if not symbol:
            return None

        tp = re.search(r"\btp\d*\s*\(\s*(\d{1,3}(?:\.\d+)?)%\s*\)", lower)
        explicit_close_pct = re.search(
            r"(?:\bclose|\bclosed|\btrim|\btake\s+profit|止盈|平仓)\D{0,12}"
            r"(\d{1,3}(?:\.\d+)?)\s*%",
            lower,
        )
        closed_pct = re.search(r"\bclosed\b.*?\(\s*(\d{1,3}(?:\.\d+)?)%\s*\)", lower)
        if tp or explicit_close_pct or closed_pct:
            pct = float((tp or explicit_close_pct or closed_pct).group(1))
            intent = "close" if pct >= 99 else "partial_close"
            move_to_be = _requests_break_even(lower)
            # Trade-tracker reports ("TP1 (25%) - 75% remaining", then "Closed in
            # profits (75%)") count percent of the original size, not of what is left.
            notes = ["pct_of_original"] if (tp or closed_pct) and intent == "partial_close" else []
            if move_to_be:
                notes.append("break_even")
            return IntentResult(
                should_act=True,
                intent=intent,
                confidence=0.99,
                symbol=symbol,
                close_pct=pct,
                entry_note="; ".join(notes) or None,
                summary=f"{symbol} {'全部平仓' if intent == 'close' else f'部分平仓 {pct:g}%'}",
            )

        if re.search(r"\bstopped\s+(?:out|be)\b|\bclosed\b", lower):
            return IntentResult(
                should_act=True,
                intent="close",
                confidence=0.99,
                symbol=symbol,
                close_pct=100,
                summary=f"{symbol} 全部平仓",
            )

        if re.search(r"\blimit\s+order\s+(?:cancell?ed|removed)\b", lower):
            return IntentResult(
                should_act=True,
                intent="cancel",
                confidence=0.99,
                symbol=symbol,
                summary=f"{symbol} 限价单已撤销，撤掉对应挂单",
            )
        candle = re.search(
            r"\bstops?\s+moved\s+to\s+(?:(\d{1,2})\s*([mhd])|([mhd])\s*(\d{1,2}))\s*(?:candle\s*)?(?:close\s*)?"
            r"(<|>|below|above|under|over)\s*\$?([0-9]*\.?[0-9]+)", lower)
        moved = re.search(r"\bstops?\s+moved\s+to\s+\$?([0-9]*\.?[0-9]+)(?![0-9a-z.])", lower)
        if candle:
            count = candle.group(1) or candle.group(4)
            unit = (candle.group(2) or candle.group(3)).upper()
            direction = "below" if candle.group(5) in {"<", "below", "under"} else "above"
            stop_loss = float(candle.group(6))
            entry_note = f"{count}{unit} candle close {direction} {stop_loss:g}"
        elif moved:
            stop_loss = float(moved.group(1))
            entry_note = None
        elif _requests_break_even(lower):
            stop_loss = _latest_entry_for_symbol(context or "", symbol)
            entry_note = "break_even"
        else:
            return None
        return IntentResult(
            should_act=True,
            intent="update_sl",
            confidence=0.99,
            symbol=symbol,
            entry_note=entry_note,
            stop_loss=stop_loss,
            summary=f"{symbol} 止损移至 {'持仓均价' if stop_loss is None else f'{stop_loss:g}'}",
        )

    def _mia_trade_action(self, content: str, context: str | None = None) -> IntentResult | None:
        text = " ".join((content or "").replace("：", ":").split())
        lower_context = " ".join((context or "").replace("：", ":").split())
        strategy_name = "比特币米娅" if "米娅" in f"{text} {lower_context}" else None
        symbol = (
            _position_context_symbol(text)
            or _position_context_symbol(lower_context)
            or _symbol(text)
            or _symbol(lower_context)
        )
        if not symbol:
            return None

        partial_pct = _partial_take_profit_pct(text)
        if partial_pct is not None:
            stop_loss = _mia_stop_move(text)
            return IntentResult(
                should_act=True,
                intent="partial_close",
                confidence=0.98,
                symbol=symbol,
                close_pct=partial_pct,
                stop_loss=stop_loss,
                strategy_name=strategy_name,
                summary=f"{symbol} 部分止盈 {partial_pct:g}%",
            )

        tp_move = re.search(rf"止盈(?:点位|位)?(?:调整至|移至|移动至|改到|移动到)\s*({PRICE_RE})", text, re.I)
        if tp_move:
            take_profit = _price(tp_move.group(1))
            return IntentResult(
                should_act=True,
                intent="update_tp",
                confidence=0.96,
                symbol=symbol,
                take_profit=take_profit,
                strategy_name=strategy_name,
                summary=f"{symbol} 止盈调整至 {take_profit:g}",
            )

        sl_move = re.search(rf"止损(?:点位|位)?(?:调整至|移至|移动至|改到|移动到)\s*({PRICE_RE})", text, re.I)
        if sl_move:
            stop_loss = _price(sl_move.group(1))
            return IntentResult(
                should_act=True,
                intent="update_sl",
                confidence=0.96,
                symbol=symbol,
                stop_loss=stop_loss,
                strategy_name=strategy_name,
                summary=f"{symbol} 止损移至 {stop_loss:g}",
            )

        if re.search(r"全部仓位止盈出局|剩余仓位.*止盈出局|再次恭喜.*止盈出局|止盈出局吧|空仓等待|全部止盈|全部.*出局", text):
            return IntentResult(
                should_act=True,
                intent="close",
                confidence=0.98,
                symbol=symbol,
                close_pct=100,
                strategy_name=strategy_name,
                summary=f"{symbol} 全部平仓",
            )

        return None

    def _normalize_result(self, content: str, result: IntentResult) -> IntentResult:
        text = " ".join((content or "").replace("：", ":").split())
        explicit_symbol = _position_context_symbol(text) or _symbol(text)
        if explicit_symbol:
            result.symbol = explicit_symbol
        manual_fill = _manual_fill_without_levels(text)
        if (
            manual_fill
            and result.intent == "open"
            and result.should_act
            and not result.stop_loss
            and (not result.symbol or result.symbol == manual_fill[0])
        ):
            result.symbol = manual_fill[0]
            result.side = result.side or manual_fill[1]
            notes = str(result.entry_note or "")
            if "market" not in notes.lower():
                notes = f"{notes}; market" if notes else "market"
            if "allow_no_stop_loss" not in notes.lower():
                notes = f"{notes}; allow_no_stop_loss" if notes else "allow_no_stop_loss"
            result.entry_note = notes
            result.summary = result.summary or f"{result.symbol} {result.side} 市价跟进（无止损信号）"
        candle_stop = _candle_close_stop(text)
        if candle_stop:
            count, timeframe, direction, price = candle_stop
            note = f"{count} {timeframe} candle closures {direction} {price:g}"
            result.stop_loss = price
            result.entry_note = f"{result.entry_note}; {note}" if result.entry_note else note
            result.summary = f"{result.summary}；条件止损：{note}" if result.summary else f"条件止损：{note}"
        if re.search(r"\bspot\b|现货", text, re.I) and "spot" not in str(result.entry_note or "").lower():
            result.entry_note = f"{result.entry_note}; spot" if result.entry_note else "spot"
        return result

    def _result_update(self, content: str) -> IntentResult | None:
        text = " ".join((content or "").replace("：", ":").split())
        lower = text.lower()
        if re.search(
            r"\b(?:limit\s+)?order\s+(?:has\s+been\s+)?filled\b|"
            r"\blimit\s+(?:buy|sell)\s+filled\b|"
            r"限价(?:订单)?已成交|订单已成交|挂单已成交",
            lower,
        ):
            return IntentResult(
                should_act=False,
                intent="hold",
                confidence=0.99,
                reject_reason="已有挂单成交回报，不是新开仓指令",
                symbol=_symbol(text),
                summary="限价订单已成交",
            )
        if not re.search(
            r"target\s+hit|targets\s+hit|all\s+targets\s+hit|trade\s+closed|"
            r"sl\s+hit|stop\s+loss\s+(?:hit|triggered)|目标命中|止盈.*触发|止损.*(?:触发|平仓)",
            lower,
        ):
            return None
        if re.search(
            r"\bstop\s+loss\s+adjusted\b|\bstops?\s+moved\b|止损.*(?:调整|移至|移动至|移动到|改到)",
            text,
            re.I,
        ):
            return None
        intent = (
            "close"
            if re.search(r"all\s+targets\s+hit|trade\s+closed|sl\s+hit|stop\s+loss|止损.*(?:触发|平仓)", lower)
            else "partial_close"
        )
        return IntentResult(
            should_act=False,
            intent=intent,
            confidence=0.99,
            reject_reason="已有交易战报，不是新开仓",
            symbol=_symbol(text),
            summary="交易结果更新",
        )

    def _mock_analyze(self, content: str, context: str | None = None) -> IntentResult:
        content = _clean_signal_text(content)
        context = _clean_signal_text(context or "")
        text = " ".join((content or "").replace("：", ":").split())
        lower = text.lower()
        context_text = " ".join((context or "").replace("：", ":").split())

        result_update = self._result_update(content)
        if result_update:
            return result_update
        structured_open = _arthur_structured_open(content)
        if structured_open:
            return structured_open
        mia_action = self._mia_trade_action(content, context)
        if mia_action:
            return mia_action
        shorthand = _woods_shorthand_open(text)
        if shorthand:
            return shorthand

        symbol = (
            _position_context_symbol(text)
            or _position_context_symbol(context_text)
            or _symbol(text)
            or _symbol(context_text)
        )
        side = _side(text) or _side(context_text)
        entry_low, entry_high = _range_after(text, ("进场点位", "入场", "entry", "spot limit", "现货限价"))
        if entry_low is None and entry_high is None:
            entry_low, entry_high = _range_after(
                context_text, ("进场点位", "入场", "entry", "spot limit", "现货限价")
            )
        stop_loss = _number_after(text, ("止损点位", "止损", "sl", "stop loss", "成本价"))
        if stop_loss is None:
            stop_loss = _number_after(context_text, ("止损点位", "止损", "sl", "stop loss"))
        take_profit = _first_target(text) or _first_target(context_text)
        leverage = _leverage(text)

        close_match = re.search(r"(?:止盈|平仓)\s*(\d{1,3})\s*%", text)
        if close_match:
            return IntentResult(
                should_act=True,
                intent="partial_close",
                confidence=0.98,
                symbol=symbol,
                close_pct=float(close_match.group(1)),
                stop_loss=stop_loss if re.search(r"止损.*(?:成本|保本)|移动到成本", text) else None,
                summary="部分止盈并更新剩余仓位",
            )
        if re.search(r"全部止盈|止盈出局|出局吧|全部.*出局", text):
            return IntentResult(
                should_act=True,
                intent="close",
                confidence=0.98,
                symbol=symbol,
                close_pct=100,
                summary="全部平仓",
            )
        if re.search(r"取消|撤掉|撤销", text):
            return IntentResult(
                should_act=True,
                intent="cancel",
                confidence=0.95,
                symbol=symbol,
                summary="取消最近挂单",
            )
        if re.search(r"止损.*(?:移动|改|调整).*(?:成本|保本)|(?:成本|保本)价", text) and stop_loss:
            return IntentResult(
                should_act=True,
                intent="update_sl",
                confidence=0.95,
                symbol=symbol,
                stop_loss=stop_loss,
                summary="移动止损",
            )
        if re.search(r"直接进|市价进|现价进", text) and symbol and side and stop_loss:
            return IntentResult(
                should_act=True,
                intent="open",
                confidence=0.95,
                symbol=symbol,
                side=side,
                entry_note="market",
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy_name="飞扬合约策略",
                summary=f"{symbol} {side} 市价进场",
            )

        is_open = bool(symbol and side and (entry_low is not None or entry_high is not None) and stop_loss)
        if not is_open:
            return IntentResult(
                should_act=False,
                intent="noise",
                confidence=0.85,
                reject_reason="未识别到完整开仓指令",
                summary=text[:160],
            )
        is_chartprime_signal = "@crypto signal" in lower
        strategy = "ChartPrime Crypto Signal" if is_chartprime_signal else "KOL Signal"
        if "飞扬合约策略" in text:
            strategy = "飞扬合约策略"
        if "米娅" in text:
            strategy = "比特币米娅"
        entry_note = None
        if re.search(r"\bspot\b|现货", text, re.I):
            entry_note = "spot"
        elif is_chartprime_signal and not re.search(r"\blimit\b|限价", text, re.I):
            entry_note = "market"
        return IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol=symbol,
            side=side,
            entry_low=entry_low,
            entry_high=entry_high,
            entry_note=entry_note,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage_hint=leverage,
            strategy_name=strategy,
            summary=f"{symbol} {side} signal",
        )


def _symbol(text: str) -> str | None:
    text = re.sub(r"\d{1,2}:\d{2}(?=[A-Z])", " ", text or "")
    patterns = [
        r"\b([A-Z][A-Z0-9]{1,14})\s+(?:(?:positional|swing|compound|intraday)\s+)?(?:long|short)\s+risk\b",
        r"米娅\s*#?([A-Z0-9]{2,15})\s*短线",
        r"\b([A-Z0-9]{2,15})(?:多单|空单)",
        r"\b(?:longed|shorted|bought|sold)\s+#?([A-Z0-9]{1,15})\s+at\b",
        r"\b(?:long|short|buy|sell)\s+#?([A-Z0-9]{1,15})\s+(?:at|@)\b",
        r"(?:具体产品|品种|symbol)\s*[:：]\s*#?([A-Z0-9]{2,15})",
        r"\b([A-Z0-9]{2,15})\s*[:：]\s*(?:limit|限价|order|订单)",
        r"^\s*#?([A-Z0-9]{2,15})\s*\(",
        r"\b#([A-Z0-9]{2,15})\b",
        r"\b([A-Z0-9]{2,15})(?:USDT\.P|/USDT|USDT)\b",
        r"\b([A-Z0-9]{2,15})\s+spot\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            token = match.group(1).upper()
            if token not in {"LONG", "SHORT", "ENTRY", "EXIT", "TARGET", "TARGETS"}:
                return token
    if re.search(r"#?比特币|大饼|BTC", text, re.I):
        return "BTC"
    return None


def _position_context_symbol(text: str) -> str | None:
    cleaned = " ".join((text or "").replace("：", ":").split())
    cleaned = re.sub(r"\d{1,2}:\d{2}(?=[A-Z])", " ", cleaned)
    matches: list[tuple[int, str]] = []
    patterns = [
        r"\b([A-Z][A-Z0-9]{1,14})\s+(?:LONG|SHORT|Long|Short)\b",
        r"\b(?:LONG|SHORT|Long|Short)\s+([A-Z][A-Z0-9]{1,14})\b",
    ]
    blocked = {
        "LONG",
        "SHORT",
        "ENTRY",
        "EXIT",
        "TARGET",
        "TARGETS",
        "STOP",
        "LOSS",
        "RISK",
        "SIGNAL",
        "CRYPTO",
        "THIS",
        "SWING",
        "POSITIONAL",
        "COMPOUND",
        "INTRADAY",
        "RUNNING",
        "ACTIVE",
        "BID",
    }
    for pattern in patterns:
        for match in re.finditer(pattern, cleaned):
            token = match.group(1).upper()
            if token not in blocked:
                matches.append((match.start(), token))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _arthur_structured_open(text: str) -> IntentResult | None:
    cleaned = " ".join(_clean_signal_text(text).split())
    price_value = rf"(?:{PRICE_RE})(?:\s*k)?"
    pattern = (
        rf"^\s*\b([A-Z][A-Z0-9]{{1,14}})\s+"
        rf"(?:(?:positional|swing|compound|intraday)\s+)?"
        rf"(long|short)(?:\s+\w+)?\s+risk\s*:\s*(\d+(?:\.\d+)?)%\s+"
        rf"entry\s*:\s*({price_value})\s+"
        rf"tp\s*:\s*({price_value})\s+"
        rf"sl\s*:\s*({price_value})\s+"
        rf".*?\bposition\s+closed\s*:\s*(yes|no)\b"
        rf".*?\bposition\s+filled\s*:\s*(yes|no)\b"
    )
    match = re.search(pattern, cleaned, re.I)
    if not match:
        return None
    symbol = match.group(1).upper()
    if symbol in {"LONG", "SHORT", "SWING", "POSITIONAL", "COMPOUND", "INTRADAY"}:
        return None
    closed = match.group(7).lower() == "yes"
    if closed:
        return None
    filled = match.group(8).lower() == "yes"
    side = match.group(2).lower()
    entry = _price_with_k(match.group(4))
    tp = _price_with_k(match.group(5))
    sl = _price_with_k(match.group(6))
    return IntentResult(
        should_act=True,
        intent="open",
        confidence=0.97,
        symbol=symbol,
        side=side,
        entry_low=entry,
        entry_high=entry,
        entry_note="market" if filled else "limit",
        stop_loss=sl,
        take_profit=tp,
        summary=f"{symbol} {side} Arthur structured signal",
    )


def _manual_fill_without_levels(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(longed|shorted)\s+#?([A-Z][A-Z0-9]{1,14})\b"
        r"(?!\s+(?:at|@|entry|limit)\b)"
        r"(?:\s+\d+(?:\.\d+)?%\s*(?:risk)?)?"
        rf"(?:\s+{PRICE_RE}\s+(?:sl|stop))?"
        r"(?=\s|$|[-–—])",
        text,
        re.I,
    )
    if not match:
        return None
    symbol = match.group(2).upper()
    if symbol in {"LONG", "SHORT", "ENTRY", "EXIT", "TARGET", "TARGETS", "RISK"}:
        return None
    side = "long" if match.group(1).lower() == "longed" else "short"
    return symbol, side


def _filled_open_with_levels(text: str) -> IntentResult | None:
    """"Longed MET at 0.455 sl; 0.4375 (0.5% risk)" / "Longed BTC 83400 sl 81792".

    Past tense means the author is already in at that price, so the follow is a market
    entry with the author's stop; the quoted price stays as the reference entry.
    """
    cleaned = " ".join(str(text or "").split())
    match = re.search(
        r"\b(longed|shorted)\s+#?([A-Z][A-Z0-9]{0,14})\s+(?:(?:at|@)\s+)?\$?(" + PRICE_RE + r")"
        r"\s*[,;]?\s*(?:sl|stop(?:\s*loss)?)[a-z]?\s*[:;]?\s*\$?(" + PRICE_RE + r")",
        cleaned,
        re.I,
    )
    if not match:
        return None
    symbol = match.group(2).upper()
    if symbol in {"LONG", "SHORT", "ENTRY", "EXIT", "TARGET", "TARGETS", "RISK", "AT"}:
        return None
    side = "long" if match.group(1).lower() == "longed" else "short"
    entry, stop = _price(match.group(3)), _price(match.group(4))
    if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
        return None  # levels that contradict the side go to the model instead of guessing
    return IntentResult(
        should_act=True,
        intent="open",
        confidence=0.97,
        symbol=symbol,
        side=side,
        entry_low=entry,
        entry_high=entry,
        entry_note="market; author_filled",
        stop_loss=stop,
        summary=f"{symbol} {'做多' if side == 'long' else '做空'}已在 {entry:g} 成交，止损 {stop:g}，市价跟进",
    )


def _manual_filled_open(text: str) -> IntentResult | None:
    match = re.search(
        r"\b(longed|shorted)\s+#?([A-Z][A-Z0-9]{1,14})\b"
        r"(?!\s+(?:at|@|entry|limit)\b)"
        r"(?:\s+(\d+(?:\.\d+)?)%\s*(?:risk)?)?"
        rf"(?:\s+({PRICE_RE})\s+(?:sl|stop))?"
        r"(?=\s|$|[-–—])",
        text,
        re.I,
    )
    if not match:
        return None
    symbol = match.group(2).upper()
    if symbol in {"LONG", "SHORT", "ENTRY", "EXIT", "TARGET", "TARGETS", "RISK"}:
        return None
    side = "long" if match.group(1).lower() == "longed" else "short"
    stop_loss = _price(match.group(4)) if match.group(4) else None
    return IntentResult(
        should_act=True,
        intent="open",
        confidence=0.96,
        symbol=symbol,
        side=side,
        entry_note="market",
        stop_loss=stop_loss,
        summary=f"{symbol} {side} 已开仓，市价跟进",
    )


PRICE_RE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+"


def _price(value: str) -> float:
    normalized = value.replace(",", "")
    if normalized.startswith("."):
        normalized = f"0{normalized}"
    return float(normalized)


def _price_with_k(value: str) -> float:
    raw = value.strip().lower().replace(" ", "")
    if raw.endswith("k"):
        return _clean_price_float(_price(raw[:-1]) * 1000)
    return _price(raw)


def _woods_shorthand_open(text: str) -> IntentResult | None:
    text = _clean_signal_text(text)
    patterns = [
        rf"\b#?([A-Z][A-Z0-9]{{1,15}})\b\s+"
        rf"(?:spot\s+|limit\s+)?"
        rf"(?:(long|short)\s+)?"
        rf"(?:at\s+)?"
        rf"({PRICE_RE})"
        rf"(?:(?:\s*[-–—/]\s*|\s+)({PRICE_RE}))?"
        rf"\s*(?:stop|sl)\s*:?\s*({PRICE_RE})\b",
        rf"\b(?:limit\s+|spot\s+)?(long|short)\s+"
        rf"#?([A-Z][A-Z0-9]{{1,15}})\b\s+"
        rf"(?:at\s+)?"
        rf"({PRICE_RE})"
        rf"(?:(?:\s*[-–—/]\s*|\s+)({PRICE_RE}))?"
        rf"\s*(?:stop|sl)\s*:?\s*({PRICE_RE})\b",
    ]
    match = None
    direction_first = False
    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.I)
        if match:
            direction_first = idx == 1
            break
    if not match:
        return None

    if direction_first:
        side = match.group(1).lower()
        symbol = match.group(2).upper()
        first_entry = _price(match.group(3))
        second_entry = _price(match.group(4)) if match.group(4) else first_entry
        stop_loss = _price(match.group(5))
    else:
        symbol = match.group(1).upper()
        side = match.group(2).lower() if match.group(2) else None
        first_entry = _price(match.group(3))
        second_entry = _price(match.group(4)) if match.group(4) else first_entry
        stop_loss = _price(match.group(5))
    if symbol in {"LIMIT", "SPOT", "LONG", "SHORT", "STOP"}:
        return None
    first_entry, second_entry, stop_loss = _normalize_entry_scale(symbol, first_entry, second_entry, stop_loss)
    entry_low = min(first_entry, second_entry)
    entry_high = max(first_entry, second_entry)
    avg_entry = (entry_low + entry_high) / 2
    if side is None:
        if stop_loss < avg_entry:
            side = "long"
        elif stop_loss > avg_entry:
            side = "short"
    if side is None:
        return None

    return IntentResult(
        should_act=True,
        intent="open",
        confidence=0.95,
        symbol=symbol,
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        entry_note="spot" if re.search(r"\bspot\b|现货", text, re.I) else None,
        stop_loss=stop_loss,
        strategy_name="Woods Signal",
        summary=f"{symbol} {side} signal",
    )


def _clean_signal_text(text: str) -> str:
    cleaned = "".join(ch for ch in str(text or "") if unicodedata.category(ch) != "Cf")
    return cleaned.replace("：", ":").replace("＜", "<").replace("＞", ">")


def _normalize_entry_scale(symbol: str, first_entry: float, second_entry: float, stop_loss: float) -> tuple[float, float, float]:
    if symbol == "BTC" and max(first_entry, second_entry) < 1000 <= stop_loss:
        return _clean_price_float(first_entry * 1000), _clean_price_float(second_entry * 1000), stop_loss
    if symbol == "BTC" and 10 <= min(first_entry, second_entry, stop_loss) and max(first_entry, second_entry, stop_loss) < 1000:
        return _clean_price_float(first_entry * 1000), _clean_price_float(second_entry * 1000), _clean_price_float(stop_loss * 1000)
    if symbol == "DOGE" and 0.5 <= min(first_entry, second_entry, stop_loss) and max(first_entry, second_entry, stop_loss) < 1:
        return _clean_price_float(first_entry / 10), _clean_price_float(second_entry / 10), _clean_price_float(stop_loss / 10)
    return first_entry, second_entry, stop_loss


def _clean_price_float(value: float) -> float:
    return float(f"{value:.12g}")


def _leading_update_symbol(text: str) -> str | None:
    # A ticker starts with a letter (single-letter S/H exist); "3:1" or "08:00" is not one.
    match = re.search(r"(?:^|\s)((?:1000)?[A-Z][A-Z0-9]{0,14})\s*:", text, re.I)
    if not match:
        return None
    token = match.group(1).upper()
    return token if token not in {"SL", "TP", "LONG", "SHORT", "ENTRY", "EXIT"} else None


def _requests_break_even(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:stops?\s+)?(?:moved?|move)\s+to\s+(?:be|break\s*even|breakeven|entry)\b|"
            r"\b(?:moved?|move)\s+(?:the\s+)?(?:stops?|sl)\s+to\s+(?:be|break\s*even|breakeven|entry(?:\s+price)?)\b|"
            r"止损.*(?:成本|保本|开仓)|移动.*(?:成本|保本|开仓)",
            text,
            re.I,
        )
    )


def _mia_stop_move(text: str) -> float | None:
    moved = re.search(
        rf"止损(?:点位|位)?(?:调整至|移至|移动至|改到|移动到|重设为)\s*({PRICE_RE})",
        text,
        re.I,
    )
    if moved:
        return _price(moved.group(1))
    changed = re.search(rf"修改至\s*({PRICE_RE})", text, re.I)
    return _price(changed.group(1)) if changed else None


def _partial_take_profit_pct(text: str) -> float | None:
    pct_values = [
        float(value)
        for value in re.findall(r"(?:止盈|出局)\D{0,8}(\d{1,3}(?:\.\d+)?)\s*%", text)
    ]
    if pct_values:
        return min(max(max(pct_values), 1), 99)
    if re.search(r"短线稳健.*(?:止盈|出局).*中长线", text):
        return 50.0
    return None


def _latest_entry_for_symbol(context: str, symbol: str) -> float | None:
    text = " ".join((context or "").replace("：", ":").split())
    escaped = re.escape(symbol)
    patterns = [
        rf"\b{escaped}\s*:\s*updated\s+average\s+entry\s+to\s+([0-9]+(?:\.[0-9]+)?)",
        rf"\b(?:longed|shorted)\s+{escaped}\s+at\s+([0-9]+(?:\.[0-9]+)?)",
        rf"\b{escaped}\s+(?:limit\s+)?(?:long|short)\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*[-–—]\s*([0-9]+(?:\.[0-9]+)?)",
    ]
    matches: list[tuple[int, float]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            values = [float(value) for value in match.groups() if value is not None]
            matches.append((match.start(), sum(values) / len(values)))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _side(text: str) -> str | None:
    explicit = re.search(r"\b(LONG|SHORT)\s*:", text, re.I)
    if explicit:
        return explicit.group(1).lower()
    if re.search(r"做空|空单|\bshort\b|\bsell\b", text, re.I):
        return "short"
    if re.search(r"做多|多单|\blong\b|\bbuy\b", text, re.I):
        return "long"
    if re.search(r"\bspot\s+limit\b|现货限价", text, re.I):
        return "long"
    return None


def _candle_close_stop(text: str) -> tuple[int, str, str, float] | None:
    english = re.search(
        r"(?:sl|stop\s*loss)\s*:\s*(\d+)\s*([mhdw]\d*|\d+[mhdw])\s+"
        r"candle\s+closures?\s+(below|above)\s+([0-9]+(?:\.[0-9]+)?)",
        text,
        re.I,
    )
    if english:
        return int(english.group(1)), english.group(2).upper(), english.group(3).lower(), float(english.group(4))

    chinese = re.search(
        r"(?:止损|sl)\s*(?:\([^)]*\))?\s*:\s*(\d+)\s*[，,]?\s*"
        r"([mhdw]\d*|\d+[mhdw])\s*蜡烛线?\s*收盘\s*(低于|高于)\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.I,
    )
    if chinese:
        direction = "below" if chinese.group(3) == "低于" else "above"
        return int(chinese.group(1)), chinese.group(2).upper(), direction, float(chinese.group(4))
    return None


def _range_after(text: str, labels: tuple[str, ...]) -> tuple[float | None, float | None]:
    joined = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{joined})\s*(?:long|short|buy|sell)?\s*[:：-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:-|–|—|to|~)\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.I,
    )
    if match:
        return float(match.group(1)), float(match.group(2))
    match = re.search(rf"(?:{joined})\s*[:：-]?\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    if match:
        value = float(match.group(1))
        return value, value
    return None, None


def _number_after(text: str, labels: tuple[str, ...]) -> float | None:
    joined = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{joined})\s*[:：-]?\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    return float(match.group(1)) if match else None


def _first_target(text: str) -> float | None:
    joined = "|".join(re.escape(label) for label in ("止盈点位", "止盈", "exit", "tp", "take profit"))
    match = re.search(rf"(?:{joined})\s*[:：-]?\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    return float(match.group(1)) if match else None


def _leverage(text: str) -> int | None:
    match = re.search(r"\b(\d+)\s*(?:-|to|~)\s*(\d+)\s*x\b|\b(\d+)\s*x\b", text, re.I)
    if not match:
        return None
    return int(match.group(1) or match.group(3))
