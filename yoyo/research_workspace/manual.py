"""Personal decision journal, separate from strategy evaluation and execution.

The owner's 2026-09-16 manual-system v2 supplies candidate playbooks, not
approved risk defaults or evidence of profitability. CME's trade-plan outline
also separates methodology, risk and a trader log:
https://www.cmegroup.com/education/courses/building-a-trade-plan

All amounts are user-entered USDT for linear instruments. Initial price risk
uses only the recorded entry, original stop and base-coin quantity; it excludes
fees, funding and gaps. Net PnL is explicitly entered from the user's ledger,
never inferred from signal outcomes. This module has no exchange client.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .store import now


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    expected_revision: int = Field(default=0, ge=0)


class Playbook(Record):
    name: str = Field(min_length=2, max_length=120)
    setup: str = Field(default="", max_length=4000)
    context: str = Field(default="", max_length=4000)
    entry_rule: str = Field(default="", max_length=4000)
    invalidation_rule: str = Field(default="", max_length=4000)
    exit_rule: str = Field(default="", max_length=4000)
    avoid_rule: str = Field(default="", max_length=4000)
    management_rule: str = Field(default="", max_length=4000)
    status: Literal["draft", "ready", "archived"] = "draft"
    source_template: Optional[Literal["ma-launch", "bb-reversal"]] = None


class Session(Record):
    date: date
    title: str = Field(min_length=2, max_length=120)
    market_context: str = Field(default="", max_length=4000)
    watchlist: str = Field(default="", max_length=2000)
    focus: str = Field(default="", max_length=4000)
    avoid: str = Field(default="", max_length=4000)
    availability: str = Field(default="", max_length=2000)
    stop_rule: str = Field(default="", max_length=4000)
    risk_budget_usdt: Optional[float] = Field(default=None, gt=0, le=1e12)


class Ticket(Record):
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,24}-USDT-SWAP$")
    side: Literal["long", "short"]
    timeframe: str = Field(min_length=1, max_length=20)
    playbook_id: Optional[str] = Field(default=None, pattern=r"^manual-playbook-[0-9a-f]{16}$")
    playbook_revision: Optional[int] = Field(default=None, ge=1)
    session_id: Optional[str] = Field(default=None, pattern=r"^manual-session-[0-9a-f]{16}$")
    mode: Literal["practice", "real"]
    status: Literal["watching", "planned"] = "watching"
    thesis: str = Field(default="", max_length=4000)
    trigger: str = Field(default="", max_length=4000)
    invalidation: str = Field(default="", max_length=4000)
    exit_plan: str = Field(default="", max_length=4000)
    signal_ref: str = Field(default="", max_length=500)
    planned_entry: Optional[float] = Field(default=None, gt=0, le=1e12)
    planned_stop: Optional[float] = Field(default=None, gt=0, le=1e12)
    risk_budget_usdt: Optional[float] = Field(default=None, gt=0, le=1e12)


class Event(Record):
    action: Literal["open", "close", "skip", "review", "reconcile", "manage"]
    occurred_at: Optional[datetime] = None
    entry_price: Optional[float] = Field(default=None, gt=0, le=1e12)
    initial_stop: Optional[float] = Field(default=None, gt=0, le=1e12)
    quantity: Optional[float] = Field(default=None, gt=0, le=1e12)
    net_pnl_usdt: Optional[float] = Field(default=None, ge=-1e24, le=1e24)
    notes: str = Field(default="", max_length=8000)
    adherence: Literal["followed", "deviated", "unknown"] = "unknown"
    lesson: str = Field(default="", max_length=4000)
    protection_status: Literal["unconfirmed", "confirmed", "none"] = "unconfirmed"

    @model_validator(mode="after")
    def aware_time(self):
        if self.occurred_at is not None and self.occurred_at.utcoffset() is None:
            raise ValueError("成交时间必须包含时区。")
        if self.occurred_at is not None and self.action not in {"open", "close"}:
            raise ValueError("此事件仅记录提交时间；不接受会被忽略的成交时间。")
        allowed = {"open": {"entry_price", "initial_stop", "quantity"},
                   "close": {"net_pnl_usdt"}, "reconcile": {"net_pnl_usdt"}}
        for name in {"entry_price", "initial_stop", "quantity", "net_pnl_usdt"} - allowed.get(self.action, set()):
            if getattr(self, name) is not None:
                raise ValueError(f"{self.action} 事件不接受 {name}，未写入。")
        return self


SOURCES = [
    dict(name="个人交易手册 v2 · 历史提案", url="https://app.notion.com/p/3dd8856479af81fc8db1e26ee985359e"),
    dict(name="CME · 交易计划与日志", url="https://www.cmegroup.com/education/courses/building-a-trade-plan"),
]

TEMPLATES = [
    dict(id="ma-launch", name="均线密集启动 · 人工规则草稿", status="draft",
         setup="主练均线密集后启动；本人看图确认，YOLO 只作辅助观察。",
         context="先记录大盘环境、所在结构与关键位置。", entry_rule="",
         invalidation_rule="", exit_rule="", avoid_rule="", management_rule="",
         note="来自 2026-09-16 手册。历史 V9 与当前 V12.8 需明确选择；周期、风险及退出规则由本人补齐，不沿用未经确认的数值。"),
    dict(id="bb-reversal", name="BB 反转 · 独立人工规则草稿", status="draft",
         setup="作为独立形态记录，与均线启动的结果分开评价。",
         context="先核对当前趋势、结构与是否有能力持续盯盘。", entry_rule="",
         invalidation_rule="", exit_rule="", avoid_rule="", management_rule="",
         note="历史手册提案，不代表已证实盈利；需明确实际采用的指标版本与趋势排除条件。"),
]


def _directional_stop(side, entry, stop):
    if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
        raise ValueError("初始止损需位于入场价的亏损方向。")


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ManualWorkspace:
    """Append-only record versions; compare-and-swap prevents lost updates."""

    def __init__(self, store):
        self.store = store

    def records(self, kind):
        return sorted((dict(value, id=key) for key, value in self.store.notes("manual_" + kind).items()),
                      key=lambda value: value["updated_at"], reverse=True)

    def get(self, kind, key):
        value = self.store.notes("manual_" + kind).get(key)
        if value is None:
            raise KeyError("个人交易记录不存在。")
        return dict(value, id=key)

    def _save(self, kind, payload, key=None):
        previous = self.get(kind, key) if key else None
        if previous is None and payload.expected_revision:
            raise ValueError("新记录版本必须从 0 开始。")
        key = key or "manual-" + kind + "-" + uuid.uuid4().hex[:16]
        value = payload.model_dump(mode="json", exclude={"expected_revision"})
        value["created_at"] = previous["created_at"] if previous else now()
        return key, value, previous

    def _write(self, kind, key, value, revision):
        result = self.store.save_note("manual_" + kind, key, value, revision)
        with self.store.connect() as db:
            stamp = db.execute("SELECT created_at FROM notes WHERE kind=? AND entity_id=? AND revision=?",
                               ("manual_" + kind, key, result["revision"])).fetchone()[0]
        return dict(result, id=key, updated_at=stamp)

    def save_playbook(self, payload, key=None):
        if payload.status == "ready" and any(not getattr(payload, field) for field in (
                "setup", "context", "entry_rule", "invalidation_rule", "exit_rule", "avoid_rule", "management_rule")):
            raise ValueError("启用个人规则前，请补齐形态、环境、入场、失效、退出、不做条件和持仓管理。")
        key, value, _ = self._save("playbook", payload, key)
        return self._write("playbook", key, value, payload.expected_revision)

    def save_session(self, payload, key=None):
        key, value, _ = self._save("session", payload, key)
        return self._write("session", key, value, payload.expected_revision)

    def save_ticket(self, payload, key=None):
        key, value, previous = self._save("ticket", payload, key)
        if previous and previous["status"] not in {"watching", "planned"}:
            raise ValueError("已成交或已放弃的计划已冻结，请通过事件追加复盘。")
        playbook = self.get("playbook", payload.playbook_id) if payload.playbook_id else None
        if bool(payload.playbook_id) != bool(payload.playbook_revision):
            raise ValueError("个人规则 ID 与版本需同时填写或同时留空。")
        if playbook and playbook["revision"] != payload.playbook_revision:
            raise ValueError("个人规则已更新，请重新选择版本。")
        if playbook and playbook["status"] == "archived":
            raise ValueError("归档规则不能用于新计划。")
        if payload.planned_entry is not None and payload.planned_stop is not None:
            _directional_stop(payload.side, payload.planned_entry, payload.planned_stop)
        if payload.status == "planned":
            required = (payload.thesis, payload.trigger, payload.invalidation, payload.exit_plan,
                        payload.planned_entry, payload.planned_stop, payload.risk_budget_usdt)
            if not playbook or playbook["status"] != "ready" or not all(required):
                raise ValueError("正式计划需启用的个人规则、交易理由、触发与失效条件、退出计划、参考入场/止损和自定风险预算。")
        value["playbook_snapshot"] = playbook
        value["session_snapshot"] = self.get("session", payload.session_id) if payload.session_id else None
        value["record_source"] = "owner_manual"
        value["evidence_status"] = "self_reported_unreconciled"
        return self._write("ticket", key, value, payload.expected_revision)

    def event(self, key, payload):
        previous = self.get("ticket", key)
        value = {k: deepcopy(v) for k, v in previous.items() if k not in {"revision", "id", "updated_at"}}
        action, status = payload.action, previous["status"]
        stamp = now()
        occurred = payload.occurred_at
        if action in {"open", "close"}:
            if occurred is None or occurred > datetime.now(timezone.utc):
                raise ValueError("登记实际成交需填写已发生且含时区的时间。")
        if action == "open":
            if status not in {"watching", "planned"}:
                raise ValueError("只能对观察或计划登记一次已成交。")
            if payload.entry_price is None or payload.quantity is None:
                raise ValueError("请填写实际平均成交价和基础币数量，不能填写合约张数。")
            risk = None
            if payload.initial_stop is not None:
                _directional_stop(value["side"], payload.entry_price, payload.initial_stop)
                risk = float(abs(Decimal(str(payload.entry_price)) - Decimal(str(payload.initial_stop))) * Decimal(str(payload.quantity)))
            value.update(status="open", plan_snapshot=previous,
                         execution=dict(opened_at=occurred.isoformat(), entry_price=payload.entry_price,
                                        initial_stop=payload.initial_stop, quantity=payload.quantity,
                                        initial_risk_usdt=risk, backfilled=occurred < _time(previous["updated_at"]),
                                        unplanned=status != "planned", notes=payload.notes,
                                        protection_status=payload.protection_status,
                                        price_risk_budget_exceeded=(risk > value["risk_budget_usdt"])
                                        if risk is not None and value["risk_budget_usdt"] is not None else None))
        elif action == "close":
            if status != "open":
                raise ValueError("只能对已登记持仓记录全部平仓。")
            if occurred < _time(value["execution"]["opened_at"]):
                raise ValueError("平仓时间不能早于入场时间。")
            risk, net = value["execution"]["initial_risk_usdt"], payload.net_pnl_usdt
            value.update(status="closed", outcome=dict(closed_at=occurred.isoformat(), net_pnl_usdt=net,
                         net_r=float(Decimal(str(net)) / Decimal(str(risk))) if net is not None and risk else None,
                         r_basis="net_pnl_over_initial_price_risk_excluding_fees_funding_gaps",
                         notes=payload.notes))
        elif action == "skip":
            if status not in {"watching", "planned"} or not payload.notes:
                raise ValueError("放弃观察或计划时请保留理由。")
            value.update(status="skipped", skipped=dict(notes=payload.notes, recorded_at=stamp))
        elif action == "reconcile":
            if status != "closed" or not payload.notes:
                raise ValueError("仅已平仓记录可补记净收益，请注明补记或更正依据。")
            risk, net = value["execution"]["initial_risk_usdt"], payload.net_pnl_usdt
            value["outcome"].update(net_pnl_usdt=net,
                net_r=float(Decimal(str(net)) / Decimal(str(risk))) if net is not None and risk else None,
                reconciliation=dict(notes=payload.notes, recorded_at=stamp))
        elif action == "manage":
            if status != "open" or not payload.notes:
                raise ValueError("持仓管理仅追加到已登记持仓，请记录实际操作与保护确认情况。")
            value.setdefault("management", []).append(dict(notes=payload.notes,
                protection_status=payload.protection_status, recorded_at=stamp))
        else:
            if status not in {"closed", "skipped"} or not payload.notes or not payload.lesson:
                raise ValueError("交易结束或放弃后，请填写复盘事实与下一步改进。")
            value["review"] = dict(notes=payload.notes, adherence=payload.adherence, lesson=payload.lesson,
                                   recorded_at=stamp)
        value["last_event"] = dict(action=action, recorded_at=stamp)
        return self._write("ticket", key, value, payload.expected_revision)

    def overview(self):
        tickets = self.records("ticket")
        summary = dict(watching=0, planned=0, open=0, closed=0, skipped=0, pending_review=0)
        for value in tickets:
            summary[value["status"]] += 1
            if value["status"] in {"closed", "skipped"} and not value.get("review"):
                summary["pending_review"] += 1
        for mode in ("real", "practice"):
            rows = [value for value in tickets if value["mode"] == mode and value["status"] == "closed"]
            def results(group):
                known = [value["outcome"]["net_pnl_usdt"] for value in group if value["outcome"]["net_pnl_usdt"] is not None]
                return dict(closed=len(group), known_pnl=len(known), unknown_pnl=len(group) - len(known),
                            net_pnl_usdt=float(sum(Decimal(str(x)) for x in known)) if known else None,
                            wins=sum(x > 0 for x in known),
                            win_rate=sum(x > 0 for x in known) / len(known) if known else None,
                            backfilled=sum(value["execution"]["backfilled"] for value in group))
            summary[mode] = dict(results(rows), evidence_status="self_reported_unreconciled", cohorts={
                "prospective": results([row for row in rows if not row["execution"]["backfilled"]]),
                "retrospective": results([row for row in rows if row["execution"]["backfilled"]])})
        return dict(playbooks=self.records("playbook"), sessions=self.records("session"), tickets=tickets,
                    templates=deepcopy(TEMPLATES), summary=summary, sources=SOURCES,
                    capabilities=dict(exchange_connected=False, order_execution=False, automatic_position_sync=False,
                                      instrument_scope="USDT linear perpetual; quantity in base coin", partial_fill_accounting=False))
