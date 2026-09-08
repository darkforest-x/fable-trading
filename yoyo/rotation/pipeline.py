"""As-of rotation observations, explicit missingness, and illustrative risk.

Inputs: closed daily/4h/15m candles from the injected public provider, plus
time-aware human event metadata. This orchestrator imports L1 and L2; neither
layer imports the other. No model, outcome label, backtest or order API is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import math
from statistics import median
import time
from typing import Any, Mapping, Optional

from yoyo.contracts.costs import LEGACY_P0_ROUND_TRIP, SPOT_TAKER
from yoyo.contracts.rotation import (SCHEMA, RotationConfig, RotationError, authorize,
                                    digest, iso, now_utc, source_identity, utc)
from yoyo.data.rotation_features import daily_metrics, market_regime
from yoyo.layers.l1_detection.rotation_setups import detect_setup
from yoyo.layers.l2_judgment.rotation_judgment import rank_candidates
from yoyo.rotation.metadata import asset_context
from yoyo.rotation.providers import BinanceProvider, SyntheticProvider
from yoyo.rotation.derivatives import fetch_context

# Imported Python functions remain cached in a long-running server. A later
# disk commit must not let old in-memory functions claim the new source hash.
LOADED_SOURCE_HASH = source_identity()["source_hash"]


def risk_example(candidate: dict, config: RotationConfig, regime: dict) -> dict:
    """Every 10,000 notional-account units, structure distance plus spot cost.

    SPOT_TAKER is the unchanged canonical route assumption. The older 20bp
    reporting benchmark is shown separately; neither is an execution quote.
    Fees/slippage are an allowance, not a worst-case loss guarantee. Existing
    holdings are unknown, so examples are not combined into a portfolio order.
    """
    setup = candidate.get("setup", {})
    entry, stop = setup.get("entry_reference"), setup.get("stop_reference")
    result = {"status": "blocked", "entry_reference": entry, "stop_reference": stop,
              "distance_pct": None, "notional_per_10000": None,
              "max_loss_per_10000": None, "cost_pct": SPOT_TAKER,
              "legacy_reporting_cost_pct": LEGACY_P0_ROUND_TRIP,
              "cost_route": "SPOT_TAKER", "portfolio_checked": False,
              "execution_eligible": False, "reasons": []}
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (entry, stop)) or stop >= entry:
        result["reasons"].append("没有有效的入场/结构失效参考，无法计算风险示例")
        return result
    distance = (entry - stop) / entry
    amount = min(10000 * config.illustrative_risk_fraction / (distance + SPOT_TAKER),
                 10000 * config.maximum_illustrative_allocation)
    result.update(distance_pct=distance, notional_per_10000=round(amount, 2),
                  max_loss_per_10000=round(amount * (distance + SPOT_TAKER), 2))
    if candidate.get("event_risk") != "clear":
        result["reasons"].append("事件与供给覆盖尚未审核或存在阻断事件")
    if not candidate.get("liquidity_eligible"):
        result["reasons"].append("流动性或资产类别不合格")
    if regime.get("state") in {"defensive", "unknown"}:
        result["reasons"].append("市场环境防御或基准覆盖不足")
    if setup.get("state") not in {"breakout", "pullback"}:
        result["reasons"].append("当前不处于已确认突破或回踩观察状态")
    if candidate.get("trigger", {}).get("state") not in {"watch", "breakout", "pullback", "extended"}:
        result["reasons"].append("15分钟观察缺失、未知或结构失效，风险示例暂不可用")
    if not result["reasons"]:
        result["status"] = "illustration"
    result["reasons"].append("仅按每1万资金示例；未计既有持仓相关性，不构成下单计划，跳空可超出计划损失")
    return result


def _clean(value: Any) -> Any:
    """Strict JSON, never display an unavailable value as zero."""
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_clean(v) for v in value]
    if hasattr(value, "item"):
        return _clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, datetime):
        return iso(value)
    return value


def run_scan(config: RotationConfig, *, catalog: Optional[dict] = None,
             receipt: Optional[dict] = None, provider: Any = None,
             as_of: Any = None, wall_clock=now_utc) -> dict:
    """Construct an observation only after the configuration/time permission gate."""
    started = time.monotonic()
    cutoff = utc(as_of if as_of is not None else wall_clock() if config.mode == "live_observation" else config.as_of)
    identity = source_identity()
    if identity["source_hash"] != LOADED_SOURCE_HASH:
        raise RotationError("observer source changed since process startup; restart required")
    policy = authorize(config, cutoff, source_hash=identity["source_hash"], receipt=receipt, now=wall_clock())
    catalog = catalog or {"schema_version": 1, "assets": [], "events": [], "reviews": []}

    def guard():
        authorize(config, cutoff, source_hash=identity["source_hash"], receipt=receipt, now=wall_clock())

    if provider is None:
        if config.mode == "synthetic_demo":
            factory = SyntheticProvider
        elif config.venue == "okx":
            from yoyo.rotation.okx_provider import OKXProvider
            factory = OKXProvider
        else:
            factory = BinanceProvider
        provider = factory(guard=guard)
    elif config.mode == "synthetic_demo" and not isinstance(provider, SyntheticProvider):
        raise RotationError("synthetic mode must not fetch real data")
    if isinstance(provider, SyntheticProvider) and config.mode != "synthetic_demo":
        raise RotationError("synthetic provider cannot impersonate historical or live data")
    symbols, exclusions = list(config.symbols), []
    selection_basis = "明确指定的工程诊断样本；不代表当时完整市场，存在选样局限"
    selection_at = iso(cutoff)
    if config.universe_mode == "exchange_spot":
        symbols, exclusions, selection_at = provider.discover(maximum=config.max_symbols)
        cutoff = utc(selection_at)
        policy = authorize(config, cutoff, source_hash=identity["source_hash"], receipt=receipt, now=wall_clock())
        selection_basis = "当前现货成员按冻结时点过去24小时quoteVolume排序；非涨幅榜；不是历史成员重建"
    if not symbols:
        raise RotationError("no symbols in the observed universe")

    frames, errors = {}, []
    requested = len(symbols)
    selected_symbols = []
    contexts = {}
    for symbol in symbols:
        context = asset_context(symbol, catalog, as_of=cutoff)
        contexts[symbol] = context
        if context["asset_type"] == "non_crypto":
            exclusions.append({"symbol": symbol, "reason": "non_crypto_asset"})
        else:
            selected_symbols.append(symbol)
    jobs = [(s, t, limit) for s in selected_symbols for t, limit in
            (("1d", config.daily_bars), ("4h", config.setup_bars), ("15m", config.trigger_bars))]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(provider.candles, s, t, as_of=cutoff, limit=n): (s, t)
                   for s, t, n in jobs}
        for future in as_completed(futures):
            symbol, interval = futures[future]
            try:
                frames[(symbol, interval)] = future.result()
            except Exception as exc:  # provider boundary, keep the coverage loss visible
                errors.append({"symbol": symbol, "stage": interval, "message": str(exc)})

    metrics = {}
    for symbol in selected_symbols:
        frame = frames.get((symbol, "1d"))
        if frame is not None:
            try:
                metrics[symbol] = daily_metrics(frame, as_of=cutoff)
            except (ValueError, TypeError) as exc:
                errors.append({"symbol": symbol, "stage": "daily_metrics", "message": str(exc)})
    observed = sum(m.get("state") == "ok" for m in metrics.values())
    if not observed:
        raise RotationError("no valid daily coverage; failed acquisition is not a zero-signal scan: " +
                            "; ".join(e["symbol"] + ": " + e["message"] for e in errors[:3]))
    regime = market_regime(metrics)
    candidates = []
    for symbol in selected_symbols:
        if symbol in {"BTCUSDT", "ETHUSDT"}:
            continue
        context = contexts[symbol]
        daily = metrics.get(symbol, {"state": "insufficient_data", "reasons": ["日线数据不可用"]})
        states = {}
        for interval, key in (("4h", "setup"), ("15m", "trigger")):
            frame = frames.get((symbol, interval))
            try:
                states[key] = detect_setup(frame, as_of=cutoff, interval=interval,
                                           lookback=config.lookback, volume_multiple=config.volume_multiple) if frame is not None else {
                                               "state": "insufficient_data", "reasons": [interval + "数据不可用"]}
            except (ValueError, TypeError) as exc:
                errors.append({"symbol": symbol, "stage": key, "message": str(exc)})
                states[key] = {"state": "insufficient_data", "reasons": [str(exc)]}
        quote_volume = daily.get("median_quote_volume_30d")
        liquidity_ok = (context["asset_type"] == "crypto" and isinstance(quote_volume, (int, float)) and
                        math.isfinite(quote_volume) and quote_volume >= config.minimum_daily_quote_volume)
        row = {"symbol": symbol, **context, "daily": daily, **states,
               "liquidity_eligible": liquidity_ok, "reasons": [],
               "derivatives": {"status": "not_collected", "funding_rate": None,
                               "interval_hours": None, "open_interest": None, "observed_at": None,
                               "reason": "现货扫描未取得同一时点的衍生品快照；不以当前数据回填历史"}}
        candidates.append(row)
    ranked = rank_candidates(candidates, regime=regime)
    # Received after ranking, displayed with its own event time, never a feature.
    # No spot-to-perpetual multiplier mapping or cross-venue substitution.
    if config.mode == "live_observation" and config.venue == "binance":
        for candidate in ranked[:config.derivative_context_limit]:
            candidate["derivatives"] = fetch_context(candidate["symbol"], guard=guard)
            candidate["derivatives"]["use"] = "auxiliary_only_not_used_in_score"
    for candidate in ranked:
        candidate["risk"] = risk_example(candidate, config, regime)
    sectors = []
    for sector in sorted({c["sector"] for c in ranked}):
        rows = [c for c in ranked if c["sector"] == sector and c["daily"].get("state") == "ok"]
        relative = [c.get("rs_30d") for c in rows if isinstance(c.get("rs_30d"), (int, float))]
        sectors.append({"sector": sector, "count": len(rows),
                        "strong_count": sum(x > 0 for x in relative),
                        "median_rs_30d": median(relative) if relative else None})
    inputs = {s + ":" + t: frame.attrs.get("input_digest") or digest({"index": [iso(x) for x in frame.index],
              "rows": frame.to_dict("records")}) for (s, t), frame in sorted(frames.items())}
    guard()
    if source_identity()["source_hash"] != identity["source_hash"]:
        raise RotationError("observer source changed during scan; result not persisted")
    body = _clean({"schema_version": SCHEMA, "as_of": iso(cutoff), "mode": config.mode,
                  "venue": config.venue,
                  "config_hash": config.config_hash, "source_hash": identity["source_hash"],
                  "catalog_hash": digest(catalog), "input_hashes": inputs,
                  "status": "partial" if errors else "ok", "policy": policy,
                  "universe": {"requested": requested, "observed": observed,
                               "eligible": sum(c["liquidity_eligible"] for c in ranked),
                               "exclusions": exclusions, "selection_basis": selection_basis,
                               "selection_at": selection_at, "symbols": symbols},
                  "regime": regime, "benchmarks": metrics, "sectors": sectors,
                  "candidates": ranked, "errors": sorted(errors, key=lambda e: (e["symbol"], e["stage"]))})
    # Receipt timestamps and wall duration are provenance, not content identity.
    body["scan_id"] = digest(body)
    body["generated_at"] = iso(wall_clock())
    body["sources"] = sorted(getattr(provider, "receipts", []), key=lambda x: x["url"])
    body["duration_seconds"] = round(time.monotonic() - started, 3)
    body["financial_validation"] = {"status": "not_performed", "auc": None,
                                     "net_return": None, "permutation_p": None,
                                     "reason": "工程观察系统；未作收益实验或生产裁决"}
    return body
