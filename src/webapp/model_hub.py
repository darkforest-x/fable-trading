"""P2.5 Phase 3: read-only model hub (frozen artifacts + ACTIVE pointer).

Lists models/frozen_*.json with companion .txt pair checks. Does not promote
or rewrite thresholds (promote POST intentionally deferred).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.judgment.frozen import file_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS_DIR = PROJECT_ROOT / "models"
DEFAULT_ACTIVE_POINTER = DEFAULT_MODELS_DIR / "ACTIVE"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path, root: Path = PROJECT_ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def active_pointer_path() -> Path:
    """Resolve ACTIVE path from OPS_ACTIVE_MODEL_POINTER env or default models/ACTIVE."""
    raw = os.environ.get("OPS_ACTIVE_MODEL_POINTER", "").strip()
    if not raw:
        return DEFAULT_ACTIVE_POINTER
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def read_active_pointer(pointer_path: Path | None = None) -> dict[str, Any]:
    """Read models/ACTIVE text pointer if present."""
    path = pointer_path if pointer_path is not None else active_pointer_path()
    if not path.is_file():
        return {
            "exists": False,
            "path": _rel(path),
            "artifact_id": None,
            "relative_path": None,
            "raw": None,
        }
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return {
            "exists": True,
            "path": _rel(path),
            "artifact_id": None,
            "relative_path": None,
            "raw": None,
            "error": str(exc),
        }
    # Accept either bare id (frozen_xxx) or relative path (models/frozen_xxx.txt|.json).
    relative = raw
    stem = Path(raw).stem
    if stem.endswith(".json") or stem.endswith(".txt"):
        stem = Path(stem).stem
    # Normalize path-like "models/frozen_foo.txt" → artifact_id frozen_foo
    artifact_id = Path(raw).name
    if artifact_id.endswith(".json") or artifact_id.endswith(".txt"):
        artifact_id = Path(artifact_id).stem
    return {
        "exists": True,
        "path": _rel(path),
        "artifact_id": artifact_id or None,
        "relative_path": relative or None,
        "raw": raw,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
    }


def _pair_status(json_path: Path, txt_path: Path) -> dict[str, Any]:
    json_ok = json_path.is_file()
    txt_ok = txt_path.is_file()
    if json_ok and txt_ok:
        status = "paired"
    elif json_ok and not txt_ok:
        status = "missing_txt"
    elif txt_ok and not json_ok:
        status = "missing_json"
    else:
        status = "missing_both"
    return {
        "pair_status": status,
        "json_exists": json_ok,
        "txt_exists": txt_ok,
        "json_path": _rel(json_path),
        "txt_path": _rel(txt_path),
        "json_size": json_path.stat().st_size if json_ok else None,
        "txt_size": txt_path.stat().st_size if txt_ok else None,
        "json_mtime": (
            datetime.fromtimestamp(json_path.stat().st_mtime, tz=timezone.utc).isoformat()
            if json_ok
            else None
        ),
        "txt_mtime": (
            datetime.fromtimestamp(txt_path.stat().st_mtime, tz=timezone.utc).isoformat()
            if txt_ok
            else None
        ),
    }


def _verify_dataset_sha(meta: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Best-effort: recompute dataset sha if CSV present; else unverifiable."""
    rel = meta.get("dataset_path")
    expected = meta.get("dataset_sha256")
    if not rel or not expected:
        return {
            "fingerprint_status": "no_fingerprint",
            "dataset_path": rel,
            "expected_sha256": expected,
            "actual_sha256": None,
            "match": None,
        }
    path = project_root / str(rel) if not Path(str(rel)).is_absolute() else Path(str(rel))
    if not path.is_file():
        return {
            "fingerprint_status": "unverifiable",
            "dataset_path": str(rel),
            "expected_sha256": str(expected),
            "actual_sha256": None,
            "match": None,
            "note": "dataset CSV not present on this host (common on VPS)",
        }
    try:
        actual = file_sha256(path)
    except OSError as exc:
        return {
            "fingerprint_status": "error",
            "dataset_path": str(rel),
            "expected_sha256": str(expected),
            "actual_sha256": None,
            "match": None,
            "note": str(exc),
        }
    match = actual == str(expected)
    return {
        "fingerprint_status": "ok" if match else "mismatch",
        "dataset_path": str(rel),
        "expected_sha256": str(expected),
        "actual_sha256": actual,
        "match": match,
    }


def _extract_meta_fields(meta: dict[str, Any]) -> dict[str, Any]:
    features = meta.get("feature_columns") or meta.get("features")
    n_features = len(features) if isinstance(features, list) else None
    return {
        "config": meta.get("config"),
        "created_at": meta.get("created_at") or meta.get("frozen_on"),
        "threshold_val_q90": meta.get("threshold_val_q90"),
        "dataset_path": meta.get("dataset_path"),
        "dataset_sha256": meta.get("dataset_sha256") or meta.get("pool_sha256"),
        "dataset_size_bytes": meta.get("dataset_size_bytes"),
        "n_features": n_features,
        "best_iteration": meta.get("best_iteration"),
        "score_quantile": meta.get("score_quantile"),
        "universe": meta.get("universe"),
        "horizon_bars": meta.get("horizon_bars"),
        "artifact_version": meta.get("artifact_version"),
    }


def list_frozen_models(
    models_dir: Path | None = None,
    *,
    project_root: Path | None = None,
    verify_fingerprint: bool = True,
    active: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Scan models/frozen_*.json (+ orphan .txt) into normalized rows."""
    root = project_root if project_root is not None else PROJECT_ROOT
    mdir = models_dir if models_dir is not None else DEFAULT_MODELS_DIR
    if not mdir.is_dir():
        return []

    stems: set[str] = set()
    for p in mdir.glob("frozen_*.json"):
        stems.add(p.stem)
    for p in mdir.glob("frozen_*.txt"):
        stems.add(p.stem)

    active_id = (active or {}).get("artifact_id")
    rows: list[dict[str, Any]] = []
    for stem in sorted(stems):
        json_path = mdir / f"{stem}.json"
        txt_path = mdir / f"{stem}.txt"
        pair = _pair_status(json_path, txt_path)
        meta_fields: dict[str, Any] = {
            "config": None,
            "created_at": None,
            "threshold_val_q90": None,
            "dataset_path": None,
            "dataset_sha256": None,
            "dataset_size_bytes": None,
            "n_features": None,
            "best_iteration": None,
            "score_quantile": None,
            "universe": None,
            "horizon_bars": None,
            "artifact_version": None,
        }
        fingerprint: dict[str, Any] = {
            "fingerprint_status": "no_json",
            "dataset_path": None,
            "expected_sha256": None,
            "actual_sha256": None,
            "match": None,
        }
        parse_error = None
        if json_path.is_file():
            try:
                raw_meta = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(raw_meta, dict):
                    meta_fields = _extract_meta_fields(raw_meta)
                    if verify_fingerprint:
                        fingerprint = _verify_dataset_sha(raw_meta, root)
                    else:
                        fingerprint = {
                            "fingerprint_status": "skipped",
                            "dataset_path": raw_meta.get("dataset_path"),
                            "expected_sha256": raw_meta.get("dataset_sha256"),
                            "actual_sha256": None,
                            "match": None,
                        }
                else:
                    parse_error = "json root is not an object"
            except (OSError, json.JSONDecodeError) as exc:
                parse_error = str(exc)

        is_active = bool(active_id) and (
            stem == active_id
            or pair["txt_path"].endswith(str(active.get("relative_path") or ""))
            or pair["json_path"].endswith(str(active.get("relative_path") or ""))
            or str(active.get("relative_path") or "") in {pair["txt_path"], pair["json_path"], stem}
        )
        rows.append(
            {
                "artifact_id": stem,
                "is_active": is_active,
                **pair,
                **meta_fields,
                "fingerprint": fingerprint,
                "parse_error": parse_error,
            }
        )
    # Active first, then mtime desc of json/txt
    rows.sort(
        key=lambda r: (
            0 if r["is_active"] else 1,
            -(
                _mtime_key(mdir / f"{r['artifact_id']}.json")
                or _mtime_key(mdir / f"{r['artifact_id']}.txt")
                or 0
            ),
        )
    )
    return rows


def _mtime_key(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.is_file() else None
    except OSError:
        return None


def model_hub_payload(
    *,
    models_dir: Path | None = None,
    project_root: Path | None = None,
    active_path: Path | None = None,
    verify_fingerprint: bool = True,
) -> dict[str, Any]:
    """Full GET /api/ops/model-hub body."""
    active = read_active_pointer(active_path)
    items = list_frozen_models(
        models_dir,
        project_root=project_root,
        verify_fingerprint=verify_fingerprint,
        active=active,
    )
    paired = sum(1 for it in items if it["pair_status"] == "paired")
    return {
        "generated_at": _iso_now(),
        "models_dir": _rel(models_dir or DEFAULT_MODELS_DIR),
        "active": active,
        "count": len(items),
        "paired_count": paired,
        "items": items,
        "read_only": True,
        "promote_available": False,
        "notes": {
            "promote": "POST /api/ops/models/promote deferred (Phase 3 read-only only).",
            "threshold": "threshold is display-only; never writable via ops API.",
        },
    }
