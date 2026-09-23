"""Read-only inventory and audit helpers for local research model assets.

Model identity is sourced from the project registries and small metadata files.
Text LightGBM dumps may be hashed and have their literal feature header read;
binary YOLO checkpoints are never opened or hashed here. No model runtime is
loaded, and this catalog can never grant training or production eligibility.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import Catalog, _sanitize_json
from .yolo import overview as yolo_overview


MAX_METADATA_BYTES = 1_000_000
MAX_TEXT_MODEL_BYTES = 32_000_000
ALLOWED_STAGES = {"research", "rejected", "archived"}

FAMILY_DEFINITIONS = (
    {
        "id": "yolo_detector",
        "name": "YOLO 检测模型",
        "role": "图像形态检测器与登记权重",
        "description": "按产物登记与有界训练摘要展示；大权重只看路径和文件元数据，不读取或重算权重哈希。",
        "view": "yolo",
    },
    {
        "id": "l2_frozen",
        "name": "冻结 LightGBM",
        "role": "历史冻结的 L2 判断模型",
        "description": "从 models/frozen*.json sidecar 读取小型配置，并核对对应的文本模型身份；ACTIVE 指针不构成准入。",
        "view": "models",
    },
    {
        "id": "l2_research",
        "name": "L2 研究模型",
        "role": "已登记的 LightGBM 排序或回归模型",
        "description": "汇总 artifacts/registry.yaml 中的 model 记录和关联实验；历史结果与资格声明分别展示。",
        "view": "models",
    },
    {
        "id": "numeric_baseline",
        "name": "数值形态基线",
        "role": "L1 数值特征研究管线",
        "description": "展示因果 OHLCV 特征和历史拒绝实验；不把管线能力冒充成现存可部署权重。",
        "view": "models",
    },
    {
        "id": "vision_review",
        "name": "VLM 辅助研究能力",
        "role": "研究用图像复核与 VLM 辅助代码能力",
        "description": "仅登记本地研究能力入口；不读取 provider 配置、密钥、调用服务或宣称有已验证模型。",
        "view": "vision",
    },
)

_FAMILY_BY_ID = {row["id"]: row for row in FAMILY_DEFINITIONS}
_MODEL_NAME_OVERRIDES = {
    "rejected_lightgbm_global_context_ranker": "L2 全局上下文排序器",
    "rejected_research_only_long_gross_return_regressor": "L2 LONG 收益回归器",
    "rejected_research_only_short_gross_return_regressor": "L2 SHORT 收益回归器",
    "rejected_side_specific_long_return_regressor_without_l15": "L2 LONG 直通回归器",
    "rejected_side_specific_short_return_regressor_without_l15": "L2 SHORT 直通回归器",
    "rejected_reference_augmented_long_return_regressor": "L2 LONG 参考样本增强回归器",
    "rejected_reference_augmented_short_return_regressor": "L2 SHORT 参考样本增强回归器",
    "rejected_tune_selected_long_24_feature_return_regressor": "L2 LONG 特征组消融模型",
    "rejected_tune_selected_short_16_feature_return_regressor": "L2 SHORT 特征组消融模型",
    "rejected_tune_selected_long_110_feature_return_regressor": "L2 LONG 特征扩展模型",
    "rejected_tune_selected_short_54_feature_return_regressor": "L2 SHORT 特征扩展模型",
}


def _sha256_file(path: Path, max_bytes: int) -> str | None:
    """Hash only a bounded regular file without following unsafe paths."""
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size > max_bytes:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _status(value: Any, role: str = "") -> str:
    lowered = str(value or "").strip().casefold()
    if lowered in ALLOWED_STAGES:
        return lowered
    if lowered in {"fail", "failed", "failure", "inconclusive_rejected"} or "reject" in lowered:
        return "rejected"
    if not lowered and "reject" in role.casefold():
        return "rejected"
    return "research"


class ModelCatalog:
    """Join registered model assets with frozen metadata and review notes.

    The source of truth for durable artifacts remains ``Catalog``'s registries.
    Human annotations are stored separately from audit snapshots so editing a
    stage or note cannot make an old identity audit appear current.
    """

    def __init__(self, root: Path, catalog: Catalog, store):
        self.root = Path(root).expanduser().resolve()
        self.catalog = catalog
        self.store = store

    def _safe(self, relative: str | None) -> Path | None:
        if not relative:
            return None
        try:
            candidate = self.catalog._safe_path(self.root / relative, base=self.root)
            return candidate
        except (OSError, RuntimeError, ValueError):
            return None

    def _read_json(self, relative: str | None) -> dict[str, Any] | None:
        path = self._safe(relative)
        if path is None or not path.is_file():
            return None
        try:
            if path.stat().st_size > MAX_METADATA_BYTES:
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        sanitized, _ = _sanitize_json(raw)
        return sanitized if isinstance(sanitized, dict) else None

    def _text_header(self, relative: str | None) -> dict[str, Any]:
        path = self._safe(relative)
        result: dict[str, Any] = {"sha256": None, "feature_names": None, "size_bytes": None}
        if path is None or path.suffix.casefold() != ".txt" or not path.is_file():
            return result
        try:
            size = path.stat().st_size
            result["size_bytes"] = size
            with path.open("rb") as stream:
                data = stream.read(256 * 1024)
        except OSError:
            return result
        for line in data.decode("utf-8", errors="replace").splitlines():
            if line.startswith("feature_names="):
                names = line.partition("=")[2].split()
                result["feature_names"] = names
                break
        return result

    def _text_info(self, relative: str | None) -> dict[str, Any]:
        """Read a bounded header and hash the complete text only for explicit audit."""
        result = self._text_header(relative)
        path = self._safe(relative)
        if path is None or not result.get("size_bytes"):
            return result
        if result["size_bytes"] > MAX_TEXT_MODEL_BYTES:
            result["too_large"] = True
            return result
        result["sha256"] = _sha256_file(path, MAX_TEXT_MODEL_BYTES)
        return result

    def _experiments_by_artifact(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for exp in self.catalog.experiments():
            for artifact_id in exp.get("artifacts") or []:
                if isinstance(artifact_id, str):
                    result.setdefault(artifact_id, []).append(exp)
        return result

    @staticmethod
    def _record_name(record: dict[str, Any]) -> str:
        role = str(record.get("role") or "")
        if role in _MODEL_NAME_OVERRIDES:
            return _MODEL_NAME_OVERRIDES[role]
        artifact_id = str(record.get("artifact_id") or "registered model")
        if record.get("artifact_type") == "weights":
            return f"YOLO 权重 · {artifact_id}"
        label = role.replace("_", " ").strip()
        return f"L2 研究模型 · {label or artifact_id}"

    def _new_artifact_item(self, record: dict[str, Any], owners: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.catalog._safe_source_path(record.get("source_path"))
        artifact_type = str(record.get("artifact_type") or "")
        family = "yolo_detector" if artifact_type == "weights" else "l2_research"
        local = self._safe(path)
        role = str(record.get("role") or "")
        statuses = [_status(row.get("status"), role) for row in owners]
        status = "rejected" if "rejected" in statuses else (statuses[0] if statuses else _status(None, role))
        declared_sha = record.get("sha256")
        feature_names = None
        if family == "l2_research":
            feature_names = self._text_header(path).get("feature_names")
        experiment_ids = [row["id"] for row in owners]
        notes = str(record.get("notes") or "")
        if artifact_type == "weights":
            notes = (notes + " " if notes else "") + "大权重只核验安全路径、存在性与登记声明；不会读取权重或重算 SHA。"
        else:
            notes = (notes + " " if notes else "") + "登记模型只作历史研究证据；当前训练与生产资格仍为 false。"
        return {
            "id": str(record.get("artifact_id")),
            "name": self._record_name(record),
            "family": family,
            "kind": "yolo_weights" if artifact_type == "weights" else "lightgbm_text_model",
            "status": status,
            "source_path": path,
            "metadata_path": None,
            "feature_count": len(feature_names) if feature_names is not None else None,
            "feature_semantics": None,
            "objective": role or None,
            "experiment_ids": experiment_ids,
            "artifact_ids": [str(record.get("artifact_id"))],
            "local_exists": bool(local and local.is_file()),
            "training_eligible": False,
            "production_eligible": False,
            "declared_eligibility": [{
                "artifact_id": str(record.get("artifact_id")),
                "training_eligible": record.get("training_eligible") is True,
                "production_eligible": record.get("production_eligible") is True,
            }],
            "declared_sha256s": [declared_sha] if isinstance(declared_sha, str) else [],
            "notes": notes,
            "revision": 0,
            "audit": None,
            "_metadata_paths": [],
            "_metadata": {},
            "_registered": True,
        }

    def _merge_artifact(self, current: dict[str, Any], record: dict[str, Any], owner_rows: list[dict[str, Any]]) -> None:
        artifact_id = str(record.get("artifact_id"))
        if artifact_id not in current["artifact_ids"]:
            current["artifact_ids"].append(artifact_id)
        if artifact_id not in {entry["artifact_id"] for entry in current["declared_eligibility"]}:
            current["declared_eligibility"].append({
                "artifact_id": artifact_id,
                "training_eligible": record.get("training_eligible") is True,
                "production_eligible": record.get("production_eligible") is True,
            })
        declared = record.get("sha256")
        if isinstance(declared, str) and declared not in current["declared_sha256s"]:
            current["declared_sha256s"].append(declared)
        for owner in owner_rows:
            if owner["id"] not in current["experiment_ids"]:
                current["experiment_ids"].append(owner["id"])
        if any(_status(row.get("status"), str(record.get("role") or "")) == "rejected" for row in owner_rows):
            current["status"] = "rejected"
        current["declared_eligibility"].append({
            "artifact_id": artifact_id,
            "training_eligible": record.get("training_eligible") is True,
            "production_eligible": record.get("production_eligible") is True,
        }) if artifact_id not in {entry["artifact_id"] for entry in current["declared_eligibility"]} else None

    def _linked_metadata_paths(self, item: dict[str, Any], artifacts: list[dict[str, Any]]) -> list[str]:
        owners = set(item.get("experiment_ids") or [])
        linked_ids = {
            artifact_id
            for exp in self.catalog.experiments()
            if exp["id"] in owners
            for artifact_id in (exp.get("artifacts") or [])
        }
        paths: list[str] = []
        for record in artifacts:
            if record.get("artifact_id") not in linked_ids or record.get("artifact_type") != "manifest":
                continue
            role = str(record.get("role") or "").casefold()
            source = self.catalog._safe_source_path(record.get("source_path"))
            if not source or not source.casefold().endswith(".json"):
                continue
            if "receipt" not in role and not any(word in role for word in ("training", "feature", "dataset")):
                continue
            path = self._safe(source)
            if path and path.is_file():
                try:
                    if path.stat().st_size > MAX_METADATA_BYTES:
                        continue
                except OSError:
                    continue
            else:
                continue
            # A receipt shared by LONG/SHORT or multiple arms is not a model
            # contract unless it binds itself to this exact artifact identity.
            metadata = self._read_json(source)
            if not metadata:
                continue
            declared_path = self.catalog._safe_source_path(metadata.get("model_path"))
            declared_sha = metadata.get("model_sha256")
            path_matches = bool(item.get("source_path") and declared_path == item["source_path"])
            sha_matches = bool(
                isinstance(declared_sha, str)
                and declared_sha in (item.get("declared_sha256s") or [])
            )
            if path_matches or sha_matches:
                paths.append(source)
        return paths[:4]

    def _merge_metadata(self, item: dict[str, Any], relative: str, *, frozen: bool = False) -> None:
        metadata = self._read_json(relative)
        if metadata is None:
            return
        item["_metadata_paths"] = [relative] + [p for p in item["_metadata_paths"] if p != relative]
        # Preserve specific metadata precedence: root frozen sidecars override
        # generic receipts; receipts only fill missing fields.
        current = dict(item.get("_metadata") or {})
        if frozen:
            current.update(metadata)
        else:
            for key, value in metadata.items():
                current.setdefault(key, value)
        item["_metadata"] = current
        if not item.get("metadata_path"):
            item["metadata_path"] = relative
        if frozen:
            item["family"] = "l2_frozen"
            item["metadata_path"] = relative
            item["kind"] = "frozen_lightgbm_text_model"
            item["name"] = f"冻结 LightGBM · {metadata.get('config') or Path(relative).stem}"
        names = metadata.get("feature_columns") or metadata.get("feature_names")
        if isinstance(names, list) and all(isinstance(name, str) for name in names):
            item["feature_count"] = len(names)
        if isinstance(metadata.get("feature_semantics"), str):
            item["feature_semantics"] = metadata["feature_semantics"]
        objective = metadata.get("objective") or metadata.get("target_column")
        if isinstance(objective, str):
            item["objective"] = objective

    def _add_frozen_sidecars(self, items: list[dict[str, Any]], by_path: dict[str, dict[str, Any]]) -> None:
        models_dir = self._safe("models")
        if models_dir is None or not models_dir.is_dir():
            return
        try:
            children = sorted(models_dir.glob("frozen*.json"), key=lambda p: p.name.casefold())[:128]
        except OSError:
            return
        for sidecar in children:
            safe_sidecar = self.catalog._safe_path(sidecar, base=self.root)
            if safe_sidecar is None or not safe_sidecar.is_file():
                continue
            relative_meta = safe_sidecar.relative_to(self.root).as_posix()
            metadata = self._read_json(relative_meta)
            if metadata is None:
                continue
            model_relative = self.catalog._safe_source_path(metadata.get("model_path"))
            item = by_path.get(model_relative) if model_relative else None
            if item is None:
                item = {
                    "id": f"frozen:{safe_sidecar.stem}",
                    "name": f"冻结 LightGBM · {metadata.get('config') or safe_sidecar.stem}",
                    "family": "l2_frozen",
                    "kind": "frozen_lightgbm_text_model",
                    "status": _status(metadata.get("status")),
                    "source_path": model_relative,
                    "metadata_path": relative_meta,
                    "feature_count": None,
                    "feature_semantics": metadata.get("feature_semantics") if isinstance(metadata.get("feature_semantics"), str) else None,
                    "objective": metadata.get("objective") or metadata.get("target_column"),
                    "experiment_ids": [],
                    "artifact_ids": [],
                    "local_exists": bool(model_relative and (self._safe(model_relative) and self._safe(model_relative).is_file())),
                    "training_eligible": False,
                    "production_eligible": False,
                    "declared_eligibility": [],
                    "declared_sha256s": [],
                    "notes": "models/ 下的历史 frozen sidecar；ACTIVE 指针和旧 promote 声明不授予当前准入。",
                    "revision": 0,
                    "audit": None,
                    "_metadata_paths": [],
                    "_metadata": {},
                    "_registered": False,
                }
                items.append(item)
                if model_relative:
                    by_path[model_relative] = item
            self._merge_metadata(item, relative_meta, frozen=True)
            if model_relative:
                item["source_path"] = model_relative
                path = self._safe(model_relative)
                item["local_exists"] = bool(path and path.is_file())

    def _add_yolo_summary_models(self, items: list[dict[str, Any]], by_path: dict[str, dict[str, Any]]) -> None:
        try:
            summary = yolo_overview(self.catalog)
        except (OSError, ValueError, KeyError, TypeError):
            return
        experiments = {row["id"]: row for row in self.catalog.experiments()}
        for index, model in enumerate(summary.get("models", [])):
            if not isinstance(model, dict):
                continue
            source_path = self.catalog._safe_source_path(model.get("source_path"))
            if source_path and source_path in by_path:
                current = by_path[source_path]
                if model.get("sha256") and model["sha256"] not in current["declared_sha256s"]:
                    current["declared_sha256s"].append(model["sha256"])
                if model.get("experiment_id") and model["experiment_id"] not in current["experiment_ids"]:
                    current["experiment_ids"].append(model["experiment_id"])
                continue
            experiment_id = model.get("experiment_id") if isinstance(model.get("experiment_id"), str) else None
            owner = experiments.get(experiment_id or "")
            safe_path = self._safe(source_path)
            slug = re.sub(r"[^a-z0-9]+", "-", str(model.get("name") or index).casefold()).strip("-")[:64] or str(index)
            identifier = f"yolo-run:{experiment_id or 'unknown'}:{slug}"
            if any(item["id"] == identifier for item in items):
                identifier += f":{index}"
            receipt_path = self.catalog._safe_source_path(model.get("source_receipt"))
            item = {
                "id": identifier,
                "name": str(model.get("name") or "YOLO 实验权重"),
                "family": "yolo_detector",
                "kind": "yolo_weights",
                "status": _status(owner.get("status") if owner else None),
                "source_path": source_path,
                "metadata_path": receipt_path,
                "feature_count": None,
                "feature_semantics": None,
                "objective": "实验训练产物；模型权重身份未在本目录重算。",
                "experiment_ids": [experiment_id] if experiment_id else [],
                "artifact_ids": [],
                "local_exists": bool(safe_path and safe_path.is_file()),
                "training_eligible": False,
                "production_eligible": False,
                "declared_eligibility": [],
                "declared_sha256s": [model["sha256"]] if isinstance(model.get("sha256"), str) else [],
                "notes": "从有界 YOLO 训练摘要发现的历史权重；摘要或远端进程状态不等于当前运行进度或准入。",
                "revision": 0,
                "audit": None,
                "_metadata_paths": [receipt_path] if receipt_path else [],
                "_metadata": {},
                "_registered": bool(model.get("registered")),
            }
            items.append(item)
            if source_path:
                by_path[source_path] = item

    def _add_numeric_baseline(self, items: list[dict[str, Any]], experiments: list[dict[str, Any]]) -> None:
        source_relative = "yoyo/layers/l1_detection/numeric_baseline/train.py"
        feature_relative = "yoyo/layers/l1_detection/numeric_baseline/features.py"
        source = self._safe(source_relative)
        feature_path = self._safe(feature_relative)
        feature_count = None
        if feature_path and feature_path.is_file():
            try:
                if feature_path.stat().st_size <= MAX_METADATA_BYTES:
                    tree = ast.parse(feature_path.read_text(encoding="utf-8"), filename=feature_relative)
                    for node in tree.body:
                        if isinstance(node, ast.Assign) and any(
                            isinstance(target, ast.Name) and target.id == "FEATURE_COLUMNS" for target in node.targets
                        ):
                            values = ast.literal_eval(node.value)
                            if isinstance(values, (tuple, list)) and all(isinstance(value, str) for value in values):
                                feature_count = len(values)
                            break
            except (OSError, UnicodeError, SyntaxError, ValueError, TypeError):
                pass
        experiment = next((row for row in experiments if row["id"] == "exp-yoyo-eth-semantic-mvp"), None)
        artifacts = experiment.get("artifacts") if experiment else []
        items.append({
            "id": "numeric-baseline-family",
            "name": "OHLCV 数值形态候选基线",
            "family": "numeric_baseline",
            "kind": "research_pipeline",
            "status": _status(experiment.get("status") if experiment else "research"),
            "source_path": source_relative if source else None,
            "metadata_path": feature_relative if feature_path and feature_path.is_file() else None,
            "feature_count": feature_count,
            "feature_semantics": "源模块声明特征使用决策时点及之前 K 线；此为管线语义，不是现存模型的 schema。",
            "objective": "历史研究目标为以 27 个因果数值特征排序短向效用；登记实验为 rejected，未登记可部署权重。",
            "experiment_ids": [experiment["id"]] if experiment else [],
            "artifact_ids": list(artifacts) if isinstance(artifacts, list) else [],
            "local_exists": bool(source and source.is_file()),
            "training_eligible": False,
            "production_eligible": False,
            "declared_eligibility": [{
                "experiment_id": experiment["id"],
                "training_eligible": experiment.get("training_eligible") is True,
                "production_eligible": experiment.get("production_eligible") is True,
            }] if experiment else [],
            "declared_sha256s": [],
            "notes": "展示研究代码和失败记录，不表示模型权重已生成；历史配置与本目录均不授予训练或生产资格。",
            "revision": 0,
            "audit": None,
            "_metadata_paths": [feature_relative] if feature_path and feature_path.is_file() else [],
            "_metadata": {"feature_columns_count": feature_count},
            "_registered": bool(experiment),
        })

    def _add_vision_capability(self, items: list[dict[str, Any]]) -> None:
        relative = "yoyo/vision_research"
        folder = self._safe(relative)
        items.append({
            "id": "vision-review-capability",
            "name": "人工图像复核与 VLM 辅助能力",
            "family": "vision_review",
            "kind": "research_capability",
            "status": "research",
            "source_path": relative if folder and folder.is_dir() else None,
            "metadata_path": None,
            "feature_count": None,
            "feature_semantics": None,
            "objective": "辅助研究样本复核与标注工作流；不是已经训练或验证的交易模型。",
            "experiment_ids": [],
            "artifact_ids": [],
            "local_exists": bool(folder and folder.is_dir()),
            "training_eligible": False,
            "production_eligible": False,
            "declared_eligibility": [],
            "declared_sha256s": [],
            "notes": "仅记录本地研究能力入口；未读取 provider/settings 文件、密钥或模型，不代表外部服务可用。",
            "revision": 0,
            "audit": None,
            "_metadata_paths": [],
            "_metadata": {},
            "_registered": False,
        })

    def _collect_items(self) -> list[dict[str, Any]]:
        records = [row for row in self.catalog._artifact_rows() if row.get("artifact_type") in {"model", "weights"}]
        owners_by_id = self._experiments_by_artifact()
        items: list[dict[str, Any]] = []
        by_path: dict[str, dict[str, Any]] = {}
        by_artifact_id: dict[str, dict[str, Any]] = {}
        for record in records:
            item = self._new_artifact_item(record, owners_by_id.get(str(record.get("artifact_id")), []))
            source_path = item.get("source_path")
            current = by_path.get(source_path) if source_path else None
            if current is not None:
                self._merge_artifact(current, record, owners_by_id.get(str(record.get("artifact_id")), []))
                by_artifact_id[str(record.get("artifact_id"))] = current
                continue
            items.append(item)
            if source_path:
                by_path[source_path] = item
            by_artifact_id[str(record.get("artifact_id"))] = item
        all_artifacts = self.catalog._artifact_rows()
        for item in items:
            if item["family"] == "l2_research":
                paths = self._linked_metadata_paths(item, all_artifacts)
                for path in paths:
                    self._merge_metadata(item, path)
        self._add_frozen_sidecars(items, by_path)
        self._add_yolo_summary_models(items, by_path)
        experiments = self.catalog.experiments()
        self._add_numeric_baseline(items, experiments)
        self._add_vision_capability(items)
        return items

    def _annotations(self) -> dict[str, dict[str, Any]]:
        if self.store is None:
            return {}
        return self.store.notes("model_annotation")

    def _audit_notes(self) -> dict[str, dict[str, Any]]:
        if self.store is None:
            return {}
        return self.store.notes("model_audit")

    def _item(self, model_id: str) -> dict[str, Any]:
        for item in self._collect_items():
            if item["id"] == model_id:
                return item
        raise KeyError(model_id)

    def _file_identity(self, item: dict[str, Any], *, audit: bool = False) -> dict[str, Any]:
        relative = item.get("source_path")
        path = self._safe(relative)
        identity: dict[str, Any] = {"path": relative, "exists": False,
                                    "declared_sha256s": sorted(item.get("declared_sha256s") or [])}
        if path is not None:
            try:
                stat = path.stat()
                is_file = path.is_file()
                is_capability_dir = item.get("family") == "vision_review" and path.is_dir()
                identity.update(exists=is_file or is_capability_dir, path_kind="file" if is_file else ("directory" if is_capability_dir else "other"),
                                size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns,
                                ctime_ns=stat.st_ctime_ns, inode=stat.st_ino)
                if is_file and item.get("family") == "yolo_detector":
                    identity["declared_sha256s"] = sorted(item.get("declared_sha256s") or [])
                    identity["hash_policy"] = "declared_only_not_rehashed"
            except OSError:
                identity["stat_error"] = True
        else:
            identity["unsafe_or_unavailable_path"] = True
        metadata_identities = []
        for meta_relative in item.get("_metadata_paths") or []:
            meta_path = self._safe(meta_relative)
            record = {"path": meta_relative, "exists": False}
            if meta_path is not None:
                try:
                    stat = meta_path.stat()
                    record.update(exists=meta_path.is_file(), size_bytes=stat.st_size,
                                  mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns, inode=stat.st_ino)
                    if meta_path.is_file() and stat.st_size <= MAX_METADATA_BYTES:
                        record["sha256"] = _sha256_file(meta_path, MAX_METADATA_BYTES)
                except OSError:
                    record["stat_error"] = True
            metadata_identities.append(record)
        identity["metadata"] = metadata_identities
        return identity

    @staticmethod
    def _fingerprint(identity: dict[str, Any]) -> str:
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _decorate(self, item: dict[str, Any], annotations: dict[str, dict[str, Any]], audits: dict[str, dict[str, Any]]) -> dict[str, Any]:
        result = {key: value for key, value in item.items() if not key.startswith("_")}
        annotation = annotations.get(item["id"], {})
        if annotation:
            stage = annotation.get("stage")
            if stage in ALLOWED_STAGES:
                result["status"] = stage
            result["revision"] = int(annotation.get("revision") or 0)
            result["annotation_notes"] = str(annotation.get("notes") or "")
            if result["annotation_notes"]:
                result["notes"] = (str(result.get("notes") or "") + "\n\n复核备注：" + result["annotation_notes"]).strip()
        current_identity = self._file_identity(item)
        result["identity_snapshot"] = current_identity
        stored = audits.get(item["id"])
        if stored:
            current_fingerprint = self._fingerprint(current_identity)
            stale = stored.get("fingerprint") != current_fingerprint
            audit = {key: value for key, value in stored.items() if key != "revision"}
            audit["stale"] = stale
            if stale:
                audit["status"] = "stale"
                audit["notes"] = "模型或元数据身份已变化；请重新执行审计。"
            result["audit"] = audit
        else:
            result["audit"] = None
        # Hard policy: registry declarations and user notes cannot grant these.
        result["training_eligible"] = False
        result["production_eligible"] = False
        return result

    def overview(self) -> dict[str, Any]:
        annotations = self._annotations()
        audits = self._audit_notes()
        raw_items = self._collect_items()
        decorated = [self._decorate(item, annotations, audits) for item in raw_items]
        families = []
        for definition in FAMILY_DEFINITIONS:
            entry = dict(definition)
            entry["count"] = sum(item["family"] == definition["id"] for item in decorated)
            families.append(entry)
        return {
            "families": families,
            "items": decorated,
            "gates": {
                "training": {"allowed": False, "reason": "当前项目阶段禁止新训练；目录与审计不能授予训练资格。"},
                "production": {"allowed": False, "reason": "当前没有经 Owner 批准的生产模型包；历史 ACTIVE 或登记声明不改变该门。"},
            },
        }

    def get(self, model_id: str) -> dict[str, Any]:
        item = self._item(model_id)
        return self._decorate(item, self._annotations(), self._audit_notes())

    @staticmethod
    def _chronology(metadata: dict[str, Any]) -> tuple[str, str]:
        splits = metadata.get("splits")
        if not isinstance(splits, dict):
            return "unknown", "元数据没有可核验的 train/validation 时间边界。"
        train = splits.get("train")
        validation = splits.get("val") or splits.get("validation")
        if not isinstance(train, dict) or not isinstance(validation, dict):
            return "unknown", "元数据没有同时提供 train 与 validation 分段。"
        train_range = train.get("range")
        val_range = validation.get("range")
        if not isinstance(train_range, list) or len(train_range) < 2 or not isinstance(val_range, list) or len(val_range) < 2:
            return "unknown", "分段中没有完整的时间范围。"
        train_end, val_start = _parse_date(train_range[-1]), _parse_date(val_range[0])
        if train_end is None or val_start is None:
            return "unknown", "分段时间无法解析。"
        try:
            ordered = train_end < val_start
        except TypeError:
            return "unknown", "train/validation 时区格式不兼容。"
        return ("pass", "train 结束早于 validation 开始。") if ordered else ("mismatch", "train/validation 时间重叠或逆序。")

    def audit(self, model_id: str) -> dict[str, Any]:
        item = self._item(model_id)
        metadata: dict[str, Any] = dict(item.get("_metadata") or {})
        identity = self._file_identity(item, audit=True)
        fingerprint = self._fingerprint(identity)
        checks: list[dict[str, str]] = []
        path = self._safe(item.get("source_path"))
        if not item.get("source_path"):
            checks.append({"name": "source_path", "status": "unknown", "detail": "登记记录没有安全的仓库相对模型路径。"})
        elif path is None:
            checks.append({"name": "source_path", "status": "mismatch", "detail": "路径越界、包含受保护目录或解析到仓库外；未读取该目标。"})
        elif item.get("family") == "vision_review" and path.is_dir():
            checks.append({"name": "source_path", "status": "pass", "detail": "VLM 研究代码目录存在；本审计未读取 provider、settings 或密钥文件。"})
        elif not path.is_file():
            checks.append({"name": "source_path", "status": "missing", "detail": "安全路径下没有模型文件；目录存在不等于 checkpoint 存在。"})
        else:
            checks.append({"name": "source_path", "status": "pass", "detail": f"模型文件存在，大小 {path.stat().st_size} 字节。"})

        text_info = self._text_info(item.get("source_path")) if item.get("family") in {"l2_frozen", "l2_research"} else {}
        if item.get("family") == "yolo_detector":
            checks.append({"name": "weight_sha256", "status": "not_rehashed", "detail": "为避免读取大二进制权重，仅保留 registry/receipt 声明的 SHA；此页未验证权重内容。"})
        else:
            declared = item.get("declared_sha256s") or []
            actual = text_info.get("sha256")
            if text_info.get("too_large"):
                checks.append({"name": "text_model_sha256", "status": "unknown", "detail": f"文本模型超过 {MAX_TEXT_MODEL_BYTES} 字节读取上限，未计算哈希。"})
            elif actual:
                if declared:
                    status = "pass" if all(value == actual for value in declared) else "mismatch"
                    detail = "实际文本模型 SHA 与所有登记声明一致。" if status == "pass" else "实际文本模型 SHA 与至少一项登记声明不一致。"
                else:
                    status, detail = "computed", "已计算文本模型 SHA，但没有登记 SHA 可供比较。"
                checks.append({"name": "text_model_sha256", "status": status, "detail": detail})
            else:
                checks.append({"name": "text_model_sha256", "status": "unknown", "detail": "没有可读取的受支持文本模型文件。"})

        metadata_path = item.get("metadata_path")
        if metadata_path:
            meta_file = self._safe(metadata_path)
            if meta_file is None:
                checks.append({"name": "metadata_path", "status": "mismatch", "detail": "元数据路径不安全，未读取。"})
            elif not meta_file.is_file():
                checks.append({"name": "metadata_path", "status": "missing", "detail": "登记元数据文件不存在。"})
            elif meta_file.stat().st_size > MAX_METADATA_BYTES:
                checks.append({"name": "metadata_path", "status": "unknown", "detail": "元数据超过 1 MB 限制，未读取。"})
            else:
                checks.append({"name": "metadata_path", "status": "pass", "detail": "小型仓库内元数据可读取；不代表其声明已独立验证。"})
        else:
            checks.append({"name": "metadata_path", "status": "unknown", "detail": "没有配套的小型元数据文件。"})

        declared_semantics = metadata.get("feature_semantics")
        if isinstance(declared_semantics, str) and declared_semantics.strip():
            checks.append({"name": "feature_semantics", "status": "pass", "detail": "元数据显式声明 feature_semantics；此项不验证语义实现正确性。"})
        elif item.get("family") == "numeric_baseline":
            checks.append({"name": "feature_semantics", "status": "pass", "detail": "研究管线源码描述因果输入；该声明不是某个权重模型的已验证语义合同。"})
        else:
            checks.append({"name": "feature_semantics", "status": "missing", "detail": "未找到明确 feature_semantics 声明；不能用当前特征代码或旧 ACTIVE 指针补推。"})

        metadata_features = metadata.get("feature_columns") or metadata.get("feature_names")
        header_features = text_info.get("feature_names") if text_info else None
        if isinstance(metadata_features, list) and header_features is not None:
            same = metadata_features == header_features
            checks.append({"name": "feature_names", "status": "pass" if same else "mismatch",
                           "detail": "Sidecar feature list 与 LightGBM 文本 header 一致。" if same else "Sidecar feature list 与文本模型 header 不一致。"})
        elif header_features is not None:
            checks.append({"name": "feature_names", "status": "computed", "detail": f"从文本模型 header 读到 {len(header_features)} 个 feature_names；没有 sidecar 列表可比较。"})
        elif isinstance(metadata_features, list):
            checks.append({"name": "feature_names", "status": "declared_only", "detail": f"小型元数据声明 {len(metadata_features)} 个特征；未加载模型。"})
        else:
            checks.append({"name": "feature_names", "status": "unknown", "detail": "没有可核验的特征清单。"})

        chronology_status, chronology_detail = self._chronology(metadata)
        checks.append({"name": "split_chronology", "status": chronology_status, "detail": chronology_detail})
        checks.append({"name": "eligibility", "status": "disabled", "detail": "目录强制 training_eligible=false 与 production_eligible=false。"})

        severe = any(check["status"] in {"mismatch"} for check in checks)
        incomplete = any(check["status"] in {"unknown", "missing", "not_rehashed", "declared_only"} for check in checks)
        status = "review_required" if severe else ("incomplete" if incomplete else "inspected")
        result = {
            "model_id": model_id,
            "status": status,
            "checks": checks,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "fingerprint": fingerprint,
            "identity_snapshot": identity,
            "notes": "只核验本目录可安全读取的来源身份和元数据；不执行推理、重训、交易验证或准入裁决。",
            "stale": False,
        }
        if self.store is not None:
            existing = self.store.notes("model_audit").get(model_id, {})
            expected = int(existing.get("revision") or 0)
            saved = self.store.save_note("model_audit", model_id, result, expected_revision=expected)
            result["revision"] = saved["revision"]
        else:
            result["revision"] = 0
        return result

    def annotate(self, model_id: str, stage: str, notes: str, expected_revision: int = 0) -> dict[str, Any]:
        self._item(model_id)  # Fail before writing if the model disappeared or ID is unknown.
        if stage not in ALLOWED_STAGES:
            raise ValueError("stage must be research, rejected, or archived")
        if not isinstance(notes, str) or len(notes) > 12_000:
            raise ValueError("notes must be a string no longer than 12000 characters")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if self.store is None:
            raise ValueError("model annotations require a WorkspaceStore")
        self.store.save_note("model_annotation", model_id, {"stage": stage, "notes": notes}, expected_revision)
        return self.get(model_id)
