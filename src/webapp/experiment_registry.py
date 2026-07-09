"""P2.5 Phase 1: scan analysis/output/*.json into a read-only experiment registry."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "analysis" / "output"
ANALYSIS_DIR = PROJECT_ROOT / "analysis"

# Best-effort metric keys (null if absent — never invent).
METRIC_KEYS = (
    "val_auc",
    "perm_p",
    "top_gross",
    "top_net_maker",
    "top_net_maker_006",
    "top_net_taker_010",
    "top_win_rate",
    "maker_fill_rate",
    "n_val",
    "n",
    "n_candidates",
    "n_train",
    "profit_factor",
    "max_drawdown",
)


def _kind_from_stem(stem: str) -> str:
    s = stem.lower()
    for prefix, kind in (
        ("p0_", "p0"),
        ("p2a_", "p2a"),
        ("p2b_", "p2b"),
        ("p3_", "p3"),
        ("p15_", "p15"),
        ("h9_", "h9"),
        ("h1", "exit"),
        ("mtf_", "mtf"),
        ("exit_", "exit"),
        ("swap_", "swap"),
        ("short_", "short"),
        ("data_audit", "audit"),
        ("v3_", "v3"),
    ):
        if s.startswith(prefix) or prefix.rstrip("_") in s:
            return kind
    return "other"


def _guess_report(stem: str) -> str | None:
    """Map output JSON stem → analysis/*.md when a clear match exists."""
    candidates = [
        ANALYSIS_DIR / f"{stem}.md",
        ANALYSIS_DIR / f"p2b_{stem}.md",
        ANALYSIS_DIR / f"p15_{stem}.md",
    ]
    # Common renames
    aliases = {
        "swap_replication": "p2b_v3_barrier_sweep.md",
        "p2b_v3_sweep": "p2b_v3_barrier_sweep.md",
        "p2b_v3_sweep2": "p2b_v3_barrier_sweep.md",
        "p2b_v2_expanded_final_metrics": "p2b_v2_report.md",
        "p2b_v2_expanded_metrics": "p2b_v2_report.md",
        "p2b_v2_strict_metrics": "p2b_v2_report.md",
        "p2b_metrics": "p2b_judgment_report.md",
        "p0_summary": "p0_alpha_report.md",
        "p2a_val_metrics": "p2a_detection_report.md",
        "p2a_val_metrics_smoke3": "p2a_detection_report.md",
        "p3_backtest": "p3_backtest_report.md",
        "mtf_sweep": "p2b_mtf_report.md",
        "exit_variants_swap": "p15_h1_h2_exit_report.md",
        "exit_variants": "p15_h1_h2_exit_report.md",
        "short_replication": "p15_h10_short_report.md",
        "h9_swap_trend_filter": "p15_h9_report.md",
        "h9_spot_trend_filter": "p15_h9_report.md",
        "h9_swap_feature_retrain": "p15_h9_report.md",
        "data_audit_summary": "p2_data_audit_report.md",
        "p2b_ma206_comparison": "p2b_ma206_comparison.md",
    }
    if stem in aliases:
        p = ANALYSIS_DIR / aliases[stem]
        if p.is_file():
            return str(p.relative_to(PROJECT_ROOT))
    for p in candidates:
        if p.is_file():
            return str(p.relative_to(PROJECT_ROOT))
    # fuzzy: any md containing stem fragment
    frag = stem.replace("_metrics", "").replace("_summary", "")
    for p in ANALYSIS_DIR.glob("*.md"):
        if frag and frag in p.stem:
            return str(p.relative_to(PROJECT_ROOT))
    return None


def _pick_metrics(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in METRIC_KEYS:
        if k in row and row[k] is not None:
            out[k] = row[k]
    # normalize aliases into top_net_maker for table
    if "top_net_maker" not in out:
        for alt in ("top_net_maker_006", "top_net_maker_real_funding_available"):
            if alt in row and row[alt] is not None:
                out["top_net_maker"] = row[alt]
                break
    if "n_val" not in out and "n" in out:
        out["n_val"] = out["n"]
    return out


def _flatten_json(raw: Any) -> list[dict[str, Any]]:
    """Turn heterogeneous experiment JSON into list of row dicts."""
    if isinstance(raw, list):
        rows = []
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("_row_index", i)
                rows.append(row)
            else:
                rows.append({"_row_index": i, "value": item})
        return rows or [{"_empty": True}]
    if isinstance(raw, dict):
        # nested results/runs
        for key in ("results", "runs", "items", "configs"):
            if isinstance(raw.get(key), list) and raw[key]:
                base = {k: v for k, v in raw.items() if k not in {key} and not isinstance(v, (list, dict))}
                rows = []
                for i, item in enumerate(raw[key]):
                    if isinstance(item, dict):
                        rows.append({**base, **item, "_row_index": i})
                    else:
                        rows.append({**base, "value": item, "_row_index": i})
                return rows
        # single summary dict
        return [dict(raw)]
    return [{"value": raw}]


def list_experiments(
    *,
    kind: str = "",
    q: str = "",
    sort: str = "mtime",
    order: str = "desc",
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if not OUTPUT_DIR.is_dir():
        return {
            "items": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": 0,
            "note": "analysis/output 不存在",
        }

    for path in sorted(OUTPUT_DIR.glob("*.json")):
        stem = path.stem
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            items.append(
                {
                    "id": stem,
                    "path": str(path.relative_to(PROJECT_ROOT)),
                    "kind": _kind_from_stem(stem),
                    "mtime": path.stat().st_mtime,
                    "size": path.stat().st_size,
                    "error": str(exc),
                    "n_rows": 0,
                    "metrics": {},
                    "report_path": _guess_report(stem),
                }
            )
            continue

        rows = _flatten_json(raw)
        # primary metrics: first row or best val_auc row
        primary = rows[0] if rows else {}
        best_auc = None
        for r in rows:
            auc = r.get("val_auc")
            if isinstance(auc, (int, float)) and (best_auc is None or auc > best_auc):
                best_auc = auc
                primary = r
        metrics = _pick_metrics(primary) if isinstance(primary, dict) else {}
        config = primary.get("config") if isinstance(primary, dict) else None
        entry = {
            "id": stem,
            "path": str(path.relative_to(PROJECT_ROOT)),
            "kind": _kind_from_stem(stem),
            "mtime": path.stat().st_mtime,
            "mtime_iso": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "size": path.stat().st_size,
            "n_rows": len(rows),
            "config": config,
            "metrics": metrics,
            "report_path": _guess_report(stem),
            "raw_keys": sorted({k for r in rows if isinstance(r, dict) for k in r.keys()})[:40],
        }
        items.append(entry)

    if kind:
        items = [x for x in items if x.get("kind") == kind]
    if q:
        ql = q.lower()
        items = [
            x
            for x in items
            if ql in x["id"].lower()
            or ql in str(x.get("config", "")).lower()
            or ql in str(x.get("report_path", "")).lower()
        ]

    reverse = order != "asc"
    if sort == "val_auc":
        items.sort(key=lambda x: (x.get("metrics") or {}).get("val_auc") is None, reverse=False)
        items.sort(
            key=lambda x: (x.get("metrics") or {}).get("val_auc") or -1,
            reverse=reverse,
        )
    elif sort == "perm_p":
        items.sort(key=lambda x: (x.get("metrics") or {}).get("perm_p") is None)
        items.sort(key=lambda x: (x.get("metrics") or {}).get("perm_p") or 99, reverse=not reverse)
    else:
        items.sort(key=lambda x: x.get("mtime") or 0, reverse=reverse)

    return {
        "items": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
    }


def experiment_detail(exp_id: str) -> dict[str, Any] | None:
    # prevent path traversal
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", exp_id):
        return None
    path = OUTPUT_DIR / f"{exp_id}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"id": exp_id, "error": str(exc)}

    rows = _flatten_json(raw)
    report_path = _guess_report(exp_id)
    report_md = None
    if report_path:
        rp = PROJECT_ROOT / report_path
        if rp.is_file():
            try:
                report_md = rp.read_text(encoding="utf-8")
            except OSError:
                report_md = None
    return {
        "id": exp_id,
        "path": str(path.relative_to(PROJECT_ROOT)),
        "mtime": path.stat().st_mtime,
        "kind": _kind_from_stem(exp_id),
        "report_path": report_path,
        "report_markdown": report_md,
        "rows": rows,
        "raw": raw,
    }
