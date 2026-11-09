"""Causal OKX delisting-notice short entries on retired-contract histories.

The study uses the locally archived official OKX notice HTML and its declared
JSON-LD ``datePublished`` timestamp. Each selected notice/instrument gets only
the first UTC 12H period whose open is at or after publication. That period
must contain 12 consecutive actual H1 bars, and its completed EMA20 must be
below EMA50. Entry is the exact next H1 open. The stop is that first 12H
period's high. The native V1.7 VectorBT path runner exits on its fixed stop,
prior-confirmed EMA flip, observed data gap, or source end; a scheduled stale
flag forces a market exit at the last observed H1 open before official delivery.

The delayed comparison enters the same short 12 hours later only when that
exact H1 open exists and the original native path has not exited before it.
It retains the original signal high as its stop. This is a survivor-matched
timing comparison; it does not represent the full notice cohort.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-delist-notice-entry-20261109-v1"
NOTICE_SOURCE = ROOT / "experiments/active/exp-cloud-close-okx-retired-notices-20261109-v1"
PRICE_CACHE = ROOT / "data/research/cloud_close_okx_retired_prices/series"
HOUR_MS = 3_600_000
STEP_12H = 12 * HOUR_MS
FIELDS = ("open", "high", "low", "close")
PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00:00Z"),
    "2025": ("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    "2026_to_source_cutoff": ("2026-01-01T00:00:00Z", "2026-11-08T12:00:00Z"),
}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_json_safe(value), ensure_ascii=False,
                               indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _write_csv(path: Path, frame: pd.DataFrame, *, index: bool = False,
               index_label: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    frame.to_csv(temp, index=index, index_label=index_label,
                 compression="gzip" if path.name.endswith(".gz") else None)
    temp.replace(path)


def _read_jsonld_article(url: str, articles_dir: Path) -> dict:
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    html_path = articles_dir / f"{slug}.html"
    if not html_path.is_file():
        raise FileNotFoundError(f"Cached official notice HTML missing: {html_path}")
    html = html_path.read_text(encoding="utf-8", errors="replace")
    scripts = re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html, re.IGNORECASE | re.DOTALL)
    matches = []
    for script in scripts:
        try:
            parsed = json.loads(script)
        except json.JSONDecodeError:
            continue
        for obj in parsed if isinstance(parsed, list) else [parsed]:
            if not isinstance(obj, dict) or "datePublished" not in obj:
                continue
            page = obj.get("mainEntityOfPage")
            canonical = page.get("@id") if isinstance(page, dict) else page
            if isinstance(canonical, str) and canonical.rstrip("/") == url.rstrip("/"):
                matches.append(obj)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one matching Article JSON-LD for {url}; got {len(matches)}")
    article = matches[0]
    return {"html_path": str(html_path.relative_to(ROOT)),
            "canonical_url": url, "published_raw": article.get("datePublished"),
            "modified_raw": article.get("dateModified"),
            "jsonld_type": article.get("@type")}


def _publication_ms(raw: object) -> tuple[int | None, str]:
    """Accept only timezone-qualified date-time; date-only/naive is excluded."""
    if not isinstance(raw, str) or "T" not in raw:
        return None, "date_only_or_missing"
    try:
        stamp = pd.Timestamp(raw)
    except (TypeError, ValueError):
        return None, "unparseable"
    if stamp.tzinfo is None:
        return None, "timezone_missing"
    return int(stamp.tz_convert("UTC").value // 1_000_000), "exact_timezone_qualified_datetime"


def _selected_rows(config: dict) -> pd.DataFrame:
    source = ROOT / config["inputs"]["crosschecked_lifecycles"]
    rows = pd.read_csv(source)
    required = {"symbol", "notice", "delivery_utc", "selected"}
    if not required.issubset(rows.columns):
        raise ValueError(f"Notice roster schema is missing {sorted(required-set(rows.columns))}")
    selected = rows[rows.selected.astype(str).str.lower().eq("true")].copy()
    selected = selected.sort_values(["symbol", "notice"], kind="stable").reset_index(drop=True)
    if len(selected) != int(config["roster"]["expected_selected_symbols"]):
        raise ValueError(f"Frozen notice roster count changed: {len(selected)}")
    if selected.symbol.nunique() != len(selected):
        raise ValueError("The frozen selected notice roster contains duplicate symbols")
    return selected


def _read_h1(symbol: str, delivery_ms: int, cutoff_ms: int) -> tuple[pd.DataFrame, dict]:
    instrument = f"{symbol}-USDT-SWAP"
    folder = PRICE_CACHE / instrument
    bars_path = folder / "bars1h.csv.gz"
    receipt_path = folder / "source.json"
    month_path = folder / "months.json"
    if not (bars_path.is_file() and receipt_path.is_file() and month_path.is_file()):
        raise FileNotFoundError(f"Missing frozen retired-market source for {instrument}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("instrument") != instrument:
        raise ValueError(f"Instrument identity mismatch in {receipt_path}")
    raw = pd.read_csv(bars_path)
    if not {"time", *FIELDS}.issubset(raw.columns):
        raise ValueError(f"H1 schema invalid in {bars_path}")
    raw = raw[["time", *FIELDS]].copy()
    raw["time"] = raw.time.astype("int64")
    raw = raw[(raw.time < delivery_ms) & (raw.time < cutoff_ms)].copy()
    if raw.time.duplicated().any() or not raw.time.is_monotonic_increasing:
        raise ValueError(f"H1 time index is duplicated or unordered for {instrument}")
    prices = raw[list(FIELDS)].to_numpy(dtype=np.float64)
    if len(raw) and (not np.isfinite(prices).all() or (prices <= 0).any()):
        raise ValueError(f"Nonpositive or nonfinite H1 OHLC for {instrument}")
    if len(raw) and ((raw.low > raw[["open", "close"]].min(axis=1)).any()
                     or (raw.high < raw[["open", "close"]].max(axis=1)).any()
                     or (raw.low > raw.high).any()):
        raise ValueError(f"Invalid H1 OHLC envelope for {instrument}")
    if len(raw) and (raw.time.to_numpy(dtype=np.int64) % HOUR_MS).any():
        raise ValueError(f"H1 timestamps are not UTC-hour aligned for {instrument}")
    months = json.loads(month_path.read_text(encoding="utf-8"))
    month_status = Counter(item.get("status", "unknown") for item in months)
    audit = {"instrument": instrument, "bars_path": str(bars_path.relative_to(ROOT)),
             "source_receipt": str(receipt_path.relative_to(ROOT)),
             "source_hours_before_cutoff": len(raw),
             "source_first_ms": int(raw.time.iloc[0]) if len(raw) else None,
             "source_last_ms": int(raw.time.iloc[-1]) if len(raw) else None,
             "source_receipt_hours_full": receipt.get("hours"),
             "complete_native_minutes_h1_count": receipt.get("from_complete_native_minutes"),
             "supplemental_local_h1_count": receipt.get("supplemental_local_h1"),
             "overlap_disagreement_count": receipt.get("overlap_disagreements"),
             "month_status_counts": dict(month_status)}
    raw = raw.set_index("time")
    raw.index = raw.index.astype("int64")
    raw.index.name = "time"
    return raw, audit


def _aggregate_complete_12h(bars1: pd.DataFrame, delivery_ms: int) -> tuple[pd.DataFrame, dict]:
    if bars1.empty:
        return pd.DataFrame(columns=FIELDS, index=pd.Index([], dtype="int64", name="time")), {
            "complete_periods": 0, "incomplete_periods": 0}
    times = bars1.index.to_numpy(dtype=np.int64)
    values = bars1[list(FIELDS)].to_numpy(dtype=np.float64)
    bucket = times // STEP_12H * STEP_12H
    rows = []
    incomplete = 0
    for key in np.unique(bucket):
        if int(key) + STEP_12H > delivery_ms:
            continue
        ix = np.flatnonzero(bucket == key)
        expected = np.arange(int(key), int(key) + STEP_12H, HOUR_MS, dtype=np.int64)
        if len(ix) != 12 or not np.array_equal(times[ix], expected):
            incomplete += 1
            continue
        rows.append((int(key), float(values[ix[0], 0]), float(values[ix, 1].max()),
                     float(values[ix, 2].min()), float(values[ix[-1], 3])))
    frame = pd.DataFrame(rows, columns=["time", *FIELDS]).set_index("time")
    frame.index = frame.index.astype("int64")
    frame.index.name = "time"
    return frame, {"complete_periods": len(frame), "incomplete_periods": incomplete}


def _candidate_event(row: pd.Series, article: dict, publication_ms: int,
                     delivery_ms: int, bars12: pd.DataFrame, ema: pd.DataFrame,
                     bars1: pd.DataFrame, q: np.ndarray, bar_ms: np.ndarray,
                     delivery_index: int, delivery_stale_preexisting: bool,
                     config: dict) -> tuple[dict | None, dict]:
    symbol = str(row.symbol)
    start_ms = ((publication_ms + STEP_12H - 1) // STEP_12H) * STEP_12H
    end_ms = start_ms + STEP_12H
    expected = np.arange(start_ms, end_ms, HOUR_MS, dtype=np.int64)
    hourly = bars1.index.to_numpy(dtype=np.int64)
    observed = int(np.searchsorted(hourly, end_ms, side="left") - np.searchsorted(hourly, start_ms, side="left"))
    record = {
        "source_row_number": int(row.name) + 1, "symbol": symbol,
        "notice_url": str(row.notice), "article_html": article["html_path"],
        "date_published_raw": article["published_raw"],
        "date_modified_audit_only": article["modified_raw"],
        "publication_bound_type": "exact_timezone_qualified_datetime",
        "publication_ms": publication_ms,
        "publication_utc": pd.Timestamp(publication_ms, unit="ms", tz="UTC").isoformat(),
        "delivery_utc": pd.Timestamp(delivery_ms, unit="ms", tz="UTC").isoformat(),
        "delivery_ms": delivery_ms,
        "first_12h_open_ms": start_ms, "first_12h_close_ms": end_ms,
        "first_12h_open_utc": pd.Timestamp(start_ms, unit="ms", tz="UTC").isoformat(),
        "first_12h_close_utc": pd.Timestamp(end_ms, unit="ms", tz="UTC").isoformat(),
        "first_12h_expected_h1_count": 12, "first_12h_observed_h1_count": observed,
        "first_12h_complete": False, "ema_ready": None,
        "ema20": None, "ema50": None, "signal_high": None,
        "entry_ms": None, "entry_price": None, "risk_distance": None,
        "status": None, "reason": None,
        "delayed_status": "not_checked", "delayed_reason": "",
    }
    if end_ms > delivery_ms:
        record.update(status="excluded", reason="first_confirmation_after_delivery")
        return None, record
    if observed != 12 or not np.array_equal(hourly[np.searchsorted(hourly, start_ms):
                                                   np.searchsorted(hourly, end_ms)], expected):
        record.update(status="excluded", reason="first_post_notice_12h_incomplete")
        return None, record
    if start_ms not in bars12.index:
        raise AssertionError("Complete 12H confirmation missing from aggregate")
    record["first_12h_complete"] = True
    bar_idx = int(bars12.index.get_loc(start_ms))
    ready = bool(ema.ready.iloc[bar_idx])
    record.update(ema_ready=ready, ema20=float(ema.ema20.iloc[bar_idx]),
                  ema50=float(ema.ema50.iloc[bar_idx]), signal_high=float(bars12.high.iloc[bar_idx]))
    if not ready:
        record.update(status="excluded", reason="ema50_not_ready_after_native_gap")
        return None, record
    if not float(ema.ema20.iloc[bar_idx]) < float(ema.ema50.iloc[bar_idx]):
        record.update(status="excluded", reason="first_confirmation_not_bearishly_aligned")
        return None, record
    entry_ms = end_ms
    entry_idx = int(np.searchsorted(bar_ms, entry_ms))
    if entry_idx >= len(bar_ms) or int(bar_ms[entry_idx]) != entry_ms:
        record.update(status="excluded", reason="no_exact_confirmation_h1_open")
        return None, record
    if entry_idx >= delivery_index:
        record.update(status="excluded", reason="no_observed_pre_delivery_open_after_entry")
        return None, record
    entry_price = float(bars1.open.iloc[entry_idx])
    stop_price = float(bars12.high.iloc[bar_idx])
    risk = stop_price - entry_price  # short: stop must be above executable entry.
    if not np.isfinite(risk) or risk <= 0:
        record.update(status="excluded", reason="nonpositive_signal_high_stop_distance")
        return None, record
    event_id = f"{symbol}|{urlparse(str(row.notice)).path.rsplit('/', 1)[-1]}|actual"
    event = {
        "event_id": event_id,
        "parent_id": f"{symbol}|{urlparse(str(row.notice)).path.rsplit('/', 1)[-1]}",
        "source_row_number": int(row.name) + 1, "symbol": symbol,
        "week": pd.Timestamp(publication_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
        "side": -1, "is_control": False, "entry_type": "first_notice_confirmation",
        "notice_url": str(row.notice), "article_html": article["html_path"],
        "date_published_raw": article["published_raw"],
        "date_modified_audit_only": article["modified_raw"],
        "publication_ms": publication_ms, "delivery_ms": delivery_ms,
        "first_12h_open_ms": start_ms, "confirmation_ms": end_ms,
        "entry_i": entry_idx, "entry_ms": entry_ms,
        "entry_price": entry_price, "stop_price": stop_price,
        "risk_distance": risk, "risk_fraction": risk / entry_price,
        "signal_ema20": float(ema.ema20.iloc[bar_idx]),
        "signal_ema50": float(ema.ema50.iloc[bar_idx]),
        "delivery_exit_ms": int(bar_ms[delivery_index]),
        "delivery_exit_i": delivery_index,
        "delivery_stale_preexisting": delivery_stale_preexisting,
    }
    end_i, upper_reason = engine._safe_path_end(entry_idx, -1, q, bar_ms)
    event["end_i"] = end_i
    event["path_upper_bound_reason"] = upper_reason
    record.update(status="eligible", reason="first_confirmation_bearish_and_executable",
                  entry_ms=entry_ms,
                  entry_price=entry_price, risk_distance=risk,
                  signal_event_id=event_id,
                  delivery_exit_ms=int(bar_ms[delivery_index]),
                  delivery_exit_utc=pd.Timestamp(bar_ms[delivery_index], unit="ms", tz="UTC").isoformat())
    return event, record


def _enrich_path(row: dict, event: dict) -> dict:
    result = dict(row)
    result.update({key: event.get(key) for key in (
        "entry_type", "notice_url", "article_html", "date_published_raw",
        "date_modified_audit_only", "publication_ms", "delivery_ms",
        "first_12h_open_ms", "confirmation_ms", "signal_ema20", "signal_ema50",
        "delivery_exit_ms", "delivery_stale_preexisting")})
    if (result.get("exit_reason") == "stale_12h_markout"
            and result.get("exit_bar_ms") == event.get("delivery_exit_ms")
            and result.get("exit_phase") == "open"):
        result["exit_reason"] = "contract_delivery_preopen"
        result["delivery_exit_fired"] = True
    else:
        result["delivery_exit_fired"] = False
    return result


def _simulate(events: list[dict], work: dict[str, dict], config: dict) -> tuple[list[dict], dict]:
    rows = []
    detail = {}
    for symbol in sorted({event["symbol"] for event in events}):
        batch = [event for event in events if event["symbol"] == symbol]
        prepared = work[symbol]
        path_rows, meta = engine._simulate_symbol(batch, prepared["q"], prepared["bar_ms"],
                                                   (0.0,), int(config["execution"]["path_batch_size"]))
        for event, path in zip(batch, path_rows):
            if path.get("event_id") != event["event_id"]:
                raise AssertionError("VectorBT path row order lost event identity")
            enriched = _enrich_path(path, event)
            rows.append(enriched)
            detail[event["event_id"]] = enriched
        detail[f"_meta:{symbol}"] = meta
    return rows, detail


@lru_cache(maxsize=68)
def _quotes(symbol: str) -> tuple[np.ndarray, np.ndarray]:
    path = PRICE_CACHE / f"{symbol}-USDT-SWAP" / "bars1h.csv.gz"
    frame = pd.read_csv(path, usecols=["time", *FIELDS])
    return frame.time.to_numpy(dtype=np.int64), frame[list(FIELDS)].to_numpy(dtype=np.float64)


def _delayed_events(actual_events: list[dict], actual_paths: dict[str, dict],
                    work: dict[str, dict], config: dict,
                    candidate_audit: dict[str, dict]) -> tuple[list[dict], dict]:
    delay_ms = int(config["comparison"]["delay_hours"]) * HOUR_MS
    delayed, reasons = [], Counter()
    for actual in sorted(actual_events, key=lambda e: (e["entry_ms"], e["symbol"])):
        audit = candidate_audit[actual["parent_id"]]
        target_ms = int(actual["entry_ms"] + delay_ms)
        actual_path = actual_paths[actual["event_id"]]
        if target_ms >= int(actual["delivery_exit_ms"]):
            reason = "delayed_open_not_before_last_pre_delivery_open"
        else:
            bars = work[actual["symbol"]]["bar_ms"]
            index = int(np.searchsorted(bars, target_ms))
            if index >= len(bars) or int(bars[index]) != target_ms:
                reason = "no_exact_delayed_h1_open"
            elif int(actual_path["exit_bar_ms"]) < target_ms:
                reason = "original_path_exited_before_delayed_open"
            elif (int(actual_path["exit_bar_ms"]) == target_ms
                  and actual_path["exit_phase"] == "open"):
                reason = "original_path_exited_at_delayed_open"
            else:
                price = float(work[actual["symbol"]]["bars1"].open.iloc[index])
                risk = float(actual["stop_price"] - price)
                if risk <= 0 or not np.isfinite(risk):
                    reason = "nonpositive_fixed_signal_high_risk_at_delay"
                else:
                    end_i, upper_reason = engine._safe_path_end(index, -1,
                                        work[actual["symbol"]]["q"], bars)
                    event = dict(actual)
                    event.update(event_id=actual["parent_id"] + "|delay12h",
                                 is_control=True, entry_type="same_notice_delay_12h",
                                 entry_i=index, entry_ms=target_ms, entry_price=price,
                                 risk_distance=risk, risk_fraction=risk/price,
                                 end_i=end_i, path_upper_bound_reason=upper_reason,
                                 delay_hours=int(config["comparison"]["delay_hours"]),
                                 original_entry_ms=int(actual["entry_ms"]))
                    delayed.append(event)
                    reason = "matched_survived_to_exact_delayed_open"
        audit["delayed_status"] = "matched" if reason == "matched_survived_to_exact_delayed_open" else "unmatched"
        audit["delayed_reason"] = reason
        audit["delayed_entry_ms"] = target_ms
        reasons[reason] += 1
    return delayed, dict(reasons)


def _account(events: list[dict], label: str, slip: float, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    columns = ["event_id", "parent_id", "source_row_number", "symbol", "week", "side",
               "is_control", "entry_ms", "exit_bar_ms", "exit_phase", "entry_price",
               "exit_price", "exit_fee_rate"]
    frame = pd.DataFrame(events)
    if frame.empty:
        frame = pd.DataFrame(columns=columns)
    else:
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"Account input missing fields: {sorted(missing)}")
        if frame.duplicated("event_id").any():
            raise ValueError("Duplicate account event id")
        if not frame.exit_phase.isin(["open", "intrabar", "close"]).all():
            raise ValueError("Unexpected native exit phase")
        if not np.isclose(frame.entry_fee_rate.astype(float), engine.TAKER).all():
            raise ValueError("Entry fee differs from frozen OKX taker fee")
    data = wallet.prepare(frame, slots=int(config["account"]["slots"]),
                          start=wallet.START, end=wallet.END, quote_loader=_quotes)
    roster, equity = wallet.account(data, slip=slip,
                                    exposure=float(config["account"]["exposure"]))
    metrics = {period: wallet.summarize(roster, equity, *limits)
               for period, limits in PERIODS.items()}
    metadata = {"cohort": label, "input_events": len(frame),
                "admitted": len(data["selected"]), "funded": len(roster),
                "skips": data["skips"], "unquoted_position_hours": data["gap_marks"],
                "initial_cash_usdt": float(config["account"]["initial_cash_usdt"]),
                "slots": int(config["account"]["slots"]),
                "exposure": float(config["account"]["exposure"]),
                "extra_market_slippage_per_fill": slip}
    return roster, equity, {"metrics": metrics, "admission": metadata}


def _prepare(config: dict) -> tuple[list[dict], dict[str, dict], list[dict], dict, dict]:
    roster = _selected_rows(config)
    articles_dir = ROOT / config["inputs"]["official_article_html"]
    cutoff = pd.Timestamp(config["account"]["source_cutoff_utc"])
    if cutoff.tzinfo is None:
        raise ValueError("Source cutoff must include timezone")
    cutoff_ms = int(cutoff.tz_convert("UTC").value // 1_000_000)
    work, actual_events, candidate_rows, timing_rows = {}, [], [], []
    source_counts, publication_counts = Counter(), Counter()
    for pos, row in roster.iterrows():
        row.name = pos
        article = _read_jsonld_article(str(row.notice), articles_dir)
        pub_ms, publication_kind = _publication_ms(article["published_raw"])
        publication_counts[publication_kind] += 1
        if pub_ms is None:
            delivery_ms = int(pd.Timestamp(row.delivery_utc).value // 1_000_000)
            candidate_rows.append({"source_row_number": pos+1, "symbol": row.symbol,
                "notice_url": row.notice, "article_html": article["html_path"],
                "date_published_raw": article["published_raw"],
                "date_modified_audit_only": article["modified_raw"],
                "status": "excluded", "reason": publication_kind,
                "delayed_status": "not_checked", "delayed_reason": ""})
            timing_rows.append({**article, "symbol": row.symbol, "publication_kind": publication_kind,
                                "publication_utc": None, "delivery_utc": row.delivery_utc})
            continue
        if pd.isna(row.delivery_utc):
            raise ValueError(f"Selected row lacks official delivery time: {row.symbol}")
        delivery_ms = int(pd.Timestamp(row.delivery_utc).value // 1_000_000)
        if delivery_ms <= pub_ms:
            raise ValueError(f"Delisting precedes notice publication: {row.symbol}")
        bars1, source_audit = _read_h1(str(row.symbol), delivery_ms, cutoff_ms)
        bars12, aggregate_audit = _aggregate_complete_12h(bars1, delivery_ms)
        if bars1.empty or bars12.empty:
            candidate_rows.append({"source_row_number": pos+1, "symbol": row.symbol,
                "notice_url": row.notice, "article_html": article["html_path"],
                "date_published_raw": article["published_raw"],
                "date_modified_audit_only": article["modified_raw"],
                "status": "excluded", "reason": "no_pre_delivery_complete_history",
                "source_hours": len(bars1), **aggregate_audit,
                "delayed_status": "not_checked", "delayed_reason": ""})
            timing_rows.append({**article, "symbol": row.symbol, "publication_kind": publication_kind,
                                "publication_utc": pd.Timestamp(pub_ms, unit="ms", tz="UTC").isoformat(),
                                "delivery_utc": row.delivery_utc})
            source_counts.update(source_audit["month_status_counts"])
            continue
        q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
        if len(bar_ms) != len(bars1) or q.shape != (len(bars1), 7):
            raise AssertionError(f"Native V1.7 path arrays misaligned for {row.symbol}")
        natural_stale = bool(q[-1, 6] > 0)
        delivery_idx = len(bar_ms) - 1
        q = q.copy()
        q[delivery_idx, 6] = 1.0
        work[str(row.symbol)] = {"bars1": bars1, "bars12": bars12, "q": q,
                                 "bar_ms": bar_ms, "delivery_index": delivery_idx,
                                 "natural_stale_at_delivery": natural_stale,
                                 "source_audit": source_audit,
                                 "aggregate_audit": aggregate_audit,
                                 "engine_audit": prepared["audit"]}
        event, record = _candidate_event(row, article, pub_ms, delivery_ms, bars12,
                    engine._ema_points_12h(bars12.reset_index().to_dict("records")),
                    bars1, q, bar_ms, delivery_idx, natural_stale, config)
        record.update({"source_hours": len(bars1),
                       "complete_12h_periods": aggregate_audit["complete_periods"],
                       "incomplete_12h_periods": aggregate_audit["incomplete_periods"],
                       "native_gap_count_h1": int(np.count_nonzero(np.diff(bar_ms) > HOUR_MS)),
                       "source_tail_ms": int(bar_ms[-1]),
                       "source_tail_utc": pd.Timestamp(bar_ms[-1], unit="ms", tz="UTC").isoformat(),
                       "source_month_status_counts": json.dumps(source_audit["month_status_counts"], sort_keys=True),
                       "supplemental_local_h1_count_full_receipt": source_audit["supplemental_local_h1_count"],
                       "natural_stale_at_delivery_open": natural_stale})
        candidate_rows.append(record)
        timing_rows.append({**article, "symbol": row.symbol,
                            "publication_kind": publication_kind,
                            "publication_utc": pd.Timestamp(pub_ms, unit="ms", tz="UTC").isoformat(),
                            "publication_ms": pub_ms, "delivery_utc": row.delivery_utc,
                            "delivery_ms": delivery_ms})
        source_counts.update(source_audit["month_status_counts"])
        if event is not None:
            actual_events.append(event)
    audit_by_parent = {f"{r['symbol']}|{urlparse(r['notice_url']).path.rsplit('/', 1)[-1]}": r
                       for r in candidate_rows if r.get("notice_url")}
    metadata = {"selected_rows": len(roster), "selected_unique_symbols": roster.symbol.nunique(),
                "unique_notices": roster.notice.nunique(),
                "publication_field_counts": dict(publication_counts),
                "price_month_status_counts_over_selected_sources": dict(source_counts),
                "hours_read_before_delivery_and_cutoff": int(sum(v["source_audit"]["source_hours_before_cutoff"]
                                                                   for v in work.values())),
                "summed_source_hours_are_per_symbol_not_unique_market_hours": True,
                "market_source_receipts": [v["source_audit"] for v in work.values()]}
    return actual_events, work, candidate_rows, audit_by_parent, {
        "notice_timing": timing_rows, "source_metadata": metadata,
        "cutoff_ms": cutoff_ms}


def _save_accounts(actual_paths: list[dict], delayed_paths: list[dict], config: dict) -> dict:
    paired_parents = {event["parent_id"] for event in delayed_paths}
    groups = {
        "actual_all": actual_paths,
        "actual_paired": [e for e in actual_paths if e["parent_id"] in paired_parents],
        "delayed_12h": delayed_paths,
    }
    outputs, result = {}, {}
    for cohort, events in groups.items():
        outputs[cohort] = {}
        for stress_name, slip in (("base", 0.0), ("extra_5bp", float(config["account"]["stress_slippage_per_market_fill"]))):
            funded, equity, metrics = _account(events, cohort, slip, config)
            key = f"{cohort}_{stress_name}"
            _write_csv(EXP / f"funded_{key}.csv.gz", funded)
            _write_csv(EXP / f"hourly_{key}.csv.gz", equity, index=True, index_label="hour_end_utc")
            outputs[cohort][stress_name] = metrics
            result[key] = metrics
            print(f"{key}: funded={metrics['admission']['funded']} "
                  f"all_final={metrics['metrics']['all']['final_usdt']:.2f} "
                  f"all_dd_bound={metrics['metrics']['all']['dd_bound_pct']:.2f}%", flush=True)
    return result


def run(config: dict, *, preflight: bool = False) -> dict:
    if (EXP / "summary.json").exists() and not preflight:
        raise FileExistsError(f"Frozen output already exists: {EXP/'summary.json'}")
    if not np.isclose(float(config["account"]["taker_fee_rate"]), engine.TAKER):
        raise ValueError("Config taker fee differs from frozen native path engine")
    if not np.isclose(float(config["account"]["taker_fee_rate"]), wallet.TAKER):
        raise ValueError("Config taker fee differs from shared-cash wallet")
    actual_events, work, candidate_audit, audit_by_parent, meta = _prepare(config)
    eligible = sum(row.get("status") == "eligible" for row in candidate_audit)
    print(f"source preflight: notices={len(candidate_audit)}, eligible={eligible}, "
          f"symbols_with_paths={len(work)}", flush=True)
    if preflight:
        return {"candidate_audit": candidate_audit, **meta}
    actual_rows, actual_path_map = _simulate(actual_events, work, config)
    delayed_events, delay_reasons = _delayed_events(actual_events, actual_path_map, work,
                                                     config, audit_by_parent)
    delayed_rows, _ = _simulate(delayed_events, work, config)
    path_rows = actual_rows + delayed_rows
    for row in candidate_audit:
        if row.get("status") == "eligible":
            event_id = row.get("signal_event_id")
            path = actual_path_map.get(event_id)
            if path is not None:
                row.update(actual_exit_ms=path.get("exit_bar_ms"),
                           actual_exit_utc=path.get("exit_time_utc"),
                           actual_exit_phase=path.get("exit_phase"),
                           actual_exit_reason=path.get("exit_reason"),
                           actual_path_status=path.get("status"))
    path_frame = pd.DataFrame(path_rows)
    if path_frame.empty:
        path_frame = pd.DataFrame(columns=["event_id", "parent_id", "symbol", "side", "is_control",
                                           "entry_ms", "exit_bar_ms", "exit_phase", "exit_reason", "status"])
    if not path_frame.empty:
        if path_frame.duplicated(["event_id", "tp_r"]).any():
            raise ValueError("Duplicate native path event rows")
        if not path_frame.status.eq("executed").all():
            raise ValueError("All eligible notice paths must be priced by VectorBT")
        if not path_frame.entry_fee_rate.astype(float).eq(engine.TAKER).all():
            raise ValueError("Notice entry path fee is not OKX taker")
        if not path_frame.exit_fee_rate.astype(float).eq(engine.TAKER).all():
            raise ValueError("Notice exit path fee is not OKX taker")
        if (path_frame.exit_bar_ms < path_frame.entry_ms).any():
            raise ValueError("Path exit precedes its executable entry")
        for event in actual_events:
            hit = path_frame[path_frame.event_id.eq(event["event_id"])]
            if len(hit) != 1:
                raise ValueError(f"Actual event does not have one VectorBT path: {event['event_id']}")
        for event in delayed_events:
            hit = path_frame[path_frame.event_id.eq(event["event_id"])]
            if len(hit) != 1 or not bool(hit.iloc[0].is_control):
                raise ValueError(f"Delayed event does not have one VectorBT path: {event['event_id']}")
    accounts = _save_accounts(actual_rows, delayed_rows, config)
    _write_csv(EXP / "notice_timing.csv", pd.DataFrame(meta["notice_timing"]))
    _write_csv(EXP / "candidate_audit.csv", pd.DataFrame(candidate_audit))
    _write_csv(EXP / "paths.csv.gz", path_frame)
    summary = {
        "experiment_id": config["experiment_id"], "status": "completed",
        "vectorbt_version": engine.vbt.__version__, "source_scope": config["scope"],
        "construction": config["construction"], "account_config": config["account"],
        "source_metadata": meta["source_metadata"],
        "candidate_counts": {"notice_symbols": len(candidate_audit),
            "first_12h_incomplete": sum(r.get("reason") == "first_post_notice_12h_incomplete" for r in candidate_audit),
            "ema_not_ready": sum(r.get("reason") == "ema50_not_ready_after_native_gap" for r in candidate_audit),
            "first_confirmation_not_bearish": sum(r.get("reason") == "first_confirmation_not_bearishly_aligned" for r in candidate_audit),
            "actual_candidates": len(actual_events), "delayed_matched": len(delayed_events),
            "delayed_unmatched_reasons": delay_reasons},
        "candidate_events": len(actual_events),
        "actual_path_rows": len(actual_rows), "delayed_path_rows": len(delayed_rows),
        "native_path_meta": {key: value for key, value in actual_path_map.items() if key.startswith("_meta:")},
        "account_results": accounts,
        "limits": ["The 68 instruments are a partial notice-directory roster, not a full-market census.",
                   "Published time is the official cached Article JSON-LD datePublished field; dateModified is audit-only.",
                   "The delayed comparison is conditional on the original position surviving to the exact delayed open.",
                   "Retired-price H1 paths are historical OHLC proxies; no funding, liquidation, or market-depth model is included."],
    }
    _write_json(EXP / "summary.json", summary)
    print(f"completed: actual={len(actual_events)}, matched_delay={len(delayed_events)}, "
          f"paths={len(path_frame)}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("preflight", "all"), default="all")
    args = parser.parse_args()
    config = json.loads((EXP / "config.json").read_text(encoding="utf-8"))
    run(config, preflight=args.phase == "preflight")


if __name__ == "__main__":
    main()
