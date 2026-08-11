#!/usr/bin/env python3
"""Build a semantic owner-review pack for the ETH perfect-platform target.

The pack joins the repaired Stage-A event/time split to the original owner box
geometry in ``dense_owner_w20_midbox``.  The original 5/7-bar boxes are shown
only as review proposals; Owner's 2026-08-11 ETH boundary correction means an
old box must not be treated as geometrically correct without review.

Selection rules intentionally avoid semantic automation:
  - use only Stage-A ``train`` events;
  - use only proposals with 3--5 post-core bars (3 preferred, 5 hard ceiling);
  - do not require the box to be at the right edge or the exact middle;
  - sample deterministically across delay, position, width, symbol and time;
  - never rank by later return, model confidence or a hand-written MA threshold.

The source PNGs are already rendered pre-holdout artifacts.  This script does
not open raw OHLCV, Stage-A val images, model weights or holdout rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGE_MANIFEST = (
    ROOT / "datasets/local_signal_v2_stagea_randomcrop_v1/w20_manifest.json"
)
DEFAULT_OWNER_MANIFEST = ROOT / "datasets/dense_owner_w20_midbox/w20_manifest.json"
DEFAULT_REFERENCE = (
    ROOT / "analysis/reference/owner_ethusdt_15m_semantic_delay_contract_20260811.png"
)
DEFAULT_OUT = ROOT / "analysis/output/owner_eth_target_review_v2_shortdelay"

PROTOCOL = "owner_eth_perfect_platform_shortdelay_review_v2_20260811"
REVIEW_QUOTAS = {"delay_3": 80, "delay_4": 65, "delay_5": 55}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_key(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode()
    return hashlib.sha256(payload).hexdigest()


def yolo_label(path: Path) -> tuple[float, float, float, float]:
    parts = path.read_text().strip().split()
    if len(parts) != 5 or parts[0] != "0":
        raise ValueError(f"expected one class-0 YOLO box: {path}")
    return tuple(float(value) for value in parts[1:5])  # type: ignore[return-value]


def delay_group(post_bars: int) -> str:
    if post_bars in (3, 4, 5):
        return f"delay_{post_bars}"
    raise ValueError(f"post_bars outside review contract: {post_bars}")


def position_band(center: float) -> str:
    if center < 0.50:
        return "left_of_center"
    if center < 0.65:
        return "middle_band"
    if center < 0.80:
        return "right_band"
    return "far_right_band"


def build_eligible_rows(
    stage_manifest_path: Path,
    owner_manifest_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join repaired split identity to original owner box geometry."""
    stage_rows = json.loads(stage_manifest_path.read_text())
    owner_rows = json.loads(owner_manifest_path.read_text())
    owner_by_stem = {str(row["stem"]): row for row in owner_rows}
    if len(owner_by_stem) != len(owner_rows):
        raise ValueError("owner source manifest contains duplicate stems")
    event_counts = Counter(str(row["event_id"]) for row in stage_rows)
    duplicate_events = sorted(key for key, count in event_counts.items() if count != 1)
    if duplicate_events:
        raise ValueError(f"Stage-A event_id is not unique: {duplicate_events[:5]}")

    eligible: list[dict[str, Any]] = []
    missing_owner_stems: list[str] = []
    missing_artifacts: list[str] = []
    width_counts: Counter[int] = Counter()
    delay_counts: Counter[int] = Counter()
    position_counts: Counter[str] = Counter()
    for stage in stage_rows:
        owner = owner_by_stem.get(str(stage["source_stem"]))
        if owner is None:
            missing_owner_stems.append(str(stage["source_stem"]))
            continue
        first, last = (int(owner["small_local"][0]), int(owner["small_local"][1]))
        win_len = int(owner["win_len"])
        box_bars = last - first + 1
        post_bars = win_len - 1 - last
        center = ((first + last) / 2) / max(win_len - 1, 1)
        if stage["split"] != "train":
            continue
        if not (20 <= win_len <= 30 and 4 <= box_bars <= 7 and 3 <= post_bars <= 5):
            continue
        image_path = Path(owner["out_img"])
        label_path = Path(owner["out_lbl"])
        if not image_path.is_absolute():
            image_path = ROOT / image_path
        if not label_path.is_absolute():
            label_path = ROOT / label_path
        if not image_path.exists() or not label_path.exists():
            missing_artifacts.append(str(stage["event_id"]))
            continue
        box = yolo_label(label_path)
        group = delay_group(post_bars)
        band = position_band(center)
        item = {
            "event_id": stage["event_id"],
            "symbol": stage["symbol"],
            "stage_split": stage["split"],
            "start_time": stage["start_time"],
            "anchor_time": stage["anchor_time"],
            "end_time": owner["end_time"],
            "win_len": win_len,
            "box_bars": box_bars,
            "small_local": [first, last],
            "box_center_ratio": center,
            "post_bars": post_bars,
            "delay_group": group,
            "position_band": band,
            "image_path": str(image_path.relative_to(ROOT)),
            "label_path": str(label_path.relative_to(ROOT)),
            "yolo_box": list(box),
            "source_stagea_image_sha256": stage["image_sha256"],
            "source_owner_image_sha256": sha256_file(image_path),
            "semantic_status": "unreviewed",
            "geometry_status": "unreviewed",
            "training_eligible": False,
        }
        eligible.append(item)
        width_counts[box_bars] += 1
        delay_counts[post_bars] += 1
        position_counts[band] += 1

    profile = {
        "stage_manifest_rows": len(stage_rows),
        "stage_train_rows": sum(row["split"] == "train" for row in stage_rows),
        "stage_val_rows_excluded": sum(row["split"] == "val" for row in stage_rows),
        "owner_manifest_rows": len(owner_rows),
        "joined_stage_events": len(stage_rows) - len(missing_owner_stems),
        "eligible_train_events": len(eligible),
        "legacy_owner_physical_val_path_rows": sum(
            "/images/val/" in row["image_path"] for row in eligible
        ),
        "missing_owner_stems": missing_owner_stems,
        "missing_artifacts": missing_artifacts,
        "box_width_counts": dict(sorted(width_counts.items())),
        "post_bar_counts": dict(sorted(delay_counts.items())),
        "position_band_counts": dict(position_counts),
    }
    return eligible, profile


def _round_robin_strata(rows: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    """Deterministically cover delay × width × position before taking repeats."""
    strata: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (int(row["post_bars"]), int(row["box_bars"]), str(row["position_band"]))
        strata[key].append(row)
    for key, cohort in strata.items():
        cohort.sort(key=lambda row: stable_key(PROTOCOL, key, row["symbol"], row["anchor_time"], row["event_id"]))

    selected: list[dict[str, Any]] = []
    ordered_keys = sorted(strata, key=lambda key: stable_key(PROTOCOL, "stratum", key))
    while len(selected) < quota:
        progressed = False
        for key in ordered_keys:
            if strata[key] and len(selected) < quota:
                selected.append(strata[key].pop(0))
                progressed = True
        if not progressed:
            break
    if len(selected) != quota:
        raise ValueError(f"stratified cohort has {len(selected)} rows, expected {quota}")
    return selected


def select_review_rows(eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sample 200 rows, preferring delay 3 and never exceeding delay 5."""
    selected: list[dict[str, Any]] = []
    for group, quota in REVIEW_QUOTAS.items():
        cohort = [row for row in eligible if row["delay_group"] == group]
        picked = _round_robin_strata(cohort, quota)
        for row in picked:
            item = dict(row)
            item["review_group"] = group
            item["selection_method"] = "deterministic_delay_width_position_stratified"
            selected.append(item)
    if len({row["event_id"] for row in selected}) != len(selected):
        raise ValueError("review selection contains duplicate events")
    return selected


def relative_from_output(path: Path) -> str:
    return Path("../../..").joinpath(path.relative_to(ROOT)).as_posix()


def build_html(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    reference_path: Path,
) -> str:
    group_labels = {
        "delay_3": "优先确认 · 第3根",
        "delay_4": "短延迟 · 第4根",
        "delay_5": "硬上限 · 第5根",
    }
    cards: list[str] = []
    for index, row in enumerate(rows, start=1):
        xc, yc, bw, bh = row["yolo_box"]
        left = (xc - bw / 2) * 100
        top = (yc - bh / 2) * 100
        cards.append(
            f"""
<article class="card" data-group="{row['review_group']}" data-event="{html.escape(row['event_id'])}">
 <div class="card-head"><b>#{index:03d}</b><span class="tag {row['review_group']}">{group_labels[row['review_group']]}</span><span class="position">位置 {row['box_center_ratio']*100:.1f}%</span></div>
 <div class="chart"><img loading="lazy" src="{html.escape(relative_from_output(ROOT / row['image_path']))}" alt="{html.escape(row['event_id'])}"><div class="box" style="left:{left:.4f}%;top:{top:.4f}%;width:{bw*100:.4f}%;height:{bh*100:.4f}%"></div></div>
 <div class="facts"><b>{html.escape(row['symbol'])}</b><span>W{row['win_len']} · 原始Owner框{row['box_bars']}根 · 框后{row['post_bars']}根 · {html.escape(row['position_band'])}</span><small>{html.escape(row['event_id'])} · {html.escape(row['anchor_time'])}</small></div>
 <div class="actions"><button data-choice="yes">✓ 形态和框都准</button><button data-choice="uncertain">↔ 形态像但框要改</button><button data-choice="no">✕ 不是目标</button></div>
</article>"""
        )
    embedded = json.dumps(
        [
            {
                "event_id": row["event_id"],
                "review_group": row["review_group"],
                "post_bars": row["post_bars"],
                "box_bars": row["box_bars"],
                "box_center_ratio": row["box_center_ratio"],
            }
            for row in rows
        ],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ETH完美平台短延迟审查 V2</title>
<style>
:root{{--bg:#edf2f5;--card:#fff;--text:#172631;--muted:#657581;--red:#f3283d}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header{{background:#14212c;color:#fff;padding:27px 35px}}h1{{margin:0 0 8px;font-size:34px}}header p{{margin:0;color:#cbd7df;font-size:17px}}.intro{{max-width:1500px;margin:22px auto;padding:0 22px}}.reference{{background:#fff;border-radius:15px;padding:18px;box-shadow:0 2px 10px #0001}}.reference img{{width:100%;display:block;border-radius:9px}}.rule{{font-size:16px;line-height:1.75;margin:15px 0 0}}.warn{{border-left:5px solid #e19a16;background:#fff5d8;padding:12px 15px;border-radius:8px;margin-top:12px}}.toolbar{{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid #cbd5dc;padding:12px 22px;display:flex;gap:9px;align-items:center;flex-wrap:wrap}}button{{border:1px solid #b8c4cc;background:#fff;border-radius:8px;padding:8px 11px;cursor:pointer}}.toolbar button.active{{background:#19384a;color:#fff}}.spacer{{flex:1}}.grid{{max-width:1500px;margin:20px auto 45px;padding:0 22px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.card{{background:var(--card);border-radius:13px;overflow:hidden;box-shadow:0 2px 9px #0002;border:3px solid transparent}}.card[data-choice="yes"]{{border-color:#17a568}}.card[data-choice="no"]{{border-color:#d83c4c;opacity:.72}}.card[data-choice="uncertain"]{{border-color:#e3a321}}.card-head{{display:flex;gap:9px;align-items:center;padding:10px 13px}}.position{{margin-left:auto;color:var(--muted)}}.tag{{font-size:13px;font-weight:750;border-radius:999px;padding:4px 9px}}.delay_3{{background:#d9f2e8;color:#087252}}.delay_4{{background:#e1ebfb;color:#2c5e9f}}.delay_5{{background:#eee1f6;color:#754394}}.chart{{position:relative;aspect-ratio:1280/742;background:#fff}}.chart img{{position:absolute;inset:0;width:100%;height:100%}}.box{{position:absolute;border:4px solid var(--red);background:#f3283d0d;pointer-events:none}}.facts{{padding:11px 13px;display:grid;gap:4px;font-size:14px}}.facts small{{color:var(--muted)}}.actions{{display:flex;gap:8px;padding:0 13px 14px}}.actions button{{flex:1;font-weight:700}}.hidden{{display:none!important}}footer{{max-width:1450px;margin:0 auto 40px;padding:0 24px;color:var(--muted)}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}.toolbar{{position:static}}}}
</style></head><body><header><h1>ETH完美平台短延迟审查 V2</h1><p>200张代表样本 · 两条形态边界之间才是核心 · 第3根优先，第5根封顶</p></header><main>
<section class="intro"><div class="reference"><img src="{html.escape(relative_from_output(reference_path))}" alt="短延迟动态窗口合同"><p class="rule"><b>同时回答语义和框边界：</b>旧5/7根红框是否准确覆盖平台核心？如果形态像、但框包进了启动后的快速行情，请选“形态像但框要改”，不能把旧框直接当金标。</p><div class="warn"><b>这些仍是旧W20–30图片，只用于复核语义与框。</b>新训练图会重新渲染动态短窗，先试约14–22根，并继续按precision向更短收缩。筛选没有读取后续收益、模型置信度、修复后的Stage-A val事件或holdout。旧Owner目录中部分路径仍写作images/val，这是历史错split遗留的物理目录名；本页只按修复后的Stage-A train event_id选择。</div></div></section>
<div class="toolbar"><b>筛选</b><button class="active" data-filter="all">全部 200</button><button data-filter="delay_3">第3根 80</button><button data-filter="delay_4">第4根 65</button><button data-filter="delay_5">第5根 55</button><button data-filter="unreviewed">仅未审</button><span class="spacer"></span><span id="progress">已审 0 / 200</span><button id="export">导出审查JSON</button><button id="clear">清空选择</button></div>
<section class="grid">{''.join(cards)}</section></main><footer>候选母池 {summary['profile']['eligible_train_events']} 张；审查选择只保存在浏览器localStorage，导出后才形成标签文件。生成于 {html.escape(summary['generated_at'])}</footer>
<script>const items={embedded};const key='owner_eth_perfect_platform_shortdelay_review_v2';let choices=JSON.parse(localStorage.getItem(key)||'{{}}');let filter='all';function apply(){{document.querySelectorAll('.card').forEach(card=>{{const choice=choices[card.dataset.event]||'';card.dataset.choice=choice;const show=filter==='all'||filter===card.dataset.group||(filter==='unreviewed'&&!choice);card.classList.toggle('hidden',!show);card.querySelectorAll('.actions button').forEach(b=>b.style.background=b.dataset.choice===choice?'#dfe9ee':'#fff')}});document.getElementById('progress').textContent=`已审 ${{Object.keys(choices).length}} / ${{items.length}}`;localStorage.setItem(key,JSON.stringify(choices))}}document.querySelectorAll('.actions button').forEach(b=>b.addEventListener('click',()=>{{choices[b.closest('.card').dataset.event]=b.dataset.choice;apply()}}));document.querySelectorAll('.toolbar [data-filter]').forEach(b=>b.addEventListener('click',()=>{{filter=b.dataset.filter;document.querySelectorAll('.toolbar [data-filter]').forEach(x=>x.classList.toggle('active',x===b));apply()}}));document.getElementById('clear').addEventListener('click',()=>{{if(confirm('确认清空全部选择？')){{choices={{}};apply()}}}});document.getElementById('export').addEventListener('click',()=>{{const rows=items.map(x=>({{...x,owner_choice:choices[x.event_id]||'unreviewed'}}));const blob=new Blob([JSON.stringify({{protocol:'{PROTOCOL}',exported_at:new Date().toISOString(),rows}},null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='owner_eth_perfect_platform_shortdelay_review_v2_labels.json';a.click();URL.revokeObjectURL(a.href)}});apply();</script></body></html>"""


def write_outputs(
    output_dir: Path,
    rows: list[dict[str, Any]],
    profile: dict[str, Any],
    stage_manifest_path: Path,
    owner_manifest_path: Path,
    reference_path: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    group_counts = Counter(row["review_group"] for row in rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "scope": "semantic_owner_review_only",
        "stage_manifest": str(stage_manifest_path.relative_to(ROOT)),
        "stage_manifest_sha256": sha256_file(stage_manifest_path),
        "owner_geometry_manifest": str(owner_manifest_path.relative_to(ROOT)),
        "owner_geometry_manifest_sha256": sha256_file(owner_manifest_path),
        "reference_image": str(reference_path.relative_to(ROOT)),
        "reference_image_sha256": sha256_file(reference_path),
        "raw_market_data_read": False,
        "holdout_read": False,
        "validation_rows_used": 0,
        "model_weights_read": False,
        "later_return_used": False,
        "model_confidence_used": False,
        "automatic_training_labels": False,
        "production_eligible": False,
        "profile": profile,
        "review_counts": dict(group_counts),
        "review_total": len(rows),
        "review_legacy_owner_physical_val_path_rows": sum(
            "/images/val/" in row["image_path"] for row in rows
        ),
        "contract": {
            "semantic_target": "owner-perfect-platform morphology",
            "input_window_strategy": "dynamic shortest-sufficient context; never a fixed bar count",
            "initial_probe_total_bars": [14, 22],
            "initial_probe_pre_core_bars": [6, 10],
            "owner_core_box_bars_observed": [5, 7],
            "owner_example_core_boundary": "between the two owner-marked vertical lines; approximately 6 bars",
            "post_core_real_bars": [3, 5],
            "post_core_interpretation": "3 is preferred; 5 is the hard ceiling; 6-10 is excluded",
            "box_position": "varies naturally with the shortest sufficient context; never frozen to a coordinate",
            "training_goal": "high semantic precision and the earliest reliable hit within delay 3-5",
        },
        "quality_gates": {
            "stage_event_ids_unique": True,
            "owner_join_complete": not profile["missing_owner_stems"],
            "artifacts_complete": not profile["missing_artifacts"],
            "original_owner_box_widths_restored": set(profile["box_width_counts"]) == {5, 7},
            "post_delay_3_to_5_only": all(3 <= row["post_bars"] <= 5 for row in rows),
            "multiple_position_bands": len({row["position_band"] for row in rows}) >= 3,
            "review_counts_frozen": group_counts == Counter(REVIEW_QUOTAS),
            "val_excluded": all(row["stage_split"] == "train" for row in rows),
            "semantic_status_unreviewed": all(row["semantic_status"] == "unreviewed" for row in rows),
            "geometry_status_unreviewed": all(row["geometry_status"] == "unreviewed" for row in rows),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (output_dir / "contract.json").write_text(json.dumps(summary["contract"], ensure_ascii=False, indent=2) + "\n")
    with (output_dir / "candidates.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    fields = [
        "event_id", "symbol", "review_group", "semantic_status", "geometry_status", "win_len", "box_bars",
        "box_center_ratio", "position_band", "post_bars", "image_path", "anchor_time",
    ]
    with (output_dir / "candidates.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "index.html").write_text(build_html(rows, summary, reference_path))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-manifest", type=Path, default=DEFAULT_STAGE_MANIFEST)
    parser.add_argument("--owner-manifest", type=Path, default=DEFAULT_OWNER_MANIFEST)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    eligible, profile = build_eligible_rows(args.stage_manifest, args.owner_manifest)
    selected = select_review_rows(eligible)
    summary = write_outputs(
        args.out, selected, profile, args.stage_manifest, args.owner_manifest, args.reference
    )
    print(json.dumps({"output": str(args.out), "eligible": len(eligible), "review": len(selected), "gates": summary["quality_gates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
