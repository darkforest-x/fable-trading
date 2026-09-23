"""Read-only catalog for registered experiments and causal research factors.

The catalog joins the existing ``experiments/registry.yaml`` and
``artifacts/registry.yaml`` records with the literal ``FEATURE_COLUMNS`` list
in ``yoyo/layers/l2_judgment/features.py``. Evidence discovery is deliberately
limited to small JSON config/receipt/delivery files and CSVs directly under an
experiment or one ``summary_*`` directory. It never imports model/data
libraries or changes research/production eligibility.
"""
from __future__ import annotations

import ast
import copy
import csv
import hashlib
import io
import json
import re
import threading
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


EXPERIMENT_REGISTRY = Path("experiments/registry.yaml")
ARTIFACT_REGISTRY = Path("artifacts/registry.yaml")
FEATURE_SOURCE = Path("yoyo/layers/l2_judgment/features.py")
MAX_JSON_BYTES = 1_000_000
MAX_CSV_BYTES = 2_000_000
MAX_TABLE_ROWS = 300
MAX_JSON_FILES = 64
MAX_CSV_FILES = 128
_CSV_LOCK = threading.RLock()

_PROTECTED_PARTS = {
    ".private",
    "private",
    "runtime",
    "settings",
    "secrets",
    "credentials",
    "credential",
}
_SENSITIVE_KEY_PARTS = (
    "apikey",
    "accesskey",
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "privatekey",
    "authorization",
    "cookie",
    "seedphrase",
    "mnemonic",
    "webhook",
)

_LATEST_TITLES = {
    "exp-ma-dense-launch-pine-20260923-v1": "密集启动：TradingView 指标复现",
    "exp-spike-v127-held-age-20260923-v1": "SPIKE：突破证据时效上限",
    "exp-spike-v128-roll-hints-20260923-v1": "SPIKE V12.8：滚动结构提示",
    "exp-spike-v128-recent-20260923-v1": "SPIKE V12.8：近两月原版回测与止损复盘",
    "exp-spike-v128-bb-slope-20260924-v1": "SPIKE V12.8：BB200 方向斜率研究",
    "exp-spike-ma-launch-gate-20260923-v1": "SPIKE：训练形态门历史筛选",
    "exp-spike-v128-expansion-entry-20260923-v1": "SPIKE V12.8：高波动扩张门",
    "exp-spike-v128-retest-entry-20260923-v1": "SPIKE V12.8：突破回踩再入场",
    "exp-spike-v128-entry-clock-20260923-v1": "SPIKE V12.8：北京时间入场时段",
    "exp-spike-v128-entry-clock-long-20260923-v1": "SPIKE V12.8：入场时段长周期复核",
}

_EXPERIMENT_FACTOR_IDS = {
    "exp-spike-v128-bb-slope-20260924-v1": ["research.spike.bb200_opposite_slope"],
    "exp-spike-ma-launch-gate-20260923-v1": ["research.ma.training_morphology_gate"],
    "exp-spike-v128-expansion-entry-20260923-v1": ["research.spike.volatility_expansion_high20"],
    "exp-spike-v128-retest-entry-20260923-v1": ["research.spike.retest_entry"],
    "exp-spike-v128-entry-clock-20260923-v1": ["research.spike.entry_clock_beijing"],
    "exp-spike-v128-entry-clock-long-20260923-v1": ["research.spike.entry_clock_beijing"],
}

_FEATURE_META = {
    "ma_spread_pct": ("ma_spread", "聚类快线均线的最高与最低值之差除以收盘价。"),
    "full_spread": ("ma_spread", "全部均线的最高与最低值之差除以收盘价。"),
    "fast_slow_gap": ("ma_spread", "快线簇中点与慢线中点的绝对距离除以收盘价。"),
    "full_ratio_min48": ("ma_spread", "当前全部均线带宽与滚动 48 根最小带宽之比。"),
    "spread_mean8": ("ma_spread", "均线簇带宽最近 8 根的滚动均值。"),
    "spread_mean24": ("ma_spread", "均线簇带宽最近 24 根的滚动均值。"),
    "spread_chg8": ("ma_spread", "当前均线簇带宽减去 8 根前的值；负值表示收窄。"),
    "spread_chg24": ("ma_spread", "当前均线簇带宽减去 24 根前的值；负值表示收窄。"),
    "spread_pos96": ("ma_spread", "当前带宽在过去最多 96 根的最小值至最大值区间中的位置。"),
    "dense_run_len": ("dense_state", "以 ma_spread_pct <= 0.0028 定义的连续密集根数。"),
    "dense_frac48": ("dense_state", "最近 48 根中满足密集带宽条件的比例。"),
    "ext_up": ("price_position", "收盘价相对均线簇上边界的距离；空头语义会改用 ext_down。"),
    "close_vs_ema55": ("price_position", "收盘价相对 EMA55 的变化率；空头语义使用反向比率。"),
    "close_vs_ema200": ("price_position", "收盘价相对 EMA200 的变化率；空头语义使用反向比率。"),
    "order_score": ("trend_order", "EMA8/13/21/34/55 相邻快慢对中，满足多头顺序的对数；空头使用 down_order_score。"),
    "slow_slope_12": ("trend_order", "EMA200 相对 12 根前的百分比变化；空头语义取反。"),
    "volume_ratio": ("volume", "当前成交量与滚动 20 根平均成交量之比。"),
    "volume_z": ("volume", "成交量相对滚动 96 根均值和标准差的 z 分数。"),
    "vol_ratio_mean8": ("volume", "volume_ratio 最近 8 根的滚动均值。"),
    "atr_pct": ("volatility", "ATR14 除以收盘价。"),
    "atr_pct_ratio96": ("volatility", "当前 ATR 百分比与滚动 96 根 ATR 百分比均值之比。"),
    "pre_range48": ("volatility", "最近 48 根最高价与最低价之差除以当前收盘价。"),
    "pre_range168": ("volatility", "最近 168 根最高价与最低价之差除以当前收盘价。"),
    "drawdown24": ("price_position", "过去 24 根最高价相对当前收盘价的跌幅；空头语义使用 runup24。"),
    "ret_4": ("momentum", "收盘价相对 4 根前的收益率；空头语义取反。"),
    "ret_12": ("momentum", "收盘价相对 12 根前的收益率；空头语义取反。"),
    "ret_24": ("momentum", "收盘价相对 24 根前的收益率；空头语义取反。"),
    "ret_48": ("momentum", "收盘价相对 48 根前的收益率；空头语义取反。"),
}

_RESEARCH_FACTORS = (
    {
        "id": "research.spike.bb200_opposite_slope",
        "name": "BB200 对侧边界方向斜率",
        "category": "band_structure",
        "definition": "截至信号前一根的 12 根 OLS 斜率组合：(方向 × BB200 中线斜率 − 半带宽斜率) / ATR，单位 ATR/根；包含趋势平移和带宽变化。BB200 为 SMA200 ± 2 倍总体标准差。",
        "experiment_ids": ("exp-spike-v128-bb-slope-20260924-v1",),
        "causality": "该实验是历史信号分层分析；注册结果未建立跨周期通用过滤优势，也没有实时门验证。",
    },
    {
        "id": "research.spike.retest_entry",
        "name": "突破、回踩、再突破入场",
        "category": "entry_structure",
        "definition": "等待三个不同的收盘 K 线依次形成突破、回踩、再突破，并使用固定 24 根观察期限后入场。",
        "experiment_ids": ("exp-spike-v128-retest-entry-20260923-v1",),
        "causality": "条件在延后入场时可由已收盘 K 线观察；当前注册实验是历史策略回放，净 R 与匹配随机差值不支持默认采用，也未验证生产接入。",
    },
    {
        "id": "research.spike.entry_clock_beijing",
        "name": "北京时间入场时段",
        "category": "entry_timing",
        "definition": "按北京时间把既有串行交易的入场时刻划分为固定时段，再比较净胜率和单笔收益。",
        "experiment_ids": (
            "exp-spike-v128-entry-clock-20260923-v1",
            "exp-spike-v128-entry-clock-long-20260923-v1",
        ),
        "causality": "时间桶在入场时可观测，但从历史样本挑选优胜时段属于事后排序；注册结果显示冠军依赖日历和标的范围，未建立稳定优势。",
    },
    {
        "id": "research.spike.volatility_expansion_high20",
        "name": "币内波动扩张前 20%",
        "category": "volatility",
        "definition": "比较 SPIKE ATR 百分比相对自身 96 根滚动均值的值，与此前 30 天分布第 80 百分位的门槛。",
        "experiment_ids": ("exp-spike-v128-expansion-entry-20260923-v1",),
        "causality": "注册方案声明门槛仅依赖先前数据；该历史回放拒绝将 high20 作为通用默认门，未验证在线计算或生产适用性。",
    },
    {
        "id": "research.ma.training_morphology_gate",
        "name": "Training-selection morphology gate",
        "category": "training_morphology",
        "definition": "把冻结训练样本筛选形态用作 SPIKE 原始多空入场的资格条件。",
        "experiment_ids": ("exp-spike-ma-launch-gate-20260923-v1",),
        "causality": "注册说明参考形态的选取晚于历史回放时点；该历史测试未通过资格门，也未确认实时可用性或逐样本金标。",
    },
)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _sensitive_key(key: Any) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return any(token in compact for token in _SENSITIVE_KEY_PARTS)


def _sanitize_json(value: Any, depth: int = 0) -> tuple[Any, bool]:
    """Remove credential-like JSON keys before evidence reaches an API/UI."""
    changed = False
    if depth >= 24:
        return "[depth limited]", True
    if isinstance(value, dict):
        result = {}
        for key, child in list(value.items())[:2000]:
            if _sensitive_key(key):
                changed = True
                continue
            result[key], child_changed = _sanitize_json(child, depth + 1)
            changed = changed or child_changed
        if len(value) > 2000:
            result["_items_truncated"] = len(value) - 2000
            changed = True
        return result, changed
    if isinstance(value, list):
        result = []
        for child in value[:2000]:
            sanitized, child_changed = _sanitize_json(child, depth + 1)
            result.append(sanitized)
            changed = changed or child_changed
        if len(value) > 2000:
            changed = True
            result.append({"_items_truncated": len(value) - 2000})
        return result, changed
    return value, False


class Catalog:
    """Read the registered experiment/factor catalog and bounded local evidence."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self._cache: dict[str, tuple[tuple[int, int], Any]] = {}
        self._feature_cache: tuple[tuple[int, int], list[tuple[str, int]]] | None = None
        self._lock = threading.RLock()

    def _safe_path(self, path: Path, *, base: Path | None = None) -> Path | None:
        try:
            resolved = path.expanduser().resolve(strict=False)
            root = self.root.resolve(strict=False)
            if not _is_relative_to(resolved, root):
                return None
            if base is not None:
                resolved_base = base.resolve(strict=False)
                if not _is_relative_to(resolved, resolved_base):
                    return None
            relative_parts = [part.casefold() for part in resolved.relative_to(root).parts]
            if any(part in _PROTECTED_PARTS or part.startswith(".") for part in relative_parts):
                return None
            return resolved
        except (OSError, RuntimeError, ValueError):
            return None

    def _read_yaml(self, relative_path: Path, default: Any) -> Any:
        cache_key = relative_path.as_posix()
        path = self._safe_path(self.root / relative_path, base=self.root)
        if path is None or not path.is_file():
            return copy.deepcopy(default)
        try:
            stat = path.stat()
        except OSError:
            return copy.deepcopy(default)
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return copy.deepcopy(cached[1])
            try:
                parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise ValueError(f"无法读取注册表 {relative_path.as_posix()}: {exc}") from exc
            if parsed is None:
                parsed = copy.deepcopy(default)
            self._cache[cache_key] = (fingerprint, copy.deepcopy(parsed))
            return copy.deepcopy(parsed)

    def _experiment_rows(self) -> list[dict[str, Any]]:
        raw = self._read_yaml(EXPERIMENT_REGISTRY, {"experiments": []})
        if not isinstance(raw, dict) or not isinstance(raw.get("experiments", []), list):
            raise ValueError("experiments/registry.yaml must contain an experiments list")
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for source in raw.get("experiments", []):
            if not isinstance(source, dict):
                raise ValueError("experiment rows must be mappings")
            experiment_id = source.get("experiment_id")
            if not isinstance(experiment_id, str) or not experiment_id:
                raise ValueError("experiment row is missing a non-empty experiment_id")
            if experiment_id in seen:
                raise ValueError(f"duplicate experiment_id in registry: {experiment_id}")
            seen.add(experiment_id)
            row = copy.deepcopy(source)
            row["id"] = experiment_id
            row["title"] = row.get("title") or _LATEST_TITLES.get(experiment_id, str(row.get("question") or experiment_id))
            factor_ids = _EXPERIMENT_FACTOR_IDS.get(experiment_id)
            if factor_ids:
                row["factor_ids"] = list(factor_ids)
            rows.append(row)
        def date_key(row):
            explicit = str(row.get("created_at", "")).replace("-", "")[:8]
            dates = re.findall(r"20\d{6}", row["id"])
            return explicit if len(explicit) == 8 and explicit.isdigit() else max(dates, default="")
        return sorted(rows, key=date_key, reverse=True)

    def experiments(self) -> list[dict[str, Any]]:
        """Return registered experiments with stable UI aliases and safe titles."""
        return self._experiment_rows()

    def experiment(self, experiment_id: str) -> dict[str, Any]:
        for row in self._experiment_rows():
            if row["id"] == experiment_id:
                return row
        raise KeyError(experiment_id)

    def _literal_features(self) -> list[tuple[str, int]]:
        path = self._safe_path(self.root / FEATURE_SOURCE, base=self.root)
        if path is None or not path.is_file():
            return []
        try:
            stat = path.stat()
        except OSError:
            return []
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._feature_cache is not None and self._feature_cache[0] == fingerprint:
                return list(self._feature_cache[1])
            if stat.st_size > 1_000_000:
                raise ValueError("features.py exceeds the 1 MB AST-read limit")
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text, filename=FEATURE_SOURCE.as_posix())
            except (OSError, UnicodeError, SyntaxError) as exc:
                raise ValueError(f"无法解析 FEATURE_COLUMNS: {exc}") from exc
            values: list[tuple[str, int]] | None = None
            for node in tree.body:
                targets: list[ast.expr] = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                if not any(isinstance(target, ast.Name) and target.id == "FEATURE_COLUMNS" for target in targets):
                    continue
                try:
                    literal = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError) as exc:
                    raise ValueError("FEATURE_COLUMNS must remain an AST literal") from exc
                if not isinstance(literal, (list, tuple)) or not all(isinstance(item, str) for item in literal):
                    raise ValueError("FEATURE_COLUMNS must be a literal list or tuple of strings")
                if len(set(literal)) != len(literal):
                    raise ValueError("FEATURE_COLUMNS contains duplicate names")
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    values = [(item.value, item.lineno) for item in node.value.elts]
                else:
                    values = [(name, node.lineno) for name in literal]
                break
            if values is None:
                raise ValueError("FEATURE_COLUMNS literal was not found")
            self._feature_cache = (fingerprint, values)
            return list(values)

    def _registry_line(self, experiment_id: str) -> int | None:
        path = self._safe_path(self.root / EXPERIMENT_REGISTRY, base=self.root)
        if path is None or not path.is_file():
            return None
        marker = re.compile(rf"^\s*(?:-\s*)?experiment_id:\s*{re.escape(experiment_id)}\s*$")
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if marker.match(line):
                        return line_number
        except (OSError, UnicodeError):
            return None
        return None

    def _research_factor_records(self, experiment_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = {row["id"]: row for row in experiment_rows}
        factors: list[dict[str, Any]] = []
        for source in _RESEARCH_FACTORS:
            ids = [experiment_id for experiment_id in source["experiment_ids"] if experiment_id in indexed]
            if not ids:
                continue
            factors.append({
                "id": source["id"],
                "name": source["name"],
                "category": source["category"],
                "definition": source["definition"],
                "source_path": EXPERIMENT_REGISTRY.as_posix(),
                "source_line": self._registry_line(ids[0]),
                "status": "research",
                "experiment_ids": ids,
                "causality": source["causality"],
                "training_eligible": False,
                "production_eligible": False,
            })
        return factors

    def factors(self) -> list[dict[str, Any]]:
        """Return literal implemented L2 features plus explicitly linked research factors."""
        causal_note = (
            "features.py 声明第 i 根特征只使用截至 i 的数据；空头方向列从同根指标映射。"
            "这里记录实现的输入边界，不能视为收益优势或实盘验证。"
        )
        factors: list[dict[str, Any]] = []
        for name, line_number in self._literal_features():
            category, definition = _FEATURE_META.get(
                name, ("other", "Literal LightGBM feature; consult the source implementation for its calculation.")
            )
            factors.append({
                "id": f"l2.{name}",
                "name": name,
                "category": category,
                "definition": definition,
                "source_path": FEATURE_SOURCE.as_posix(),
                "source_line": line_number,
                "status": "implemented",
                "experiment_ids": [],
                "causality": causal_note,
                "training_eligible": False,
                "production_eligible": False,
            })
        factors.extend(self._research_factor_records(self._experiment_rows()))
        return factors

    def _artifact_rows(self) -> list[dict[str, Any]]:
        raw = self._read_yaml(ARTIFACT_REGISTRY, {"artifacts": []})
        if not isinstance(raw, dict) or not isinstance(raw.get("artifacts", []), list):
            raise ValueError("artifacts/registry.yaml must contain an artifacts list")
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for row in raw.get("artifacts", []):
            if not isinstance(row, dict):
                raise ValueError("artifact rows must be mappings")
            artifact_id = row.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id:
                raise ValueError("artifact row is missing a non-empty artifact_id")
            if artifact_id in seen:
                raise ValueError(f"duplicate artifact_id in registry: {artifact_id}")
            seen.add(artifact_id)
            rows.append(copy.deepcopy(row))
        return rows

    @staticmethod
    def _safe_relative_registry_path(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.replace("\\", "/").strip()
        pure = PurePosixPath(normalized)
        parts = [part.casefold() for part in pure.parts]
        if pure.is_absolute() or any(part in {"..", "."} for part in parts):
            return None
        if any(part in _PROTECTED_PARTS or part.startswith(".") for part in parts):
            return None
        if pure.parts and ":" in pure.parts[0]:
            return None
        return pure.as_posix()

    def _safe_source_path(self, value: Any) -> str | None:
        """Keep registry provenance only when resolution remains in the repository."""
        relative = self._safe_relative_registry_path(value)
        if relative is None:
            return None
        resolved = self._safe_path(self.root / Path(*PurePosixPath(relative).parts), base=self.root)
        return relative if resolved is not None else None

    def _experiment_dir(self, experiment_id: str) -> Path | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", experiment_id):
            return None
        base = self.root / "experiments"
        for parent in ("active", "historical", "rejected", "accepted", ""):
            candidate = base / parent / experiment_id if parent else base / experiment_id
            safe = self._safe_path(candidate, base=self.root)
            if safe is not None and safe.is_dir():
                return safe
        return None

    @staticmethod
    def _is_allowed_json(path: Path) -> bool:
        name = path.name.casefold()
        if not name.endswith(".json"):
            return False
        if any(token in name for token in ("private", "settings", "secret", "credential", "runtime")):
            return False
        return any(token in name for token in ("config", "receipt", "delivery"))

    @staticmethod
    def _protected_filename(path: Path) -> bool:
        name = path.name.casefold()
        return (
            name.startswith(".")
            or any(token in name for token in ("private", "settings", "secret", "credential", "runtime"))
        )

    def _evidence_files(self, experiment_dir: Path, notes: list[str]) -> list[Path]:
        candidates: list[Path] = []
        try:
            children = sorted(experiment_dir.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            notes.append(f"无法列出实验目录内容：{exc}")
            return []
        for child in children:
            if self._protected_filename(child):
                continue
            safe_child = self._safe_path(child, base=experiment_dir)
            if safe_child is None:
                if child.is_symlink():
                    notes.append(f"已跳过越界或不安全路径：{child.name}")
                continue
            try:
                if safe_child.is_file():
                    candidates.append(safe_child)
                    continue
                if not safe_child.is_dir() or not child.name.startswith("summary_"):
                    continue
                # One level only: direct files under summary_* are in scope.
                for nested in sorted(safe_child.iterdir(), key=lambda item: item.name.casefold()):
                    if self._protected_filename(nested):
                        continue
                    safe_nested = self._safe_path(nested, base=experiment_dir)
                    if safe_nested is None:
                        if nested.is_symlink():
                            notes.append(f"已跳过越界或不安全路径：{nested.name}")
                        continue
                    if safe_nested.is_file():
                        candidates.append(safe_nested)
            except OSError as exc:
                notes.append(f"读取实验目录条目失败：{child.name} ({exc})")
        return candidates

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def evidence(self, experiment_id: str) -> dict[str, Any]:
        """Return bounded local configs/tables with hashes and registry provenance."""
        experiment = self.experiment(experiment_id)
        notes: list[str] = []
        config: dict[str, Any] = {}
        tables: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        source_paths: list[str] = []

        canonical_report = self._safe_source_path(experiment.get("canonical_report"))
        if canonical_report:
            source_paths.append(canonical_report)
        artifacts = experiment.get("artifacts", [])
        artifact_ids = set(artifacts if isinstance(artifacts, list) else [])
        try:
            for artifact in self._artifact_rows():
                if artifact.get("artifact_id") not in artifact_ids:
                    continue
                source_path = self._safe_source_path(artifact.get("source_path"))
                if source_path and source_path not in source_paths:
                    source_paths.append(source_path)
        except ValueError as exc:
            notes.append(str(exc))

        experiment_dir = self._experiment_dir(experiment_id)
        if experiment_dir is None:
            notes.append("本地实验目录不存在或不在允许的 experiments 子目录中；这不代表结果为零。")
            return {
                "experiment": experiment,
                "config": config,
                "tables": tables,
                "files": files,
                "source_paths": source_paths,
                "notes": notes,
            }

        selected = self._evidence_files(experiment_dir, notes)
        json_count = 0
        csv_count = 0
        eligible_table_count = 0
        for path in selected:
            suffix = path.suffix.casefold()
            try:
                size = path.stat().st_size
            except OSError as exc:
                notes.append(f"无法读取文件元数据：{path.name} ({exc})")
                continue
            relative_path = path.relative_to(self.root).as_posix()
            if suffix == ".json" and self._is_allowed_json(path):
                if json_count >= MAX_JSON_FILES:
                    notes.append(f"JSON 文件达到 {MAX_JSON_FILES} 个读取上限，后续项目未读取。")
                    continue
                if size >= MAX_JSON_BYTES:
                    notes.append(f"因超过 1 MB 限制跳过 JSON：{relative_path}")
                    continue
                try:
                    payload_bytes = path.read_bytes()
                    payload = json.loads(payload_bytes.decode("utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    notes.append(f"无法解析 JSON：{relative_path} ({exc})")
                    continue
                payload, redacted = _sanitize_json(payload)
                if redacted:
                    notes.append(f"JSON 中凭据样式字段已移除或截断：{relative_path}")
                config[relative_path] = payload
                files.append({
                    "path": relative_path,
                    "size_bytes": size,
                    "sha256": self._sha256(payload_bytes),
                    "kind": "json",
                    "downloadable": not redacted,
                })
                json_count += 1
                continue

            if suffix != ".csv":
                continue
            if self._protected_filename(path):
                continue
            if csv_count >= MAX_CSV_FILES:
                notes.append(f"CSV 文件达到 {MAX_CSV_FILES} 个读取上限，后续项目未读取。")
                continue
            if size >= MAX_CSV_BYTES:
                notes.append(f"因超过 2 MB 限制跳过 CSV：{relative_path}")
                continue
            eligible_table_count += 1
            try:
                payload_bytes = path.read_bytes()
                text = payload_bytes.decode("utf-8-sig")
                with _CSV_LOCK:
                    previous_limit = csv.field_size_limit()
                    try:
                        csv.field_size_limit(MAX_CSV_BYTES)
                        reader = csv.reader(io.StringIO(text, newline=""))
                        columns = next(reader, [])
                        rows: list[list[str]] = []
                        total_rows = 0
                        for row in reader:
                            total_rows += 1
                            if len(rows) < MAX_TABLE_ROWS:
                                rows.append(row)
                    finally:
                        csv.field_size_limit(previous_limit)
            except (OSError, UnicodeError, csv.Error) as exc:
                notes.append(f"无法解析 CSV：{relative_path} ({exc})")
                continue
            digest = self._sha256(payload_bytes)
            tables.append({
                "name": path.relative_to(experiment_dir).as_posix(),
                "path": relative_path,
                "columns": columns,
                "rows": rows,
                "total_rows": total_rows,
                "truncated": total_rows > MAX_TABLE_ROWS,
                "sha256": digest,
            })
            files.append({"path": relative_path, "size_bytes": size, "sha256": digest, "kind": "csv"})
            csv_count += 1

        if not tables and eligible_table_count == 0:
            notes.append("未发现符合读取范围和大小限制的 CSV 表格；这不代表结果为零。")
        elif eligible_table_count > len(tables):
            notes.append("部分符合路径范围的 CSV 无法解析，已在对应文件旁记录原因。")
        return {
            "experiment": experiment,
            "config": config,
            "tables": tables,
            "files": files,
            "source_paths": source_paths,
            "notes": notes,
        }
