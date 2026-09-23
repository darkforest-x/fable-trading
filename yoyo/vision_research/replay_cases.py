"""Read existing, receipt-bound signal outcomes as historical replay cases.

Sources are the frozen V9 all-candidate study and V12.8 original serial trades.
Their net R, costs and exits are read verbatim, never recomputed or relabelled
as visual gold. Only authenticated source OHLC at/before the selected cursor
reaches the chart; outcome fields live in a separate, explicit disclosure.
V12.8 uses its original complete-5m-bucket aggregation, not another exchange.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import threading
from pathlib import Path

import pandas as pd

from .replay_data import LocalReplayHistory, SourceError

BUCKETS = ("all", "ge3", "ge5", "gt10", "loss", "other", "open")
V9 = "spike-v9-candidates"
V128 = "spike-v128-trades"
V9_PATH = "experiments/active/exp-spike-10r-discovery-20260921-v3/dataset_v1"
V9_RAW = "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3/streams"
V128_PATH = "experiments/active/exp-spike-v128-recent-20260923-v1"
PUBLIC_KEYS = ("id", "symbol", "timeframe", "side", "signal_close_ms", "family", "source_label")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verified_bytes(path, expected):
    raw = path.read_bytes()
    if not expected or digest(raw) != expected:
        raise SourceError("case_source_changed", "历史案例来源校验不一致，已停止读取；原始记录需要核对。")
    return raw


def read_json(path, expected=None):
    return json.loads(verified_bytes(path, expected) if expected else path.read_bytes())


def csv_rows(raw):
    try:
        return pd.read_csv(io.BytesIO(raw), compression="gzip", keep_default_na=False, dtype=str).to_dict("records")
    except pd.errors.EmptyDataError:
        return []


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def truth(value):
    return str(value).lower() in {"true", "1"}


def timestamp(value):
    parsed = pd.Timestamp(value)
    if pd.isna(parsed) or parsed.tzinfo is None:
        raise SourceError("case_clock", "历史信号缺少明确时区，无法定位。")
    return int(parsed.value // 1_000_000)


def timeframe(minutes):
    return f"{minutes // 60}H" if minutes % 60 == 0 else f"{minutes}m"


def in_bucket(case, bucket):
    outcome = case["outcome"]
    r = outcome["net_r"]
    closed = outcome["status"] == "closed" and r is not None
    return {"all": True, "ge3": closed and r >= 3, "ge5": closed and r >= 5,
            "gt10": closed and r > 10, "loss": closed and r < 0,
            "other": closed and 0 <= r < 3, "open": not closed}[bucket]


def normalize(row, dataset, ledger_sha, stream):
    minutes = int(row["timeframe_min"])
    close_ms = timestamp(row["available_at"] if dataset == V9 else row["signal_close"])
    open_ms = timestamp(row["signal_bar_open"])
    if close_ms != open_ms + minutes * 60_000:
        raise SourceError("case_clock", "信号形成时间与收盘时间不一致。")
    native_key = str(row["event_key"] if dataset == V9 else row["trade_key"])
    venue, symbol = str(row["venue"]), str(row["symbol"])
    side = "long" if int(row["side"]) == 1 else "short"
    closed = not truth(row.get("censored", False)) and truth(row.get("valid_entry", True))
    net_r = number(row.get("net_r")) if closed else None
    if closed and net_r is None:
        raise SourceError("case_outcome", "已结束案例缺少有效净R，不能分组。")
    venue_key = "binance" if venue in {"binance_um", "binance"} else venue
    exposure_key = digest(f"{venue_key}:{symbol}:{minutes}:{close_ms}:{side}".encode())
    family = "V9 全部多头候选" if dataset == V9 else {"v9_both": "V12.8 普通", "joint": "V12.8 联合多头"}.get(str(row["arm"]), str(row["arm"]))
    return {"id": digest(f"{dataset}:{native_key}".encode()), "dataset": dataset,
            "symbol": symbol, "timeframe": timeframe(minutes), "minutes": minutes, "side": side,
            "signal_close_ms": close_ms, "family": family, "source_label": f"{venue.upper()} 原实验冻结行情",
            "exposure_key": exposure_key, "native_key": native_key, "ledger_sha256": ledger_sha,
            "stream": stream, "source_row": row,
            "outcome": {"net_r": net_r, "gross_r": number(row.get("gross_r")) if closed else None,
                        "mfe_r": number(row.get("mfe_known_r", row.get("mfe_r"))),
                        "mfe_upper_r": number(row.get("mfe_upper_r")),
                        "stop_bar_excursion_ambiguous": truth(row.get("stop_bar_excursion_ambiguous", False)),
                        "entry_time": row.get("entry_time") or None, "exit_time": row.get("exit_time") if closed else None,
                        "exit_reason": row.get("exit_reason") if closed else None,
                        "status": "closed" if closed else "censored", "cost_bps": 20,
                        "meaning": ("原V9逐候选独立结果，允许重叠，不是账户组合收益；MFE通常不计退出柱。" if dataset == V9
                                    else "原V12.8串行成交账本；MFE为可确认浮盈，止损柱顺序不确定时另有上界。") + " 净R按原入场风险并扣20bp往返成本；不代表AI策略收益。"}}


class FrozenCaseHistory(LocalReplayHistory):
    """Adapt one authenticated frame to the existing causal chart machinery."""

    def __init__(self, frame, minutes, path, sha):
        super().__init__(roots=[])
        self.frame, self.minutes, self.path, self.sha = frame, minutes, path, sha

    def _read_market(self, symbol, tf):
        frame = self.frame
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
            raise SourceError("case_clock", "原实验行情缺少带时区的时间轴。")
        if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
            raise SourceError("case_clock", "原实验行情存在重复或乱序时间。")
        duration = self.minutes * 60_000
        rows = []
        for t, values in zip(frame.index.asi8 // 1_000_000, frame[["open", "high", "low", "close"]].itertuples(index=False, name=None)):
            if int(t) % duration:
                raise SourceError("case_clock", "原实验行情周期未对齐。")
            o, h, low, c = self._parse_ohlc(dict(zip(("open", "high", "low", "close"), values)))
            rows.append(dict(t=int(t), o=o, h=h, l=low, c=c))
        return duration, [self.path], self.clock(), [], rows, {self.path: self.sha}


class HistoricalCaseCatalog:
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[2])
        self.lock = threading.RLock()
        self.loaded, self.errors, self.by_id, self.metadata = {}, {}, {}, {}
        self.definitions = {
            V128: ("SPIKE V12.8 · 近两月原版成交", "普通与联合分属不同策略，可能重叠；此库是成交记录，不是完整候选分母。"),
            V9: ("SPIKE V9 · 全部历史多头候选", "逐候选独立结果可能重叠；不与V12.8混算，不等于串行账户收益。"),
        }

    def _load(self, dataset):
        if dataset not in self.definitions:
            raise SourceError("case_dataset", "未知的历史案例库。")
        with self.lock:
            if dataset in self.loaded:
                return self.loaded[dataset]
            try:
                cases = self._v9() if dataset == V9 else self._v128()
                ids = [c["id"] for c in cases]
                if len(ids) != len(set(ids)):
                    raise SourceError("case_duplicate", "原实验账本存在重复事件，不能静默重复计数。")
                cases.sort(key=lambda c: (-c["signal_close_ms"], c["id"]))
                self.loaded[dataset] = cases
                self.errors.pop(dataset, None)
                self.by_id.update({c["id"]: c for c in cases})
                return cases
            except (OSError, ValueError, KeyError, TypeError) as exc:
                message = str(exc) if isinstance(exc, SourceError) else "该历史案例库缺失或校验失败；没有改用其他行情。"
                self.errors[dataset] = message
                raise SourceError("case_unavailable", message) from exc

    def _v9(self):
        folder = self.root / V9_PATH
        manifest = read_json(folder / "manifest.json")
        if not manifest.get("complete"):
            raise ValueError("incomplete V9")
        sha = manifest["candidate_sha256"]
        rows = csv_rows(verified_bytes(folder / "candidates.csv.gz", sha))
        if len(rows) != manifest["candidates"]:
            raise ValueError("V9 count")
        self.metadata[V9] = manifest
        return [normalize(r, V9, sha, str(r["stream_key"])) for r in rows]

    def _v128(self):
        folder = self.root / V128_PATH
        run = folder / "run_v1"
        manifest, identity = read_json(run / "manifest.json"), read_json(run / "identity.json")
        if not manifest.get("complete") or digest(json.dumps(identity, sort_keys=True).encode()) != manifest["run_identity"]:
            raise ValueError("V128 incomplete identity")
        inputs = read_json(folder / "input_manifest.json", identity["input_manifest_sha256"])
        self.metadata[V128] = {"identity": identity, "inputs": {r["symbol"]: r for r in inputs["streams"]}}
        cases = []
        if set(manifest["stream_keys"]) != set(manifest["receipts"]):
            raise ValueError("V128 inventory")
        for stream, receipt_sha in manifest["receipts"].items():
            base = run / "streams" / stream
            receipt = read_json(base / "receipt.json", receipt_sha)
            if receipt["status"] != "complete" or receipt["run_identity"] != manifest["run_identity"]:
                raise ValueError("V128 stream identity")
            if receipt["input_sha256"] != self.metadata[V128]["inputs"][receipt["symbol"]]["sha256"]:
                raise ValueError("V128 source identity")
            sha = receipt["files"]["trades.csv.gz"]
            for row in csv_rows(verified_bytes(base / "trades.csv.gz", sha)):
                if row["arm"] not in {"v9_both", "joint"}:
                    raise ValueError("unexpected strategy")
                cases.append(normalize(row, V128, sha, stream))
        return cases

    def list(self, dataset=V128, bucket="all", symbol="", timeframe="", offset=0, limit=20):
        if bucket not in BUCKETS or not 0 <= offset or not 1 <= limit <= 100:
            raise SourceError("case_query", "历史案例筛选或分页参数无效。")
        inventories = []
        for key, (label, warning) in self.definitions.items():
            try:
                records = self._load(key)
                inventories.append(dict(id=key, label=label, count=len(records), available=True, warning=warning))
            except SourceError:
                inventories.append(dict(id=key, label=label, count=0, available=False, warning=self.errors[key]))
        if dataset not in self.definitions:
            raise SourceError("case_dataset", "未知的历史案例库。")
        records = self.loaded.get(dataset, [])
        records = [c for c in records if symbol.strip().upper() in c["symbol"].upper()
                   and (not timeframe or c["timeframe"].lower() == timeframe.lower())]
        counts = {b: sum(in_bucket(c, b) for c in records) for b in BUCKETS}
        selected = [c for c in records if in_bucket(c, bucket)]
        page = selected[offset:offset + limit]
        return dict(datasets=inventories, dataset=dataset, items=[{k: c[k] for k in PUBLIC_KEYS} for c in page],
                    total=len(selected), offset=offset, limit=limit, counts=counts,
                    warning=self.errors.get(dataset, self.definitions[dataset][1]))

    def get(self, identity):
        with self.lock:
            if identity not in self.by_id:
                for dataset in self.definitions:
                    try:
                        self._load(dataset)
                    except SourceError:
                        pass
            if identity not in self.by_id:
                raise SourceError("case_missing", "历史案例不存在，或它的原始账本不可用。")
            return copy.deepcopy(self.by_id[identity])

    def load_history(self, case):
        try:
            return self._load_history(case)
        except SourceError:
            raise
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            raise SourceError("case_history_unavailable", "原实验冻结行情缺失或格式不一致，无法回放此案例；没有替换数据源。") from exc

    def _load_history(self, case):
        if case["dataset"] == V9:
            key = case["stream"]
            pin = self.metadata[V9]["stream_completion_sha256"][key]
            record = read_json(self.root / V9_PATH / "streams" / key / "completion.json", pin)
            base = self.root / V9_RAW / key
            read_json(base / "completion.json", record["raw_completion_sha256"])
            path = base / "control_cache.pkl.gz"
            sha = record["cache_sha256"]
            # Only repository-owned frozen caches with authenticated ledger pins.
            frame = pd.read_pickle(io.BytesIO(verified_bytes(path, sha)), compression="gzip")["bars"]
        else:
            from yoyo.evaluation.spike_v126_htf_recheck import complete_bars
            metadata = self.metadata[V128]
            item, cfg = metadata["inputs"][case["symbol"]], metadata["identity"]["config"]
            path, sha = self.root / item["path"], item["sha256"]
            raw = pd.read_csv(io.BytesIO(verified_bytes(path, sha)), compression="gzip")
            index = pd.DatetimeIndex(pd.to_datetime(raw["ts"], unit="ms", utc=True))
            if not index.is_unique or not index.is_monotonic_increasing or (index.asi8 % pd.Timedelta(minutes=5).value).any():
                raise SourceError("case_clock", "原V12.8基础行情存在重复、乱序或周期不对齐。")
            raw.index = index
            raw = raw.loc[(raw.index >= pd.Timestamp(cfg["warmup_start"]))
                          & (raw.index + pd.Timedelta(minutes=5) <= pd.Timestamp(cfg["end"]))]
            frame, _ = complete_bars(raw, case["minutes"])
        expected_open = case["signal_close_ms"] - case["minutes"] * 60_000
        if pd.Timestamp(expected_open, unit="ms", tz="UTC") not in frame.index:
            raise SourceError("case_missing_bar", "原实验行情缺少信号K线，不能移动到相邻时点代替。")
        data = FrozenCaseHistory(frame, case["minutes"], path, sha).load(case["symbol"], case["timeframe"], case["signal_close_ms"])
        data["source_label"] = case["source_label"]
        data["source_receipt"].update(case_id=case["id"], ledger_sha256=case["ledger_sha256"],
                                      original_event_key=case["native_key"],
                                      algorithm_version="frozen-study-ohlc-close-ma-v1",
                                      aggregation="original_complete_5m_buckets" if case["dataset"] == V128 else "original_frozen_bars")
        return data
