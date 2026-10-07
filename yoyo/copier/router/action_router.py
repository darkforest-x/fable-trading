from __future__ import annotations

import json
import logging

from yoyo.copier.ai.deepseek_client import DeepseekClient
from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.exchange import (
    create_exchange_client,
    create_exchange_executor,
    exchange_configured,
    exchange_label,
)
from yoyo.copier.monitor_switches import feature_enabled
from yoyo.copier.notifications.telegram import (
    notify_signal_detected,
    notify_trade_pipeline,
    notify_trade_update,
)
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)
BLOCKED_TRADE_CHANNELS_KEY = "blocked_trade_channels"


class ActionRouter:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.ai = DeepseekClient()
        self.risk = RiskEngine(db)

    async def process_message(
        self,
        message_id: int,
        content: str,
        author: str = "",
        *,
        allow_execute: bool = True,
    ) -> IntentResult:
        if not feature_enabled(self.db, "ai_parse"):
            result = IntentResult(
                should_act=False,
                intent="noise",
                confidence=0,
                reject_reason="AI 解析开关已关闭",
                summary="未解析",
            )
            self.db.update_message_status(message_id, "skipped")
            return result

        message = self.db.get_message(message_id) or {}
        channel_id = str(message.get("channel_id") or "")
        result = await self.ai.analyze(
            content,
            author,
            context=self._recent_channel_context(message_id, channel_id),
        )
        signal_id = self.db.insert_signal(
            message_id=message_id,
            intent=result.intent,
            should_act=result.should_act,
            confidence=result.confidence,
            ai_json=result.model_dump_json(),
            reject_reason=result.reject_reason,
        )
        self.db.audit(
            "ai_parsed",
            result.summary or result.intent,
            message_id=message_id,
            payload=result.model_dump(),
        )

        if _channel_trade_blocked(self.db, channel_id) and _is_trade_action(result):
            self.db.update_message_status(message_id, "skipped")
            self.db.audit(
                "trade_channel_blocked",
                f"{channel_id} trade disabled",
                message_id=message_id,
                payload={"channel_id": channel_id, "intent": result.intent, "symbol": result.symbol},
            )
            await notify_trade_pipeline(
                self.db,
                title="博主已拉黑（未执行）",
                message_id=message_id,
                author=author,
                content=content,
                result=result,
                detail="该博主已加入交易黑名单：仅记录消息，不调用交易所 API",
            )
            return result

        if _is_executable_update(content, result):
            await self._execute_trade_action(
                message_id=message_id,
                author=author,
                content=content,
                result=result,
                allow_execute=allow_execute,
            )
            return result

        if result.intent != "open":
            self.db.update_message_status(message_id, "skipped")
            if result.intent in {"close", "partial_close", "update_sl", "update_tp", "cancel", "hold"}:
                await notify_trade_update(
                    self.db,
                    message_id=message_id,
                    author=author,
                    content=content,
                    result=result,
                )
            return result

        if not result.should_act:
            self.db.update_message_status(message_id, "skipped")
            await notify_signal_detected(
                self.db,
                message_id=message_id,
                author=author,
                content=content,
                result=result,
            )
            return result

        if not allow_execute:
            self.db.update_message_status(message_id, "skipped")
            self.db.audit(
                "recovery_signal_skipped",
                "offline recovery messages are never executed",
                message_id=message_id,
            )
            await notify_trade_pipeline(
                self.db,
                title="离线补抓信号（未执行）",
                message_id=message_id,
                author=author,
                content=content,
                result=result,
                detail="电脑离线期间的历史消息仅分析与通知，不自动下单",
            )
            return result

        if feature_enabled(self.db, "risk_check"):
            decision = self.risk.check(result, message_id)
            if not decision.allowed:
                decision = self._recheck_position_risk_with_exchange(
                    decision,
                    channel_id=channel_id,
                    result=result,
                    message_id=message_id,
                )
            if not decision.allowed:
                self.db.update_message_status(message_id, "skipped")
                self.db.audit("risk_rejected", decision.reason, message_id=message_id)
                await notify_trade_pipeline(
                    self.db,
                    title="风控拒绝",
                    message_id=message_id,
                    author=author,
                    content=content,
                    result=result,
                    detail=decision.reason,
                )
                return result

        if not feature_enabled(self.db, "okx_execute"):
            self.db.update_message_status(message_id, "skipped")
            await notify_trade_pipeline(
                self.db,
                title="交易所执行已关闭",
                message_id=message_id,
                author=author,
                content=content,
                result=result,
                detail="okx_execute 开关关闭",
            )
            return result

        exchange = exchange_label(self.db, channel_id)
        if not exchange_configured(self.db, channel_id):
            self.db.update_message_status(message_id, "skipped")
            await notify_trade_pipeline(
                self.db,
                title=f"{exchange} 尚未配置",
                message_id=message_id,
                author=author,
                content=content,
                result=result,
                detail=f"{exchange} API Key 未配置，已阻止执行",
            )
            return result
        try:
            executor = create_exchange_executor(self.db, self.risk, channel_id=channel_id)
            execution = executor.open_with_sl_tp(result, message_id, signal_id)
        except Exception as exc:
            logger.exception("%s execution failed", exchange)
            execution = {"ok": False, "error": str(exc)}

        if execution.get("ok"):
            self.db.update_message_status(message_id, "executed")
            if result.symbol:
                _save_target_level(self.db, channel_id, result.symbol, 0)
            title, detail = _open_success_title_detail(exchange, execution)
        elif execution.get("skipped"):
            self.db.update_message_status(message_id, "skipped")
            title = "风控跳过"
            detail = str(execution.get("error") or "下单前检查未通过")
            self.db.audit("execution_skipped", detail[:1000], message_id=message_id)
        else:
            self.db.update_message_status(message_id, "error")
            title = f"{exchange} 下单失败"
            detail = _execution_error_detail(execution)
            self.db.audit("execution_error", detail[:1000], message_id=message_id)

        await notify_trade_pipeline(
            self.db,
            title=title,
            message_id=message_id,
            author=author,
            content=content,
            result=result,
            detail=detail,
            exec_result=execution,
        )
        return result

    def _recent_channel_context(self, message_id: int, channel_id: str) -> str:
        if not channel_id:
            return ""
        rows, _ = self.db.list_messages(page=1, per_page=8, channel_id=channel_id)
        context = [
            str(row.get("content") or "").strip()
            for row in rows
            if int(row.get("id") or 0) != message_id and str(row.get("content") or "").strip()
        ]
        return "\n---\n".join(reversed(context[:6]))

    async def _execute_trade_action(
        self,
        *,
        message_id: int,
        author: str,
        content: str,
        result: IntentResult,
        allow_execute: bool,
    ) -> None:
        if not allow_execute:
            self.db.update_message_status(message_id, "skipped")
            await notify_trade_pipeline(
                self.db,
                title="离线交易更新（未执行）",
                message_id=message_id,
                author=author,
                content=content,
                result=result,
                detail="离线补抓消息不自动操作交易所",
            )
            return
        message = self.db.get_message(message_id) or {}
        channel_id = str(message.get("channel_id") or "")
        exchange = exchange_label(self.db, channel_id)
        if not exchange_configured(self.db, channel_id):
            execution = {"ok": False, "skipped": True, "error": f"{exchange} API Key 未配置，已阻止执行"}
            self.db.update_message_status(message_id, "skipped")
            await notify_trade_pipeline(
                self.db,
                title=f"{exchange} 尚未配置",
                message_id=message_id,
                author=author,
                content=content,
                result=result,
                detail=execution["error"],
                exec_result=execution,
            )
            return
        inst_id = self._resolve_inst_id(channel_id, result)
        if not inst_id:
            execution = {"ok": False, "skipped": True, "error": "无法确定该频道要操作的品种"}
        else:
            try:
                client = create_exchange_client(self.db, channel_id=channel_id)
                if result.intent in {"close", "partial_close"}:
                    target_level = _target_level(result)
                    pct = 100.0 if result.intent == "close" else float(result.close_pct or 0)
                    if target_level:
                        previous_level = _saved_target_level(self.db, channel_id, result.symbol or "")
                        if target_level <= previous_level:
                            execution = {
                                "ok": False,
                                "skipped": True,
                                "error": f"TP{target_level} 已处理，忽略重复消息",
                            }
                        elif target_level < 6 and _has_active_tp_protection(client, inst_id):
                            result.close_pct = 0
                            execution = {
                                "ok": True,
                                "tracked_only": True,
                                "target_level": target_level,
                                "detail": f"TP{target_level} 已由交易所止盈保护单自动处理，系统仅记录进度",
                            }
                        else:
                            pct = 100.0 if target_level >= 6 else (
                                (target_level - previous_level) / max(1, 6 - previous_level) * 100
                            )
                            result.close_pct = pct
                            execution = client.close_position(inst_id, pct)
                    else:
                        execution = client.close_position(inst_id, pct)
                    stop_loss = result.stop_loss
                    if execution.get("ok") and _is_break_even(result):
                        stop_loss = _position_average(client, inst_id)
                        result.stop_loss = stop_loss
                    if execution.get("ok") and stop_loss and not execution.get("tracked_only"):
                        stop_update = client.update_stop_loss(inst_id, stop_loss)
                        execution["stop_update"] = stop_update
                        if not stop_update.get("ok"):
                            execution["ok"] = False
                            execution["partial_success"] = True
                            execution["error"] = (
                                "平仓已成功，但剩余仓位止损更新失败："
                                f"{_execution_error_detail(stop_update)}"
                            )
                    if execution.get("ok") and target_level:
                        _save_target_level(self.db, channel_id, result.symbol or "", target_level)
                elif result.intent == "update_sl":
                    stop_loss = result.stop_loss
                    if _is_break_even(result):
                        stop_loss = _position_average(client, inst_id)
                        result.stop_loss = stop_loss
                    if stop_loss:
                        execution = client.update_stop_loss(inst_id, float(stop_loss))
                    else:
                        execution = {
                            "ok": False,
                            "skipped": True,
                            "error": "无法读取交易所当前持仓均价，未移动止损",
                        }
                elif result.intent == "update_tp":
                    execution = client.update_take_profit(inst_id, float(result.take_profit or 0))
                else:
                    execution = client.cancel_open_orders(inst_id)
            except Exception as exc:
                logger.exception("%s trade update execution failed", exchange)
                execution = {"ok": False, "error": str(exc)}

        if execution.get("ok"):
            self.db.update_message_status(message_id, "executed")
            self.db.audit("trade_update_executed", str(execution)[:1000], message_id=message_id)
            title = f"{exchange} 交易更新执行成功"
            detail = _execution_detail(result, execution)
        elif execution.get("skipped"):
            self.db.update_message_status(message_id, "skipped")
            reason = str(execution.get("error") or "交易所无对应持仓")
            self.db.audit(
                "trade_update_skipped",
                reason,
                message_id=message_id,
                payload={
                    "exchange": exchange,
                    "inst_id": inst_id,
                    "intent": result.intent,
                    "api_called": bool(inst_id),
                },
            )
            title = "交易更新跳过"
            detail = (
                f"已调用 {exchange} API 查询 {inst_id}，但{reason}"
                if inst_id
                else reason
            )
        else:
            self.db.update_message_status(message_id, "error")
            self.db.audit("trade_update_error", str(execution)[:1000], message_id=message_id)
            title = f"{exchange} 交易更新失败"
            detail = _execution_error_detail(execution)
        await notify_trade_pipeline(
            self.db,
            title=title,
            message_id=message_id,
            author=author,
            content=content,
            result=result,
            detail=detail,
            exec_result=execution,
        )

    def _resolve_inst_id(self, channel_id: str, result: IntentResult) -> str | None:
        if result.inst_id():
            return result.inst_id()
        for order in self.db.list_orders(limit=100):
            if str(order.get("channel_id") or "") == channel_id and order.get("inst_id"):
                return str(order["inst_id"])
        return None

    def _recheck_position_risk_with_exchange(
        self,
        decision,
        *,
        channel_id: str,
        result: IntentResult,
        message_id: int,
    ):
        reason = str(decision.reason or "")
        if not channel_id or not _is_position_limit_reason(reason):
            return decision
        if not exchange_configured(self.db, channel_id):
            return decision

        exchange = exchange_label(self.db, channel_id)
        try:
            client = create_exchange_client(self.db, channel_id=channel_id)
            active = _exchange_active_snapshot(client)
        except Exception as exc:
            logger.warning("%s position risk recheck failed: %s", exchange, exc)
            self.db.audit(
                "risk_recheck_error",
                f"{exchange}: {exc}",
                message_id=message_id,
                payload={"channel_id": channel_id, "reason": reason},
            )
            return decision

        stale_count = self.db.mark_stale_open_orders(
            channel_id=channel_id,
            active_inst_ids=active["inst_ids"],
        )
        if stale_count:
            self.db.audit(
                "stale_orders_marked",
                f"{channel_id}: {stale_count}",
                message_id=message_id,
                payload={"active_inst_ids": sorted(active["inst_ids"])},
            )

        symbol = str(result.symbol or "").upper()
        if symbol and symbol in active["symbols"]:
            return type(decision)(False, f"{symbol} 已有持仓")

        risk = self.risk._get_risk()
        max_pos = int(risk.get("max_open_positions", 3))
        if len(active["inst_ids"]) >= max_pos:
            return type(decision)(False, f"已达最大持仓数 {max_pos}")

        self.db.audit(
            "risk_stale_position_override",
            f"{reason} -> exchange active {len(active['inst_ids'])}",
            message_id=message_id,
            payload={
                "channel_id": channel_id,
                "exchange": exchange,
                "active_inst_ids": sorted(active["inst_ids"]),
                "symbol": symbol,
            },
        )
        return type(decision)(True, "")


def _explicit_full_close(content: str, result: IntentResult) -> bool:
    if result.close_pct is not None and result.close_pct >= 99:
        return True
    return any(
        marker in content.lower()
        for marker in ("全部止盈", "止盈出局", "出局吧", "closed", "trade closed", "全平")
    )


def _is_executable_update(content: str, result: IntentResult) -> bool:
    if result.intent == "close":
        return _explicit_full_close(content, result)
    if result.intent == "partial_close":
        return result.should_act and bool(result.close_pct and 0 < result.close_pct < 100)
    if result.intent == "update_sl":
        return result.should_act and bool(
            (result.stop_loss and result.stop_loss > 0) or _is_break_even(result)
        )
    if result.intent == "update_tp":
        return result.should_act and bool(result.take_profit and result.take_profit > 0)
    return result.intent == "cancel" and result.should_act


def _is_trade_action(result: IntentResult) -> bool:
    return result.intent in {"open", "close", "partial_close", "update_sl", "update_tp", "cancel"} and (
        result.should_act or result.intent in {"close", "partial_close", "update_sl", "update_tp", "cancel"}
    )


def _is_position_limit_reason(reason: str) -> bool:
    return "已有持仓" in reason or "已达最大持仓数" in reason


def _exchange_active_snapshot(client) -> dict[str, set[str]]:
    inst_ids: set[str] = set()
    symbols: set[str] = set()
    for row in client.get_positions():
        inst_id = _active_inst_id(row)
        if not inst_id or not _row_has_active_size(row):
            continue
        inst_ids.add(inst_id)
        symbols.add(_symbol_from_inst_id(inst_id))
    for row in client.get_open_orders():
        inst_id = _active_inst_id(row)
        if not inst_id:
            continue
        inst_ids.add(inst_id)
        symbols.add(_symbol_from_inst_id(inst_id))
    return {"inst_ids": inst_ids, "symbols": {symbol for symbol in symbols if symbol}}


def _active_inst_id(row: dict) -> str:
    inst_id = row.get("instId") or row.get("inst_id") or row.get("contract") or ""
    return str(inst_id).upper()


def _symbol_from_inst_id(inst_id: str) -> str:
    return str(inst_id or "").split("-", 1)[0].upper()


def _row_has_active_size(row: dict) -> bool:
    for key in ("pos", "positionAmt", "size", "contracts"):
        if key not in row:
            continue
        try:
            return abs(float(row.get(key) or 0)) > 0
        except (TypeError, ValueError):
            continue
    return True


def _blocked_trade_channels(db: Database) -> set[str]:
    raw = db.get_setting(BLOCKED_TRADE_CHANNELS_KEY)
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return set()
    if isinstance(data, dict):
        return {str(channel_id) for channel_id, blocked in data.items() if blocked}
    if isinstance(data, list):
        return {str(channel_id) for channel_id in data}
    return set()


def _channel_trade_blocked(db: Database, channel_id: str) -> bool:
    return str(channel_id) in _blocked_trade_channels(db)


def _execution_detail(result: IntentResult, execution: dict) -> str:
    if execution.get("tracked_only"):
        return str(execution.get("detail") or "交易更新已记录，未重复调用交易所下单")
    if result.intent == "cancel":
        return f"已撤销挂单 {execution.get('canceled_count', '-') } 笔"
    if result.intent == "update_sl":
        return f"止损已更新至 {result.stop_loss:g}"
    if result.intent == "update_tp":
        return f"止盈已更新至 {result.take_profit:g}"
    pct = 100 if result.intent == "close" else result.close_pct
    detail = f"已提交 reduce-only 市价平仓 {pct:g}%"
    if execution.get("stop_update"):
        detail += f"，剩余仓位止损更新至 {result.stop_loss:g}"
    return detail


def _is_break_even(result: IntentResult) -> bool:
    return "break_even" in str(result.entry_note or "").lower()


def _open_success_title_detail(exchange: str, execution: dict) -> tuple[str, str]:
    if execution.get("dry_run"):
        return "模拟下单已记录", "dry_run"
    label = _execution_order_type_label(execution)
    if label == "市价单":
        return f"{exchange} 市价单下单成功", "市价单与止盈止损已提交"
    if label == "限价单":
        return f"{exchange} 限价单提交成功", "限价单与止盈止损已提交"
    return f"{exchange} 实盘下单成功", "订单与止盈止损已提交"


def _execution_order_type_label(execution: dict) -> str:
    plan = execution.get("plan") or {}
    order_type = str(plan.get("ord_type") or plan.get("order_type") or "").lower()
    entry_note = str(plan.get("entry_note") or "").lower()
    if "market" in order_type or "market" in entry_note:
        return "市价单"
    if "limit" in order_type or "limit" in entry_note:
        return "限价单"
    if plan.get("px") is not None:
        return "限价单"
    return "订单"


def _position_average(client, inst_id: str) -> float | None:
    for position in client.get_positions():
        if str(position.get("instId") or position.get("inst_id") or "") != inst_id:
            continue
        for key in ("avgPx", "entry_price", "entryPrice", "avg_px"):
            try:
                value = float(position.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def _has_active_tp_protection(client, inst_id: str) -> bool:
    get_triggers = getattr(client, "get_open_triggers", None)
    if not callable(get_triggers):
        return False
    try:
        triggers = get_triggers()
    except Exception:
        logger.debug("Unable to inspect active TP protection", exc_info=True)
        return False
    for row in triggers:
        if str(row.get("instId") or row.get("inst_id") or "") != inst_id:
            continue
        state = str(row.get("state") or row.get("status") or "live").lower()
        if state not in {"live", "open", "active"}:
            continue
        if row.get("tpTriggerPx") or row.get("stop_profit_price"):
            return True
    return False


def _execution_error_detail(execution: dict) -> str:
    direct = execution.get("error")
    found: list[str] = [str(direct)] if direct else []

    def visit(value) -> None:
        if isinstance(value, dict):
            code = value.get("sCode") or value.get("code") or value.get("label") or value.get("status_code")
            message = value.get("sMsg") or value.get("msg") or value.get("message") or value.get("description")
            if message:
                text = f"{code}: {message}" if code not in (None, "", "0", 0) else str(message)
                if text not in found:
                    found.append(text)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(execution.get("response") or execution)
    if _looks_like_margin_error(found):
        prefix = "保证金不足：交易所可用 USDT 不足，或下单/止损单需要额外手续费与冻结余量"
        found = [prefix, *[item for item in found if item != prefix]]
    if _looks_like_position_side_error(found):
        prefix = "Binance 双向持仓参数错误：账户为 Hedge Mode，下单必须携带 LONG/SHORT positionSide"
        found = [prefix, *[item for item in found if item != prefix]]
    return "；".join(found[:3]) if found else "交易所未返回具体失败原因"


def _looks_like_margin_error(items: list[str]) -> bool:
    text = " ".join(items).lower()
    return "51008" in text or ("insufficient" in text and "margin" in text)


def _looks_like_position_side_error(items: list[str]) -> bool:
    text = " ".join(items).lower()
    return "-4061" in text or "position side does not match" in text


def _target_level(result: IntentResult) -> int:
    import re

    match = re.search(r"target_level:(\d+)", str(result.entry_note or ""))
    return min(int(match.group(1)), 6) if match else 0


def _target_level_key(channel_id: str, symbol: str) -> str:
    return f"target_level:{channel_id}:{symbol.upper()}"


def _saved_target_level(db: Database, channel_id: str, symbol: str) -> int:
    try:
        return int(db.get_setting(_target_level_key(channel_id, symbol)) or 0)
    except (TypeError, ValueError):
        return 0


def _save_target_level(db: Database, channel_id: str, symbol: str, level: int) -> None:
    db.set_setting(_target_level_key(channel_id, symbol), str(level))
