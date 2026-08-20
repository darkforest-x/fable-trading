"""Build and score the fixed-W10 P0/P1 blind label audit.

The source contract is the 2,649-row final Gold snapshot plus its image
manifest.  Selection uses only class, migration status, annotation source and
time split; Cleanlab flags are loaded only after the unbiased sample is frozen.
Every primary image is the already-frozen W10 decision-visible render whose
``window_end_exclusive_bar == decision_bar + 1``.  Future reference material is
never copied into the public pack and has a physically separate directory.

Scoring joins answers to the private truth table by opaque ``review_id``.  The
DIRECT error rate is emitted as individual joined rows so the acceptance gate
can re-derive it rather than trusting a reported scalar.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import random
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from yoyo.datasets.legacy_gold_migration.audit import acceptance


SCHEMA_VERSION = 1
PACK_ID = "fixed_w10_core4_confirm1_v1_p1_blind_audit_v1"
PRIORITY_PACK_ID = "fixed_w10_core4_confirm1_v1_cleanlab28_priority_v1"
EXPECTED_DATASET_MANIFEST_SHA256 = (
    "20686feba41d15b82e34109402840c2d640fe1e2daea0392b35e1ea79320a7fc"
)
DEFAULT_SEED = 20260820
PRIMARY_TARGET = 398
DIRECT_TARGET = 188
REPEAT_TARGET = 50
DIRECT_SAMPLE_MIN_FRACTION = 0.15
DIRECT_ERROR_MAX_RATE = 0.05
VALID_LABELS = ("SIGNAL", "NO_SIGNAL", "IGNORE", "UNCERTAIN")


class AuditBuildError(ValueError):
    """Raised when lineage or blinding cannot be proven."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _stable_rank(seed: int, *parts: object) -> str:
    payload = "|".join([str(seed), *(str(part) for part in parts)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise AuditBuildError(f"{path} escapes {root}") from exc


def _stratum(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("migration_status") or "UNKNOWN"),
        str(row.get("shape_label") or "UNKNOWN"),
        str(row.get("source_annotation_type") or "UNKNOWN"),
        str(row.get("split") or "UNKNOWN"),
    )


def _allocate_strata(
    groups: dict[tuple[str, ...], list[dict[str, Any]]], target: int
) -> dict[tuple[str, ...], int]:
    """Hamilton allocation with at least one item per stratum when possible."""

    total = sum(len(rows) for rows in groups.values())
    if target < 0 or target > total:
        raise AuditBuildError(f"sample target {target} outside population 0..{total}")
    if not groups:
        if target:
            raise AuditBuildError("cannot sample from an empty population")
        return {}

    keys = sorted(groups)
    raw = {key: target * len(groups[key]) / total for key in keys}
    allocation = {key: int(math.floor(raw[key])) for key in keys}
    remaining = target - sum(allocation.values())
    for key in sorted(keys, key=lambda item: (-(raw[item] - allocation[item]), item)):
        if not remaining:
            break
        if allocation[key] < len(groups[key]):
            allocation[key] += 1
            remaining -= 1

    if target >= len(keys):
        empty_keys = [key for key in keys if allocation[key] == 0]
        for empty_key in empty_keys:
            donors = [
                key
                for key in keys
                if allocation[key] > 1 and allocation[key] / len(groups[key]) >= 0
            ]
            if not donors:
                break
            donor = max(
                donors,
                key=lambda key: (allocation[key] / len(groups[key]), allocation[key], key),
            )
            allocation[donor] -= 1
            allocation[empty_key] += 1

    if sum(allocation.values()) != target:
        raise AuditBuildError("stratum allocation failed to preserve the target")
    if any(allocation[key] > len(groups[key]) for key in keys):
        raise AuditBuildError("stratum allocation exceeds its population")
    return allocation


def stratified_sample(
    rows: Sequence[dict[str, Any]],
    target: int,
    *,
    seed: int,
    key: Callable[[dict[str, Any]], tuple[str, ...]] = _stratum,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return deterministic selected rows and a per-stratum audit table."""

    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    allocation = _allocate_strata(groups, target)
    selected: list[dict[str, Any]] = []
    table: list[dict[str, Any]] = []
    for stratum in sorted(groups):
        ranked = sorted(
            groups[stratum],
            key=lambda row: _stable_rank(seed, *stratum, row["gold_id"]),
        )
        take = allocation[stratum]
        selected.extend(ranked[:take])
        table.append(
            {
                "migration_status": stratum[0],
                "shape_label": stratum[1],
                "source_annotation_type": stratum[2],
                "split": stratum[3],
                "population": len(ranked),
                "sampled": take,
                "sample_fraction": round(take / len(ranked), 6),
            }
        )
    selected.sort(key=lambda row: _stable_rank(seed, "selected", row["gold_id"]))
    return selected, table


def validate_dataset(dataset_root: Path) -> dict[str, Any]:
    """Re-hash all 2,649 images and bind them to the final Gold snapshot."""

    dataset_root = dataset_root.resolve()
    manifest_path = dataset_root / "manifests" / "dataset_manifest.json"
    manifest_sha = sha256_file(manifest_path)
    if manifest_sha != EXPECTED_DATASET_MANIFEST_SHA256:
        raise AuditBuildError(
            f"dataset manifest sha256 {manifest_sha} != frozen "
            f"{EXPECTED_DATASET_MANIFEST_SHA256}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gold_path = dataset_root / "gold" / "events.jsonl"
    image_manifest_path = dataset_root / "manifests" / "image_manifest.jsonl"
    gold_sha = sha256_file(gold_path)
    if gold_sha != manifest.get("gold_events_sha256"):
        raise AuditBuildError("final Gold sha256 does not match the frozen dataset manifest")

    events = read_jsonl(gold_path)
    image_rows = read_jsonl(image_manifest_path)
    expected_n = int(manifest.get("n_images", -1))
    if expected_n != 2649 or len(events) != expected_n or len(image_rows) != expected_n:
        raise AuditBuildError(
            f"expected one 2,649-row snapshot, got manifest={expected_n} "
            f"gold={len(events)} images={len(image_rows)}"
        )
    by_gold = {str(row.get("gold_id")): row for row in events}
    by_image = {str(row.get("gold_id")): row for row in image_rows}
    if len(by_gold) != len(events) or len(by_image) != len(image_rows):
        raise AuditBuildError("duplicate or missing gold_id in Gold/image manifest")
    if set(by_gold) != set(by_image):
        raise AuditBuildError("Gold and image manifest do not contain the same gold_id set")

    counts: Counter[str] = Counter()
    sha_splits: dict[str, set[str]] = defaultdict(set)
    image_set_digest = hashlib.sha256()
    total_image_bytes = 0
    cutoff = ""
    verified_rows: list[dict[str, Any]] = []
    lineage_fields = (
        "shape_label",
        "migration_status",
        "split",
        "decision_bar",
        "window_start_bar",
        "window_end_exclusive_bar",
        "source_annotation_type",
    )
    for gold_id in sorted(by_gold):
        event = by_gold[gold_id]
        image_row = by_image[gold_id]
        for field in lineage_fields:
            if image_row.get(field) != event.get(field):
                raise AuditBuildError(f"{gold_id}: image manifest drift in {field}")
        decision = event.get("decision_bar")
        visible_end = event.get("window_end_exclusive_bar")
        if decision is None or visible_end != int(decision) + 1:
            raise AuditBuildError(f"{gold_id}: render extends beyond or misses decision bar")
        if event.get("future_used_in_model_input") is not False:
            raise AuditBuildError(f"{gold_id}: future_used_in_model_input is not false")
        if event.get("holdout_read") is not False:
            raise AuditBuildError(f"{gold_id}: holdout_read is not false")

        split = str(event["split"])
        label = str(event["shape_label"])
        source_name = Path(str(image_row.get("image_path") or "")).name
        expected_path = dataset_root / "classification" / split / label / source_name
        if not source_name or not expected_path.is_file():
            raise AuditBuildError(f"{gold_id}: missing classification image {expected_path}")
        actual_sha = sha256_file(expected_path)
        if actual_sha != image_row.get("image_sha256"):
            raise AuditBuildError(f"{gold_id}: classification image sha256 drift")
        rel = _relative_to(expected_path, dataset_root)
        image_set_digest.update(rel.encode("utf-8"))
        image_set_digest.update(b"\0")
        image_set_digest.update(actual_sha.encode("ascii"))
        image_set_digest.update(b"\n")
        total_image_bytes += expected_path.stat().st_size
        counts[f"{split}/{label}"] += 1
        sha_splits[actual_sha].add(split)
        cutoff = max(cutoff, str(event.get("decision_time") or ""))
        verified = dict(event)
        verified["_image_path"] = str(expected_path)
        verified["_image_rel"] = rel
        verified["_image_sha256"] = actual_sha
        verified_rows.append(verified)

    duplicate_across_splits = sum(1 for splits in sha_splits.values() if len(splits) > 1)
    if duplicate_across_splits != int(manifest.get("duplicate_image_sha_across_splits", -1)):
        raise AuditBuildError("recomputed cross-split duplicate count disagrees with manifest")
    if dict(sorted(counts.items())) != dict(sorted(manifest.get("counts", {}).items())):
        raise AuditBuildError("recomputed class/split counts disagree with manifest")
    if int(manifest.get("holdout_rows", -1)) != 0:
        raise AuditBuildError("frozen manifest reports holdout rows")

    return {
        "dataset_root": dataset_root,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": manifest_sha,
        "gold_path": gold_path,
        "gold_sha256": gold_sha,
        "image_manifest_path": image_manifest_path,
        "image_manifest_sha256": sha256_file(image_manifest_path),
        "events": events,
        "rows": verified_rows,
        "counts": dict(sorted(counts.items())),
        "image_set_sha256": image_set_digest.hexdigest(),
        "total_image_bytes": total_image_bytes,
        "duplicate_image_sha_across_splits": duplicate_across_splits,
        "data_cutoff": cutoff,
    }


def _opaque_review_id(seed: int, gold_id: str, copy_index: int) -> str:
    token = _stable_rank(seed, "review-id", gold_id, copy_index)[:20]
    return f"rv_{token}"


def _copy_blind_image(source: Path, target: Path, expected_sha: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and sha256_file(target) == expected_sha:
        return
    shutil.copy2(source, target)
    if sha256_file(target) != expected_sha:
        raise AuditBuildError(f"blind copy hash mismatch: {target}")


def _page_html(
    *,
    pack_id: str,
    title: str,
    items: list[dict[str, str]],
    notice: str,
) -> str:
    """Return a self-contained offline reviewer page with no truth fields."""

    safe_title = html.escape(title)
    safe_notice = html.escape(notice)
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    storage_key = json.dumps(f"blind-review::{pack_id}")
    export_name = json.dumps(f"{pack_id}_answers.json")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{safe_title}</title>
<style>
:root{{--bg:#f3f1eb;--panel:#fff;--ink:#20211f;--muted:#676b65;--line:#d9d5ca;--accent:#174f43;--warn:#8b3a2b}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
header{{position:sticky;top:0;z-index:2;background:rgba(243,241,235,.96);border-bottom:1px solid var(--line);padding:12px 18px}}
h1{{font-size:18px;margin:0 0 4px}} .sub{{font-size:13px;color:var(--muted)}}
main{{max-width:1320px;margin:18px auto;padding:0 18px 28px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;box-shadow:0 2px 10px rgba(0,0,0,.04)}}
.image-wrap{{background:white;border:1px solid var(--line);border-radius:8px;overflow:auto;text-align:center}}
img{{display:block;max-width:100%;height:auto;margin:auto}} .status{{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:12px 0;font-size:14px}}
.choices{{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:8px;margin:12px 0}}
button,select,input{{font:inherit}} button{{border:1px solid var(--line);background:#fff;border-radius:8px;padding:10px 12px;cursor:pointer}}
button:hover{{border-color:var(--accent)}} button.active{{background:var(--accent);border-color:var(--accent);color:#fff}}
.boundary{{display:none;gap:12px;align-items:center;margin:10px 0;padding:10px;background:#f5f8f6;border-radius:8px}}
.boundary.show{{display:flex}} .nav{{display:flex;gap:8px;justify-content:space-between;margin-top:12px}} .nav div{{display:flex;gap:8px}}
.export{{background:var(--accent);color:white;border-color:var(--accent)}} .danger{{color:var(--warn)}}
.progress{{height:6px;background:#ddd8cc;border-radius:9px;overflow:hidden;margin-top:8px}} .bar{{height:100%;background:var(--accent);width:0}}
@media(max-width:760px){{.choices{{grid-template-columns:1fr 1fr}} main{{padding:0 8px}} header{{padding:10px}}}}
</style>
</head>
<body>
<header><h1>{safe_title}</h1><div class="sub">{safe_notice}</div><div class="progress"><div class="bar" id="bar"></div></div></header>
<main><section class="card">
  <div class="status"><span id="position"></span><span id="done"></span></div>
  <div class="image-wrap"><img id="chart" alt="盲审 K 线图"></div>
  <div class="choices" id="choices">
    <button data-label="SIGNAL">1 · SIGNAL</button><button data-label="NO_SIGNAL">2 · NO_SIGNAL</button>
    <button data-label="IGNORE">3 · IGNORE</button><button data-label="UNCERTAIN">4 · UNCERTAIN</button>
  </div>
  <div class="boundary" id="boundary"><label>若为 SIGNAL，4 根核心从第几根开始？
    <select id="coreStart"><option value="">请选择</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option></select>
  </label><span class="sub">图中共 10 根；最右一根是 decision/confirmation。</span></div>
  <label class="sub">备注（可空） <input id="note" style="width:70%" autocomplete="off"></label>
  <div class="nav"><div><button id="prev">← 上一张</button><button id="next">下一张 →</button></div><div><button id="export" class="export">导出答案 JSON</button></div></div>
</section></main>
<script>
const items={payload}; const key={storage_key}; const exportName={export_name};
let index=Number(localStorage.getItem(key+'::index')||0); if(index<0||index>=items.length) index=0;
let answers=JSON.parse(localStorage.getItem(key+'::answers')||'{{}}');
const chart=document.getElementById('chart'),position=document.getElementById('position'),done=document.getElementById('done'),bar=document.getElementById('bar'),boundary=document.getElementById('boundary'),coreStart=document.getElementById('coreStart'),note=document.getElementById('note');
function save(){{localStorage.setItem(key+'::answers',JSON.stringify(answers));localStorage.setItem(key+'::index',String(index));}}
function setLabel(label){{const id=items[index].review_id; const old=answers[id]||{{}}; answers[id]={{...old,review_id:id,review_label:label,core_start_position:label==='SIGNAL'?(old.core_start_position||null):null,note:note.value||'',answered_at:new Date().toISOString()}};save();render();}}
function render(){{const item=items[index],a=answers[item.review_id]||{{}};chart.src=item.image;position.textContent=`${{index+1}} / ${{items.length}} · ${{item.review_id}}`;const n=Object.keys(answers).filter(k=>answers[k]&&answers[k].review_label).length;done.textContent=`已完成 ${{n}} / ${{items.length}}`;bar.style.width=`${{100*n/items.length}}%`;document.querySelectorAll('[data-label]').forEach(b=>b.classList.toggle('active',b.dataset.label===a.review_label));boundary.classList.toggle('show',a.review_label==='SIGNAL');coreStart.value=a.core_start_position||'';note.value=a.note||'';}}
document.querySelectorAll('[data-label]').forEach(b=>b.onclick=()=>setLabel(b.dataset.label));
coreStart.onchange=()=>{{const id=items[index].review_id;if(!answers[id]||answers[id].review_label!=='SIGNAL')return;answers[id].core_start_position=coreStart.value?Number(coreStart.value):null;answers[id].answered_at=new Date().toISOString();save();render();}};
note.onchange=()=>{{const id=items[index].review_id;if(!answers[id])return;answers[id].note=note.value;save();}};
document.getElementById('prev').onclick=()=>{{index=Math.max(0,index-1);save();render();}};document.getElementById('next').onclick=()=>{{index=Math.min(items.length-1,index+1);save();render();}};
document.addEventListener('keydown',e=>{{if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;if(['1','2','3','4'].includes(e.key))setLabel(['SIGNAL','NO_SIGNAL','IGNORE','UNCERTAIN'][Number(e.key)-1]);if(e.key==='ArrowRight')document.getElementById('next').click();if(e.key==='ArrowLeft')document.getElementById('prev').click();}});
document.getElementById('export').onclick=()=>{{const rows=items.map(x=>answers[x.review_id]).filter(Boolean);const out={{schema_version:1,pack_id:{json.dumps(pack_id)},exported_at:new Date().toISOString(),answers:rows}};const blob=new Blob([JSON.stringify(out,null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=exportName;a.click();URL.revokeObjectURL(a.href);}};
render();
</script></body></html>"""


def _build_one_pack(
    rows: Sequence[dict[str, Any]],
    *,
    pack_root: Path,
    pack_id: str,
    seed: int,
    repeat_target: int,
    title: str,
    notice: str,
) -> dict[str, Any]:
    public_dir = pack_root / "public"
    admin_dir = pack_root / "admin"
    image_dir = public_dir / "images"
    public_dir.mkdir(parents=True, exist_ok=True)
    admin_dir.mkdir(parents=True, exist_ok=True)

    primary_truth: list[dict[str, Any]] = []
    primary_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        review_id = _opaque_review_id(seed, row["gold_id"], 0)
        source = Path(row["_image_path"])
        target = image_dir / f"{review_id}.png"
        _copy_blind_image(source, target, row["_image_sha256"])
        truth = {
            "review_id": review_id,
            "gold_id": row["gold_id"],
            "is_primary": True,
            "repeat_of_review_id": None,
            "given_label": row["shape_label"],
            "expected_core_start_position": 6 if row["shape_label"] == "SIGNAL" else None,
            "migration_status": row["migration_status"],
            "counts_toward_direct": row["migration_status"] == "DIRECT",
            "source_annotation_type": row["source_annotation_type"],
            "split": row["split"],
            "source_image": row["_image_rel"],
            "source_image_sha256": row["_image_sha256"],
            "blind_image": f"images/{review_id}.png",
        }
        primary_truth.append(truth)
        primary_by_id[review_id] = truth

    repeat_truth: list[dict[str, Any]] = []
    if repeat_target:
        repeat_source, _ = stratified_sample(
            list(rows), repeat_target, seed=seed + 17, key=_stratum
        )
        primary_by_gold = {row["gold_id"]: row for row in primary_truth}
        source_by_gold = {row["gold_id"]: row for row in rows}
        for row in repeat_source:
            primary = primary_by_gold[row["gold_id"]]
            source_row = source_by_gold[row["gold_id"]]
            review_id = _opaque_review_id(seed, row["gold_id"], 1)
            target = image_dir / f"{review_id}.png"
            _copy_blind_image(
                Path(source_row["_image_path"]), target, source_row["_image_sha256"]
            )
            repeat = dict(primary)
            repeat.update(
                {
                    "review_id": review_id,
                    "is_primary": False,
                    "repeat_of_review_id": primary["review_id"],
                    "blind_image": f"images/{review_id}.png",
                }
            )
            repeat_truth.append(repeat)

    truth = primary_truth + repeat_truth
    truth.sort(key=lambda row: _stable_rank(seed, "display", row["review_id"]))
    public_items = [
        {"review_id": row["review_id"], "image": row["blind_image"]} for row in truth
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": pack_id,
        "n_items": len(public_items),
        "items": public_items,
        "blinding": {
            "withheld": [
                "gold_id",
                "given_label",
                "migration_status",
                "source_annotation_type",
                "split",
                "repeat_of_review_id",
            ],
            "review_ids_are_opaque": True,
        },
        "visibility": "decision-visible W10 only; no bar after decision",
    }
    write_json(public_dir / "manifest.json", manifest)
    (public_dir / "index.html").write_text(
        _page_html(pack_id=pack_id, title=title, items=public_items, notice=notice),
        encoding="utf-8",
    )
    write_jsonl(admin_dir / "truth.jsonl", truth)
    write_jsonl(admin_dir / "primary_sample.jsonl", primary_truth)
    return {
        "pack_id": pack_id,
        "n_primary": len(primary_truth),
        "n_repeats": len(repeat_truth),
        "n_items": len(truth),
        "manifest_path": str(public_dir / "manifest.json"),
        "manifest_sha256": sha256_file(public_dir / "manifest.json"),
        "truth_path": str(admin_dir / "truth.jsonl"),
        "truth_sha256": sha256_file(admin_dir / "truth.jsonl"),
        "index_path": str(public_dir / "index.html"),
        "index_sha256": sha256_file(public_dir / "index.html"),
    }


def _load_cleanlab_flags(
    per_image_path: Path, rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_rel = {row["_image_rel"].removeprefix("classification/"): row for row in rows}
    flagged = [row for row in read_jsonl(per_image_path) if row.get("flagged_by_cleanlab")]
    joined: list[dict[str, Any]] = []
    missing: list[str] = []
    for flag in flagged:
        relative = str(flag.get("image") or "")
        source = by_rel.get(relative)
        if source is None:
            missing.append(relative)
            continue
        joined.append(source)
    if missing:
        raise AuditBuildError(f"Cleanlab rows do not join to image manifest: {missing}")
    if len(joined) != 28 or len({row["gold_id"] for row in joined}) != 28:
        raise AuditBuildError(f"expected 28 unique Cleanlab flags, got {len(joined)}")
    return joined


def _cleanlab_overlap_null(
    population: Sequence[dict[str, Any]],
    selected: Sequence[dict[str, Any]],
    flagged: Sequence[dict[str, Any]],
    *,
    seed: int,
    draws: int = 2000,
) -> dict[str, Any]:
    """Permutation control for accidental Cleanlab enrichment in the random pack."""

    population_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    flagged_counts: Counter[tuple[str, ...]] = Counter()
    for row in population:
        population_groups[_stratum(row)].append(str(row["gold_id"]))
    for row in flagged:
        flagged_counts[_stratum(row)] += 1
    selected_ids = {str(row["gold_id"]) for row in selected}
    flagged_ids = {str(row["gold_id"]) for row in flagged}
    observed = len(selected_ids & flagged_ids)
    rng = random.Random(seed)
    overlaps: list[int] = []
    for _ in range(draws):
        simulated: set[str] = set()
        for stratum, count in sorted(flagged_counts.items()):
            simulated.update(rng.sample(population_groups[stratum], count))
        overlaps.append(len(selected_ids & simulated))
    mean = sum(overlaps) / len(overlaps)
    distance = abs(observed - mean)
    p_two_sided = (1 + sum(abs(value - mean) >= distance for value in overlaps)) / (
        len(overlaps) + 1
    )
    return {
        "null_hypothesis": (
            "the unbiased sample is not enriched for Cleanlab flags after preserving "
            "migration-status/class/source/split flag counts"
        ),
        "draws": draws,
        "observed_overlap": observed,
        "null_mean_overlap": round(mean, 6),
        "null_min_overlap": min(overlaps),
        "null_max_overlap": max(overlaps),
        "two_sided_permutation_p": round(p_two_sided, 6),
    }


def _artifact_manifest(
    validated: dict[str, Any], acceptance_path: Path, pack_summary: dict[str, Any]
) -> dict[str, Any]:
    root: Path = validated["dataset_root"]
    manifest = validated["manifest"]
    return {
        "schema_version": 1,
        "artifact_id": "fixed-w10-core4-confirm1-v1-2649",
        "artifact_type": "dataset",
        "task_name": manifest["task_name"],
        "dataset_root": _relative_to(root, root.parents[1]),
        "n_images": len(validated["rows"]),
        "counts": validated["counts"],
        "data_cutoff": validated["data_cutoff"],
        "holdout_rows": 0,
        "training_eligible": False,
        "production_eligible": False,
        "components": {
            "frozen_dataset_manifest": {
                "path": _relative_to(validated["manifest_path"], root.parents[1]),
                "sha256": validated["manifest_sha256"],
            },
            "final_gold_snapshot": {
                "path": _relative_to(validated["gold_path"], root.parents[1]),
                "sha256": validated["gold_sha256"],
                "n_rows": len(validated["events"]),
            },
            "image_manifest": {
                "path": _relative_to(validated["image_manifest_path"], root.parents[1]),
                "sha256": validated["image_manifest_sha256"],
            },
            "image_set": {
                "sha256": validated["image_set_sha256"],
                "size_bytes": validated["total_image_bytes"],
                "duplicate_sha_across_splits": validated[
                    "duplicate_image_sha_across_splits"
                ],
            },
            "pending_acceptance": {
                "path": _relative_to(acceptance_path, root.parents[1]),
                "sha256": sha256_file(acceptance_path),
            },
            "blind_audit_pack": {
                "path": _relative_to(Path(pack_summary["manifest_path"]), root.parents[1]),
                "sha256": pack_summary["manifest_sha256"],
            },
        },
        "builder_commit": manifest.get("builder_commit"),
        "config_sha256": manifest.get("config_sha256"),
        "note": (
            "This registers the 2,649-image fixed-W10 snapshot. It is not the "
            "3,453-image W12-19 V3 artifact and not the unrelated 2,599-image v2 manifest."
        ),
    }


def build_audit(
    dataset_root: Path,
    pack_root: Path,
    cleanlab_per_image: Path,
    *,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Build the unbiased blind pack, repeats, priority queue and pending gate."""

    validated = validate_dataset(dataset_root)
    dataset_root = validated["dataset_root"]
    rows = validated["rows"]
    direct = [row for row in rows if row.get("migration_status") == "DIRECT"]
    other = [row for row in rows if row.get("migration_status") != "DIRECT"]
    if len(direct) != 1251:
        raise AuditBuildError(f"final Gold DIRECT population changed: {len(direct)} != 1251")
    selected_direct, direct_table = stratified_sample(
        direct, DIRECT_TARGET, seed=seed, key=_stratum
    )
    selected_other, other_table = stratified_sample(
        other, PRIMARY_TARGET - DIRECT_TARGET, seed=seed + 1, key=_stratum
    )
    selected = selected_direct + selected_other
    selected.sort(key=lambda row: _stable_rank(seed, "primary-order", row["gold_id"]))
    if len(selected) != PRIMARY_TARGET or len({row["gold_id"] for row in selected}) != PRIMARY_TARGET:
        raise AuditBuildError("unbiased sample is not 398 unique rows")

    pack_root.mkdir(parents=True, exist_ok=True)
    prereg = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "seed": seed,
        "population": {
            "n": len(rows),
            "n_direct": len(direct),
            "dataset_manifest_sha256": validated["manifest_sha256"],
            "gold_events_sha256": validated["gold_sha256"],
            "image_set_sha256": validated["image_set_sha256"],
        },
        "sampling": {
            "primary_unique": PRIMARY_TARGET,
            "direct_unique": DIRECT_TARGET,
            "direct_fraction": round(DIRECT_TARGET / len(direct), 8),
            "blind_repeats": REPEAT_TARGET,
            "repeat_fraction_of_primary": round(REPEAT_TARGET / PRIMARY_TARGET, 8),
            "strata": [
                "migration_status",
                "shape_label",
                "source_annotation_type",
                "split",
            ],
            "within_stratum_order": "sha256(seed|stratum|gold_id)",
        },
        "scoring": {
            "review_labels": list(VALID_LABELS),
            "uncertain_counts_as_error": True,
            "direct_error_rate_max": DIRECT_ERROR_MAX_RATE,
            "direct_sample_fraction_min": DIRECT_SAMPLE_MIN_FRACTION,
            "repeat_metrics": ["raw_agreement", "cohen_kappa"],
            "boundary_metric": "exact core-start agreement on repeated SIGNAL/SIGNAL pairs",
            "frozen_signal_core_start_position": 6,
            "repeat_threshold": None,
            "note": "repeat thresholds were not owner-frozen; report numbers, do not invent a pass line",
        },
        "safety": {
            "holdout_read": False,
            "training_performed": False,
            "future_in_public_pack": False,
            "training_eligible_changed": False,
            "owner_approval_required_after_metrics": True,
            "review_order": (
                "complete and export the unbiased main pack before opening the "
                "Cleanlab priority queue"
            ),
        },
    }
    write_json(pack_root / "prereg.json", prereg)

    pack_summary = _build_one_pack(
        selected,
        pack_root=pack_root,
        pack_id=PACK_ID,
        seed=seed,
        repeat_target=REPEAT_TARGET,
        title="fixed-W10 P0/P1 随机盲审",
        notice=(
            "只看 decision 时刻可见的 10 根；原标签、来源、split 与重复身份均隐藏。"
            "请完成全部项目并导出 JSON 后，才打开 Cleanlab 优先队列。"
        ),
    )
    flagged = _load_cleanlab_flags(cleanlab_per_image, rows)
    priority_summary = _build_one_pack(
        flagged,
        pack_root=pack_root / "priority_cleanlab28",
        pack_id=PRIORITY_PACK_ID,
        seed=seed + 28,
        repeat_target=0,
        title="Cleanlab 28 张优先修错队列",
        notice=(
            "必须先完成并导出主随机盲审。这是模型筛选队列，只用于优先修错，"
            "不进入随机错误率估计。"
        ),
    )

    future_dir = pack_root / "future_reference"
    future_dir.mkdir(parents=True, exist_ok=True)
    (future_dir / "README.md").write_text(
        "# Future reference（与主盲审物理隔离）\n\n"
        "本轮无偏盲审不生成、也不显示 decision 之后的 K 线。若盲审结束后需要二次裁决，"
        "未来参考只能生成到本目录；不得复制进 `public/images/`，不得作为模型输入，"
        "也不得混入随机错误率的首次判定。\n",
        encoding="utf-8",
    )
    results_dir = pack_root / "review_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "README.md").write_text(
        "# Review results\n\n"
        "把主页面导出的 JSON 放在这里，再运行：\n\n"
        "```bash\n"
        "python3 tools/datasets/fixed_w10_p1_audit.py score --answers <导出的JSON>\n"
        "```\n\n"
        "先完成、导出并冻结主包答案，再打开 Cleanlab 28 张；后者答案单独保存，"
        "不参与主包错误率。两个队列自然重叠的项目也必须遵守这个顺序。\n",
        encoding="utf-8",
    )

    images_meta = {
        "holdout_rows": 0,
        "duplicate_image_sha_across_splits": validated[
            "duplicate_image_sha_across_splits"
        ],
        "gold_events_sha256": validated["gold_sha256"],
    }
    pending = acceptance(validated["events"], validated["events"], images_meta)
    pending.update(
        {
            "schema_version": SCHEMA_VERSION,
            "status": "PENDING_OWNER_BLIND_REVIEW",
            "gold_events_path": _relative_to(validated["gold_path"], dataset_root.parents[1]),
            "gold_events_sha256": validated["gold_sha256"],
            "review_pack_manifest_sha256": pack_summary["manifest_sha256"],
            "holdout_read": False,
            "training_performed": False,
        }
    )
    acceptance_path = dataset_root / "reports" / "p1_acceptance_pending.json"
    write_json(acceptance_path, pending)

    artifact_path = dataset_root / "manifests" / "artifact_manifest_v1.json"
    write_json(artifact_path, _artifact_manifest(validated, acceptance_path, pack_summary))
    artifact_sha = sha256_file(artifact_path)
    null_control = _cleanlab_overlap_null(rows, selected, flagged, seed=seed + 99)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pack": pack_summary,
        "priority_cleanlab28": priority_summary,
        "population_n": len(rows),
        "population_direct": len(direct),
        "sample_primary": len(selected),
        "sample_direct": len(selected_direct),
        "sample_other": len(selected_other),
        "strata": direct_table + other_table,
        "artifact_manifest_path": str(artifact_path),
        "artifact_manifest_sha256": artifact_sha,
        "acceptance_path": str(acceptance_path),
        "acceptance_sha256": sha256_file(acceptance_path),
        "null_control": null_control,
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
    }
    write_json(pack_root / "build_summary.json", summary)
    write_json(pack_root / "strata.json", direct_table + other_table)
    return summary


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Nominal Cohen's kappa without adding a statistics dependency."""

    if not pairs:
        return None
    labels = sorted({label for pair in pairs for label in pair})
    n = len(pairs)
    observed = sum(left == right for left, right in pairs) / n
    left_counts = Counter(left for left, _ in pairs)
    right_counts = Counter(right for _, right in pairs)
    expected = sum(left_counts[label] * right_counts[label] for label in labels) / (n * n)
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else None
    return (observed - expected) / (1.0 - expected)


def _load_answers(path: Path, expected_pack_id: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("pack_id") != expected_pack_id:
        raise AuditBuildError(
            f"answer pack_id {payload.get('pack_id')!r} != {expected_pack_id!r}"
        )
    rows = payload.get("answers")
    if not isinstance(rows, list):
        raise AuditBuildError("answer export has no answers list")
    out: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise AuditBuildError(f"answer row {position} is not an object")
        review_id = str(row.get("review_id") or "")
        label = row.get("review_label")
        if not review_id or review_id in out:
            raise AuditBuildError(f"missing or duplicate review_id at answer row {position}")
        if label not in VALID_LABELS:
            raise AuditBuildError(f"{review_id}: invalid review_label {label!r}")
        core_start = row.get("core_start_position")
        if label == "SIGNAL" and core_start not in range(1, 7):
            raise AuditBuildError(f"{review_id}: SIGNAL needs core_start_position 1..6")
        out[review_id] = row
    return out


def score_audit(
    dataset_root: Path,
    pack_root: Path,
    answers_path: Path,
    *,
    owner_approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a complete public export and rebuild acceptance from joined rows."""

    validated = validate_dataset(dataset_root)
    dataset_root = validated["dataset_root"]
    truth_path = pack_root / "admin" / "truth.jsonl"
    public_manifest_path = pack_root / "public" / "manifest.json"
    prereg_path = pack_root / "prereg.json"
    truth = read_jsonl(truth_path)
    manifest = json.loads(public_manifest_path.read_text(encoding="utf-8"))
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    if manifest.get("pack_id") != PACK_ID or prereg.get("pack_id") != PACK_ID:
        raise AuditBuildError("pack manifest/prereg identity mismatch")
    if prereg.get("population", {}).get("gold_events_sha256") != validated["gold_sha256"]:
        raise AuditBuildError("pack was built against a different final Gold snapshot")

    answers = _load_answers(answers_path, PACK_ID)
    truth_by_id = {str(row["review_id"]): row for row in truth}
    public_ids = {str(row["review_id"]) for row in manifest.get("items", [])}
    if len(truth_by_id) != len(truth) or public_ids != set(truth_by_id):
        raise AuditBuildError("truth/public manifest review_id mismatch")
    missing = sorted(public_ids - set(answers))
    extra = sorted(set(answers) - public_ids)
    if missing or extra:
        raise AuditBuildError(
            f"blind review must be complete before scoring: missing={len(missing)} extra={len(extra)}"
        )

    primary_reviews: list[dict[str, Any]] = []
    primary_answers: dict[str, dict[str, Any]] = {}
    repeat_pairs: list[tuple[str, str]] = []
    boundary_pairs: list[tuple[int, int]] = []
    for row in truth:
        answer = answers[row["review_id"]]
        if row["is_primary"]:
            primary_answers[row["review_id"]] = answer
            primary_reviews.append(
                {
                    "review_id": row["review_id"],
                    "gold_id": row["gold_id"],
                    "review_label": answer["review_label"],
                    "core_start_position": answer.get("core_start_position"),
                    "counts_toward_direct": row["counts_toward_direct"],
                }
            )
    for row in truth:
        if row["is_primary"]:
            continue
        repeated = answers[row["review_id"]]
        original = primary_answers[row["repeat_of_review_id"]]
        repeat_pairs.append((original["review_label"], repeated["review_label"]))
        if original["review_label"] == repeated["review_label"] == "SIGNAL":
            boundary_pairs.append(
                (
                    int(original["core_start_position"]),
                    int(repeated["core_start_position"]),
                )
            )

    repeat_agreement = sum(left == right for left, right in repeat_pairs) / len(repeat_pairs)
    boundary_agreement = (
        sum(left == right for left, right in boundary_pairs) / len(boundary_pairs)
        if boundary_pairs
        else None
    )
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "pack_manifest_sha256": sha256_file(public_manifest_path),
        "truth_sha256": sha256_file(truth_path),
        "answers_sha256": sha256_file(answers_path),
        "gold_events_sha256": validated["gold_sha256"],
        "planned_primary_count": PRIMARY_TARGET,
        "planned_total_count": PRIMARY_TARGET + REPEAT_TARGET,
        "n_answered_total": len(answers),
        "pack_complete": len(answers) == PRIMARY_TARGET + REPEAT_TARGET,
        "primary_reviews": primary_reviews,
        "repeat_metrics": {
            "n_pairs": len(repeat_pairs),
            "n_agree": sum(left == right for left, right in repeat_pairs),
            "raw_agreement": round(repeat_agreement, 8),
            "cohen_kappa": round(float(cohen_kappa(repeat_pairs)), 8),
        },
        "boundary_metrics": {
            "n_signal_pairs": len(boundary_pairs),
            "n_exact": sum(left == right for left, right in boundary_pairs),
            "exact_agreement": (
                round(boundary_agreement, 8) if boundary_agreement is not None else None
            ),
        },
        "owner_approval": owner_approval or {},
        "holdout_read": False,
        "training_performed": False,
    }
    images_meta = {
        "holdout_rows": 0,
        "duplicate_image_sha_across_splits": validated[
            "duplicate_image_sha_across_splits"
        ],
        "gold_events_sha256": validated["gold_sha256"],
    }
    result = acceptance(
        validated["events"], validated["events"], images_meta, evidence
    )
    result.update(
        {
            "schema_version": SCHEMA_VERSION,
            "status": "REVIEW_SCORED_OWNER_APPROVAL_REQUIRED",
            "review_evidence": evidence,
            "holdout_read": False,
            "training_performed": False,
        }
    )
    out_dir = pack_root / "review_results"
    write_json(out_dir / "review_evidence.json", evidence)
    write_json(out_dir / "acceptance_scored.json", result)
    return result
