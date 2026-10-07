"""TradingClient + executor for the ``paper`` route, shaped like the OKX ones.

The executor mirrors ``OkxExecutor.open_with_sl_tp`` for entry price policy,
deviation guard and leverage caps, with public last price in place of the
authenticated mark. Sizing differs on purpose (owner, 2026-10-07): live sizes by
margin (balance x position_pct x leverage), paper sizes by fixed risk -- 1R is
``paper_risk_pct`` (default 1%) of realized equity and the quantity follows from
the stop distance, capped by what the channel's leverage allows. It ignores ``dry_run``:
paper orders never reach an exchange, so there is nothing to suppress.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.exchange import selected_leverage, should_use_market_entry
from yoyo.copier.okx.executor import (OKX_MARGIN_BUFFER_PCT, _entry_deviation_error, _entry_price,
                                       _selected_notional_leverage, _usable_notional)
from yoyo.copier.paper.book import PaperBook, direction
from yoyo.copier.paper.market import PublicMarket
from yoyo.copier.price_normalizer import normalize_decimal_shorthand_to_mark
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database

_MARKET = PublicMarket()
DEFAULT_RISK_PCT = 0.01  # owner, 2026-10-07: each paper trade risks 1% of the account


def paper_risk_pct(db: Database) -> float:
    try:
        value = float(db.get_setting("paper_risk_pct") or DEFAULT_RISK_PCT)
    except (TypeError, ValueError):
        return DEFAULT_RISK_PCT
    return value if 0 < value <= 0.05 else DEFAULT_RISK_PCT
AGGREGATE = None  # account=None reads every paper account (health/position monitors)


def _price(market: PublicMarket, book: PaperBook, inst_id: str) -> Optional[tuple[float, str]]:
    quote = market.last(inst_id)
    if quote:
        book.set_mark(inst_id, quote[0], quote[1])
    return quote


class PaperClient:
    def __init__(self, db: Database, account: Optional[str] = AGGREGATE, market: Optional[PublicMarket] = None) -> None:
        self.db = db
        self.account = account
        self.book = PaperBook(db)
        self.market = market or _MARKET

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "balance": self.get_balance_summary()}

    def get_balance_usdt(self) -> float:
        return self.book.balance(self.account)["available_usdt"]

    def get_balance_summary(self) -> dict[str, float]:
        b = self.book.balance(self.account)
        return {"total_usdt": b["total_usdt"], "available_usdt": b["available_usdt"], "unrealized_pnl": b["unrealized_pnl"]}

    def get_positions(self) -> list[dict[str, Any]]:
        rows = []
        for p in self.book.open_positions(self.account):
            mark = self.book.mark(p["inst_id"]) or p["entry_px"]
            rows.append({"instId": p["inst_id"], "posSide": p["side"], "pos": str(p["open_qty"] * direction(p["side"])),
                         "avgPx": str(p["entry_px"]), "notionalUsd": str(p["open_qty"] * mark),
                         "upl": str(self.book.unrealized(p)), "lever": str(p["leverage"]), "account": p["account"]})
        return rows

    def get_open_orders(self) -> list[dict[str, Any]]:
        return [{"instId": o["inst_id"], "ordId": f"paper-{o['id']}", "px": str(o["px"]), "sz": str(o["qty"]),
                 "side": "buy" if o["side"] == "long" else "sell", "state": "live", "account": o["account"]}
                for o in self.book.pending_orders(self.account)]

    def get_open_triggers(self) -> list[dict[str, Any]]:
        return [{"instId": p["inst_id"], "ordId": f"paper-pos-{p['id']}", "state": "live",
                 "slTriggerPx": str(p["sl"] or ""), "tpTriggerPx": str(p["tp"] or "")}
                for p in self.book.open_positions(self.account) if p["sl"] or p["tp"]]

    def _position(self, inst_id: str) -> Optional[dict[str, Any]]:
        rows = self.book.open_positions(self.account, inst_id)
        return rows[0] if rows else None

    def close_position(self, inst_id: str, close_pct: float = 100.0) -> dict[str, Any]:
        pos = self._position(inst_id)
        if not pos:
            return {"ok": False, "skipped": True, "error": "模拟盘无对应持仓"}
        quote = _price(self.market, self.book, inst_id)
        if not quote:
            return {"ok": False, "error": f"{inst_id} 取不到公开行情，未平仓"}
        result = self.book.close(pos["id"], close_pct, quote[0], "manual_close" if close_pct >= 100 else "partial_close")
        return {"ok": True, "paper": True, "closed_size": result["closed_qty"], "px": quote[0], "pnl": result["pnl"]}

    def cancel_open_orders(self, inst_id: str) -> dict[str, Any]:
        orders = self.book.pending_orders(self.account, inst_id)
        for order in orders:
            self.book.cancel(order["id"])
        if not orders:
            return {"ok": False, "skipped": True, "error": "模拟盘无对应挂单"}
        return {"ok": True, "paper": True, "canceled": len(orders)}

    def update_stop_loss(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        pos = self._position(inst_id)
        if not pos:
            return {"ok": False, "skipped": True, "error": "模拟盘无对应持仓"}
        if not trigger_px or trigger_px <= 0:
            return {"ok": False, "skipped": True, "error": "止损价无效"}
        self.book.set_stop(pos["id"], sl=float(trigger_px))
        return {"ok": True, "paper": True, "sl": float(trigger_px)}

    def update_take_profit(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        pos = self._position(inst_id)
        if not pos:
            return {"ok": False, "skipped": True, "error": "模拟盘无对应持仓"}
        if not trigger_px or trigger_px <= 0:
            return {"ok": False, "skipped": True, "error": "止盈价无效"}
        self.book.set_stop(pos["id"], tp=float(trigger_px))
        return {"ok": True, "paper": True, "tp": float(trigger_px)}

    def cancel_trigger_order(self, order_id: str) -> dict[str, Any]:
        return {"ok": True, "paper": True}


class PaperExecutor:
    def __init__(self, db: Database, risk: RiskEngine, account: str, market: Optional[PublicMarket] = None) -> None:
        self.db = db
        self.risk = risk
        self.account = account
        self.book = PaperBook(db)
        self.market = market or _MARKET

    def open_with_sl_tp(self, result: IntentResult, message_id: int, signal_id: int) -> dict[str, Any]:
        inst_id = result.inst_id()
        if not inst_id:
            return {"ok": False, "error": "no inst_id"}
        from yoyo.copier.runtime_config import get_okx

        cfg = get_okx(self.db)
        message = self.db.get_message(message_id) or {}
        channel_id = str(message.get("channel_id") or "")
        leverage = min(selected_leverage(self.db, channel_id, fallback=result.leverage_hint or cfg["default_leverage"]),
                       self.risk.get_max_leverage())
        quote = _price(self.market, self.book, inst_id)
        if not quote:
            return {"ok": False, "error": f"OKX 与 Gate 公开行情都没有 {inst_id}，模拟盘无法开仓"}
        mark, venue = quote
        price_scale_adjusted = normalize_decimal_shorthand_to_mark(
            result, mark, max_deviation=self.risk.get_max_entry_deviation_pct())
        is_market = should_use_market_entry(result, mark, str(message.get("content") or ""))
        px = mark if is_market else _entry_price(result, mark, str(cfg.get("entry_policy") or "mid"))
        deviation_error = None if is_market else _entry_deviation_error(px, mark, self.risk.get_max_entry_deviation_pct())
        if deviation_error:
            return {"ok": False, "skipped": True, "error": deviation_error}
        side = "long" if result.side == "long" else "short"
        stop = result.stop_loss
        if not stop or stop <= 0:
            return {"ok": False, "skipped": True, "error": "信号没有止损，无法按固定风险定 1R，模拟盘不开仓"}
        distance = (px - stop) if side == "long" else (stop - px)
        if distance <= 0 or px <= 0:
            return {"ok": False, "skipped": True, "error": f"止损 {stop:g} 在入场价 {px:g} 的错误一侧，模拟盘不开仓"}
        bal = self.book.balance(self.account)
        balance, equity = bal["available_usdt"], bal["equity"]
        risk_pct = paper_risk_pct(self.db)
        position_pct = self.risk.get_position_pct()
        notional_leverage = min(_selected_notional_leverage(self.db, channel_id, leverage), leverage)
        # Fixed fractional risk (owner, 2026-10-07): 1R = risk_pct of realized equity, and the
        # size follows from the stop distance. Leverage only caps how large that size may get.
        risk_budget = equity * risk_pct
        qty = risk_budget / distance
        max_notional = _usable_notional(balance, 1.0, notional_leverage)
        capped = qty * px > max_notional
        if capped:
            qty = max_notional / px
        notional = qty * px
        if notional <= 0:
            return {"ok": False, "skipped": True, "error": "模拟账户可用保证金不足（已有持仓或挂单占用）"}
        plan = {
            "exchange": "paper", "venue": venue, "account": self.account, "inst_id": inst_id,
            "side": "buy" if side == "long" else "sell", "px": px, "sz": f"{qty:.8g}",
            "sl": result.stop_loss, "tp": result.take_profit, "leverage": leverage,
            "notional_leverage": notional_leverage, "margin_usdt": notional / max(leverage, 1),
            "position_pct": position_pct, "notional_usdt": notional, "balance": balance,
            "sizing": "fixed_risk", "risk_pct": risk_pct, "equity": equity, "risk_budget_usdt": risk_budget,
            "risk_usdt": qty * distance, "stop_distance_pct": distance / px, "capped_by_leverage": capped,
            "margin_buffer_pct": OKX_MARGIN_BUFFER_PCT, "ord_type": "market" if is_market else "limit",
            "entry_note": result.entry_note, "price_scale_adjusted": price_scale_adjusted, "mark": mark,
        }
        order = self.book.place(account=self.account, inst_id=inst_id, side=side, ord_type=plan["ord_type"], px=px,
                                qty=qty, leverage=leverage, sl=result.stop_loss, tp=result.take_profit,
                                message_id=message_id, venue=venue)
        status = "open" if order["status"] == "pending" else "filled"
        row_id = self.db.insert_order(
            message_id=message_id, signal_id=signal_id, inst_id=inst_id, side=plan["side"], ord_type=plan["ord_type"],
            sz=plan["sz"], px=str(px), okx_ord_id=f"paper-{order['id']}",
            sl_trigger=str(result.stop_loss) if result.stop_loss else None,
            tp_trigger=str(result.take_profit) if result.take_profit else None,
            status="live", response_json=json.dumps({"plan": plan, "response": {"status": status, "paper_order_id": order["id"]}},
                                                    ensure_ascii=False))
        self.book.link_order_row(order["id"], row_id)
        self.db.audit("paper_order", json.dumps(plan, ensure_ascii=False), message_id=message_id)
        return {"ok": True, "paper": True, "plan": plan, "response": {"status": status, "paper_order_id": order["id"]}}
