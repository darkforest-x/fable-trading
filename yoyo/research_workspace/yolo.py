"""Bounded, read-only YOLO lifecycle catalog for the unified workbench.

Registries remain authoritative records of questions, artifacts and decisions.
Legacy workflow membership/stages are inferred for navigation only, never as
evidence of completion or eligibility. No model runtime, GPU probe or training
process is started. Files are read only inside registered experiment folders.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .catalog import Catalog, _sanitize_json

STAGES = (
    ("dataset", "样本与数据集", "标签语义、事件谱系、时间切分与数据审核"),
    ("training", "训练记录", "冻结配置、训练收据与历史学习曲线"),
    ("recognition", "识别评估", "真实样本、tip 检查、检出与误检证据"),
    ("economic", "交易验证", "原成本、时间分段、匹配随机与失败结论"),
)
_YOLO = re.compile(
    r"(?:^|[^a-z0-9])(?:yolo(?:v?\d+)?|ma[-_]morph(?:ology)?|ma3r|gold|onset|l1|l2)"
    r"(?=$|[^a-z0-9])|(?:^|[^a-z0-9])owner[-_](?:grade|tip|box|train)(?=$|[^a-z0-9])", re.I)
_STAGE_TERMS = {
    "dataset": r"dataset|label|annotation|gold|negative|curation|crop|标注|数据集|标签|样本",
    "training": r"train|finetun|epoch|训练",
    "recognition": r"eval|detect|scan|morph|iou|precision|recall|tip|smoke|识别|检测|检出|误检",
    "economic": r"econ|backtest|profit|net.return|收益|成本|回测|胜率|净值",
}
_EVIDENCE_DIR = re.compile(
    r"results?|qa|audit(?:[_-].*)?|collected|evaluation(?:[_-].*)?|economics?|"
    r"arm[_-][a-z0-9]+|training|trained(?:[_-].*)?|summary(?:[_-].*)?|run[_-].*|inventory[_-].*|recovery[_-].*", re.I)
_EXTRA_JSON = re.compile(r"manifest|summary|audit|prereg|preflight|metrics|contract|selection", re.I)
_TITLES = {
    "exp-ma-morphology-negatives-20260922-v3": "YOLO 形态 v6：A/B 训练与纯形态反例",
    "exp-ma-morph-v6-econ-audit-20260922-v1": "YOLO 形态 v6：高分收益稳健性复核",
    "exp-ma-morphology-top20-20260922-v1": "YOLO 形态：当日涨幅前 20 扫描",
    "exp-ma-profit3r-negatives-20260922-v2": "YOLO 反例方案：标签语义失败记录",
    "exp-ma-profit3r-20260922-v1": "YOLO 3R 目标：A/B 经济评估",
    "exp-ma-morphology-reuse-20260920-v1": "YOLO 历史样本复用与谱系核对",
}


def _small_receipt(catalog, folder, relative):
    if folder is None:
        return None, None
    path = catalog._safe_path(folder / relative, base=folder)
    if path is None or not path.is_file() or path.stat().st_size > 1_000_000:
        return None, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return (raw, path.relative_to(catalog.root).as_posix()) if isinstance(raw, dict) else (None, None)
    except (OSError, UnicodeError, ValueError):
        return None, None


def workflow_stages(record):
    """Classify legacy records for browsing; this does not validate any stage."""
    identity = " ".join(str(record.get(key) or "") for key in ("experiment_id", "title", "question"))
    if record.get("workflow") != "yolo" and not _YOLO.search(identity):
        return []
    text = identity + " " + str(record.get("single_variable") or "")
    selected = [stage for stage, pattern in _STAGE_TERMS.items() if re.search(pattern, text, re.I)]
    return selected or ["recognition"]


class YoloCatalog(Catalog):
    """Extend evidence discovery only for YOLO-related registered experiments."""

    def _is_allowed_json(self, path):
        # This check is also used on ordinary root/summary files; the extra
        # names apply only to paths belonging to a YOLO experiment directory.
        if super()._is_allowed_json(path):
            return True
        if path.suffix.lower() != ".json" or self._protected_filename(path):
            return False
        relative = path.relative_to(self.root)
        if not _EXTRA_JSON.search(path.name):
            return False
        registry = self.root / "experiments/registry.yaml"
        stat = registry.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = getattr(self, "_yolo_identity_cache", None)
            if cached is None or cached[0] != stamp:
                cached = (stamp, {row["id"] for row in self._experiment_rows() if workflow_stages(row)})
                self._yolo_identity_cache = cached
        ids = cached[1]
        return any(part in ids for part in relative.parts)

    def _evidence_files(self, experiment_dir, notes):
        try:
            if not workflow_stages(self.experiment(experiment_dir.name)):
                return super()._evidence_files(experiment_dir, notes)
        except KeyError:
            return super()._evidence_files(experiment_dir, notes)
        selected = set()
        pending = [(experiment_dir, 0)]
        visited = set()
        while pending and len(visited) < 64 and len(selected) < 256:
            parent, depth = pending.pop(0)
            if parent in visited:
                continue
            visited.add(parent)
            try:
                # Directory breadth is capped before sorting; no image tree
                # or dataset symlink is traversed, even for large old runs.
                children = list(itertools.islice(parent.iterdir(), 257))
                if len(children) > 256:
                    notes.append(f"目录条目超过 256，已限制读取：{parent.name}")
                for child in sorted(children[:256], key=lambda p: p.name):
                    if self._protected_filename(child):
                        continue
                    safe = self._safe_path(child, base=experiment_dir)
                    if safe is None:
                        continue
                    if safe.is_dir() and depth < 4 and _EVIDENCE_DIR.fullmatch(child.name):
                        pending.append((safe, depth + 1))
                    elif safe.is_file() and safe.suffix.lower() in {".csv", ".json"}:
                        selected.add(safe)
                    if len(selected) >= 256:
                        break
            except OSError:
                notes.append(f"目录暂不可读：{parent.name}")
        if pending or len(selected) >= 256:
            notes.append("YOLO 证据达到目录/文件读取上限；未读取内容不等于不存在。")
        return sorted(selected)

    def evidence(self, experiment_id):
        result = super().evidence(experiment_id)
        if not workflow_stages(result["experiment"]):
            return result
        result["experiment"]["title"] = _TITLES.get(experiment_id, result["experiment"]["title"])
        runs = []
        for table in result["tables"]:
            if Path(table["name"]).name != "results.csv":
                continue
            rows = [{k.strip(): v for k, v in row.items()} for row in table["rows"]]
            if not rows or "epoch" not in rows[0]:
                continue
            latest = rows[-1] if not table["truncated"] else None
            runs.append(dict(path=table["path"], recorded_rows=table["total_rows"],
                             last_epoch=latest.get("epoch") if latest else None,
                             metrics={k: v for k, v in (latest or {}).items() if k.startswith("metrics/")},
                             note="已落盘的历史训练记录；最后一行不等于最佳权重，也不代表远端当前运行状态。"))
        ids = set(result["experiment"].get("artifacts") or [])
        artifacts = []
        for record in self._artifact_rows():
            if record["artifact_id"] not in ids:
                continue
            path = self._safe_source_path(record.get("source_path"))
            item = {key: record.get(key) for key in (
                "artifact_id", "artifact_type", "role", "source_commit", "sha256",
                "training_eligible", "production_eligible")}
            item.update(source_path=path, local_exists=bool(path and (self.root / path).is_file()))
            artifacts.append(item)
        receipts = [dict(path=path, status=value["status"]) for path, value in result["config"].items()
                    if isinstance(value, dict) and isinstance(value.get("status"), str)]
        result["yolo_evidence"] = dict(training_runs=runs, artifacts=artifacts, receipts=receipts,
                                       note="哈希与资格为原登记声明；本页未重新计算权重哈希或执行准入。")
        return result


def _pointers(catalog):
    records = []
    for name, relative in (
        ("执行准入包", "models/active_bundle.json"),
        ("历史 ACTIVE 指针", "models/ACTIVE"),
        ("历史 owner_best 登记", "models/owner_best.json"),
    ):
        path = catalog._safe_path(catalog.root / relative, base=catalog.root)
        entry = dict(name=name, path=relative, exists=bool(path and path.is_file()),
                     target=None, status="未找到", note="文件登记与当前通知检测器分开，文件存在不代表本次准入通过。")
        if path and path.is_file():
            entry["status"] = "已登记，未核验"
            if path.stat().st_size <= 64_000:
                try:
                    content = path.read_text(encoding="utf-8")
                    if path.suffix == ".json":
                        payload = json.loads(content)
                        # Explicit display fields only: never return arbitrary
                        # bundle/config contents or load a referenced model.
                        if isinstance(payload, dict):
                            target = payload.get("source_run") or payload.get("bundle_id") or payload.get("weights_file")
                            entry["target"] = str(target)[:240] if target is not None else "文件存在，身份待核验"
                    else:
                        entry["target"] = content.strip()[:240]
                except (OSError, UnicodeError, ValueError):
                    entry["status"] = "文件不可解析"
        records.append(entry)
    return records


def overview(catalog, runtime_snapshot=None):
    """Return registry-derived lifecycle browsing plus a labeled monitor snapshot."""
    items, models = [], []
    artifact_rows = catalog._artifact_rows()
    registered_identities = {(catalog._safe_source_path(a.get("source_path")), a.get("sha256"))
                             for a in artifact_rows if a.get("artifact_type") in {"weights", "model"}}
    for record in catalog.experiments():
        stages = workflow_stages(record)
        if not stages:
            continue
        item = copy.deepcopy(record)
        item["title"] = _TITLES.get(record["id"], item["title"])
        item["stages"] = stages
        item["workflow_classification"] = "explicit" if record.get("workflow") == "yolo" else "inferred"
        # These are registry declarations, not permission granted by this UI.
        item["training_eligible"] = record.get("training_eligible", False) is True
        item["production_eligible"] = record.get("production_eligible", False) is True
        folder = catalog._experiment_dir(record["id"])
        for relative in ("collected/job_receipt.json", "results/training_receipt.json", "results_summary.json"):
            receipt, path = _small_receipt(catalog, folder, relative)
            if receipt and isinstance(receipt.get("status"), str):
                item["receipt_status"] = dict(status=receipt["status"][:150], path=path)
                break
        summary, receipt_path = _small_receipt(catalog, folder, "results_summary.json")
        if summary and isinstance(summary.get("arms"), dict):
            for arm, model in summary["arms"].items():
                if not isinstance(model, dict) or not model.get("best_path"):
                    continue
                path = catalog._safe_source_path(model["best_path"])
                models.append(dict(name=f"{item['title']} · {arm}", experiment_id=record["id"],
                                   source_path=path, sha256=model.get("best_sha256"),
                                   registered=bool(path and model.get("best_sha256") and
                                                   (path, model["best_sha256"]) in registered_identities),
                                   local_exists=bool(path and (catalog.root / path).is_file()), source_receipt=receipt_path))
        items.append(item)
    for artifact in artifact_rows:
        if artifact.get("artifact_type") not in {"weights", "model"}:
            continue
        owners = [x for x in items if artifact["artifact_id"] in (x.get("artifacts") or [])]
        if not owners:
            continue
        path = catalog._safe_source_path(artifact.get("source_path"))
        if path and any(m["source_path"] == path and m["sha256"] == artifact.get("sha256") for m in models):
            continue
        models.append(dict(name=artifact["artifact_id"], experiment_id=owners[0]["id"], source_path=path,
                           sha256=artifact.get("sha256"), registered=True,
                           local_exists=bool(path and (catalog.root / path).is_file()), source_receipt=None))
    pointers = _pointers(catalog)
    if isinstance(runtime_snapshot, dict):
        model = runtime_snapshot.get("runtime", {}).get("model_gate", {})
        if isinstance(model, dict) and model:
            stamp = runtime_snapshot.get("snapshot_at_ms")
            try:
                at = datetime.fromtimestamp(float(stamp) / 1000, timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                at = "时间未知"
            pointers.insert(0, dict(
                name="当前通知检测器快照", path="/api/status · runtime.model_gate", exists=True,
                source_time=at,
                target=f"{model.get('profile_id', '未记录')} · SHA {model.get('model_sha256', '未记录')}",
                status=str(model.get("status") or "unknown"),
                note=f"快照 {at}；{'已过期' if runtime_snapshot.get('stale', True) else '服务标记有效'}。loaded={model.get('loaded')}，仅通知监控；不能据此推导训练进度或交易准入。",
            ))
    safe_items, _ = _sanitize_json(items)
    return dict(
        generated_at=datetime.now(timezone.utc).isoformat(), items=safe_items, pointers=pointers, models=models,
        stages=[dict(id=key, label=label, description=description,
                     count=sum(key in item["stages"] for item in items)) for key, label, description in STAGES],
        summary=dict(experiments=len(items), artifacts=len({a for x in items for a in (x.get("artifacts") or [])})),
        notes=["旧实验按登记文本归类；同一实验可出现于多个环节，计数不代表已完成。",
               "保留历史失败与资格声明。识别指标、交易收益、生产准入分别核验。",
               "登记实验仅保存研究计划；本页面不会启动训练、切换模型或执行交易。"],
    )
