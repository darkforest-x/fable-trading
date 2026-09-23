"""Static allow-list for offline forward-paper strategy adapters.

The catalog describes which existing signal streams have a bounded paper
entry/exit contract. It does not enable orders or imply economic acceptance.
Only the raw SPIKE V12.8 stream and the V12.8 long-joint stream currently have
the frozen 20bp / 2R / 4ATR / raw-opposite exit adapter.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


TIMEFRAMES = ("15m", "30m", "1H", "4H")
@dataclass(frozen=True)
class Plugin:
    """A small immutable descriptor consumed by the paper source adapter."""

    id: str
    source_database: str | None
    protocol: str | None
    kind: str | None
    timeframes: tuple[str, ...]
    side: str
    paper_supported: bool
    exit_adapter: str | None

    def accepts(self, event: Any = None) -> bool:
        """Check only the published signal identity and closed-bar contract."""
        if not self.paper_supported:
            return False
        if event is None:
            return True
        if not isinstance(event, dict):
            return False
        if (event.get("protocol") != self.protocol or event.get("kind") != self.kind
                or event.get("timeframe") not in self.timeframes):
            return False
        side = event.get("side")
        if self.side == "long" and side != "long":
            return False
        if self.side == "both" and side not in ("long", "short"):
            return False
        if self.id == "spike-v128":
            return (event.get("is_closed") is True and event.get("source") == "live"
                    and event.get("confirmation") == "raw")
        if self.id == "spike-v128-joint":
            # The joint notification contract is intentionally validated on
            # demand, so catalog import never pulls in the pandas scanner.
            from yoyo.monitor.joint_notifications import is_joint_event

            return is_joint_event(event)
        return False

    def evaluate_trade(self, candles, timeframe, symbol, tick, decision, now_ms):
        """Dispatch through a fixed adapter identifier, never a dynamic import."""
        if not self.paper_supported or self.exit_adapter != "spike-v128-frozen":
            raise ValueError("strategy has no frozen paper exit adapter")
        from yoyo.research_workspace.paper_source import evaluate_trade

        return evaluate_trade(candles, timeframe, symbol, tick, decision, now_ms)


_PLUGINS = {
    "spike-v128": Plugin(
        id="spike-v128", source_database="monitor.sqlite3",
        protocol="spike-burst-v128-monitor-v1", kind="spike_burst_v128",
        timeframes=TIMEFRAMES, side="both", paper_supported=True,
        exit_adapter="spike-v128-frozen",
    ),
    "spike-v128-joint": Plugin(
        id="spike-v128-joint", source_database="spike-lines-v1.sqlite3",
        protocol="spike-v128-lines-monitor-v1", kind="joint",
        timeframes=TIMEFRAMES, side="long", paper_supported=True,
        exit_adapter="spike-v128-frozen",
    ),
    "trendline-breakout": Plugin(
        id="trendline-breakout", source_database=None,
        protocol=None, kind=None, timeframes=TIMEFRAMES,
        side="long", paper_supported=False, exit_adapter=None,
    ),
    "yolo-confirmed": Plugin(
        id="yolo-confirmed", source_database=None,
        protocol="spike-burst-v128-yolo-confirmation-v1", kind="yolo_confirmed",
        timeframes=TIMEFRAMES, side="both", paper_supported=False, exit_adapter=None,
    ),
}


_CATALOG = [
    {
        "id": "spike-v128",
        "name": "SPIKE V12.8 原始信号",
        "version": "spike-v12.8-monitor-20260923-v1",
        "description": "SPIKE V12.8 已收盘多空确认信号。",
        "kind": "spike_burst_v128",
        "status": "research",
        "side": "both",
        "timeframes": list(TIMEFRAMES),
        "entry_rule": "首次观测到合格信号后，安排在下一个尚未开盘的周期开盘成交。",
        "exit_rule": "固定回放：信号初始止损、2R 启动／4ATR 跟踪、原始 V6 反向信号下一开盘退出；往返成本 20bp。",
        "cost_bp": 20,
        "factor_ids": [],
        "experiment_ids": ["exp-spike-v128-recent-20260923-v1"],
        "notes": "信号收盘价和参考价不作为成交价。历史经济研究不证明前向盈利；未验证，不具备生产准入。",
        "revision": 0,
        "production_eligible": False,
        "paper_supported": True,
        "exit_adapter": "spike-v128-frozen",
        "source_database": "monitor.sqlite3",
        "protocol": "spike-burst-v128-monitor-v1",
    },
    {
        "id": "spike-v128-joint",
        "name": "SPIKE V12.8 联合信号（仅多头）",
        "version": "spike-v128-lines-monitor-20260923-v1",
        "description": "V12.8 趋势线突破与 SPIKE 确认的已收盘联合信号，仅多头。",
        "kind": "joint",
        "status": "research",
        "side": "long",
        "timeframes": list(TIMEFRAMES),
        "entry_rule": "首次观测到合格联合信号后，安排在下一个尚未开盘的周期开盘成交。",
        "exit_rule": "固定回放：信号初始止损、2R 启动／4ATR 跟踪、原始 V6 反向信号下一开盘退出；往返成本 20bp。",
        "cost_bp": 20,
        "factor_ids": [],
        "experiment_ids": ["exp-spike-v128-recent-20260923-v1"],
        "notes": "止损和 tick 取自原始联合信号；监控器 performance 仅为展示投影，不作为成交。TradingView 原生严格 pivot 平局语义未验证，前向盈利也未验证。",
        "revision": 0,
        "production_eligible": False,
        "paper_supported": True,
        "exit_adapter": "spike-v128-frozen",
        "source_database": "spike-lines-v1.sqlite3",
        "protocol": "spike-v128-lines-monitor-v1",
    },
    {
        "id": "trendline-breakout",
        "name": "趋势线突破研究",
        "version": "trendline-v2-research",
        "description": "离线趋势线突破研究候选。",
        "kind": "trendline_breakout",
        "status": "research",
        "side": "long",
        "timeframes": list(TIMEFRAMES),
        "entry_rule": "仅离线研究；尚未启用模拟交易插件。",
        "exit_rule": "不可用：未登记完整且冻结的前向退出适配器。",
        "cost_bp": 20,
        "factor_ids": [],
        "experiment_ids": ["exp-trendline-v2-tbsl-20260918-v1"],
        "notes": "历史参数搜索结果不构成可复用的前向执行契约。",
        "revision": 0,
        "production_eligible": False,
        "paper_supported": False,
        "exit_adapter": None,
        "source_database": None,
        "protocol": None,
    },
    {
        "id": "yolo-confirmed",
        "name": "YOLO 模型确认",
        "version": "spike-burst-v128-yolo-confirmation-v1",
        "description": "模型确认信号流；模型状态不构成模拟交易退出契约。",
        "kind": "yolo_confirmed",
        "status": "research",
        "side": "both",
        "timeframes": list(TIMEFRAMES),
        "entry_rule": "仅离线研究；尚未启用模拟交易插件。",
        "exit_rule": "不可用：没有已接受并冻结的模型关联前向退出契约。",
        "cost_bp": 20,
        "factor_ids": [],
        "experiment_ids": [],
        "notes": "模型状态和置信度不视为经济验证或生产准入。",
        "revision": 0,
        "production_eligible": False,
        "paper_supported": False,
        "exit_adapter": None,
        "source_database": None,
        "protocol": "spike-burst-v128-yolo-confirmation-v1",
    },
]


def catalog(root=None) -> list[dict[str, Any]]:
    """Return static strategy descriptors without reading data or loading ML deps."""
    del root  # Reserved for future registry-backed annotations; the allow-list stays static.
    return deepcopy(_CATALOG)


def get_plugin(strategy_id: str) -> Plugin:
    """Return an exact allow-listed plugin or fail closed."""
    try:
        return _PLUGINS[strategy_id]
    except KeyError as exc:
        raise KeyError(strategy_id) from exc


def source_manifest(root):
    """Lazy compatibility export for the create/tick source gate."""
    from yoyo.research_workspace.paper_source import source_manifest as build_manifest

    return build_manifest(root)
