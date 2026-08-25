"""Build the ranked Owner-positive review surface for the six-MA rope score.

The primary population is the 1,345 de-duplicated Owner positives that actually
entered ``owner_short_gold_center_v1``.  Score references and queue thresholds
are calibrated only from the 104 exact pre-holdout Owner-star boxes in the full
2,525-box direction review sheet.  A separate 390-row keep/drop set is used as
an outcome-free countercheck after thresholds are fixed; it never changes the
score, weights, or thresholds.

This builder creates a ranking and a manual review page.  It never deletes a
sample, changes a label or split, reads the trading holdout, trains a model, or
changes ``training_eligible``.  The primary page recovers the exact 1280x742
archived chart pixels from ``dense_owner_side_short`` NPY files and writes
lossless review-only PNGs.  The old 900x521 JPEG remains a fail-closed fallback,
but is no longer enlarged for the normal Owner-positive review path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from PIL import Image

from yoyo.datasets.ma_rope_filter import (
    DEFAULT_DATA_ROOT,
    DEFAULT_POSITIVE_MANIFEST,
    DEFAULT_REVIEW_SHEET,
    HOLDOUT_START,
    RopeFilterConfig,
    read_positive_manifest,
    read_review_sheet,
    score_rows,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAR_REGISTRY = PROJECT_ROOT / "data" / "benchmark_exemplars.json"
DEFAULT_SHORT_TIP_REVIEW = (
    PROJECT_ROOT
    / "analysis"
    / "output"
    / "owner_side_short_tip_v1b_detect1000"
    / "review_sheet.csv"
)
DEFAULT_SOURCE_PACK = (
    PROJECT_ROOT
    / "datasets"
    / "owner_short_gold_center_v1"
    / "review"
    / "owner_positive_refilter_v1"
)
DEFAULT_OWNER_REVIEW_ROOT = PROJECT_ROOT / "analysis" / "output" / "owner_side_review"
DEFAULT_HIRES_SOURCE = PROJECT_ROOT / "datasets" / "dense_owner_side_short"
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "datasets"
    / "owner_short_gold_center_v1"
    / "review"
    / "ma_rope_prefilter_v1"
)

PACK_ID = "owner_short_gold_center_v1_ma_rope_prefilter_v1"
EXPECTED_POSITIVES = 1345
EXPECTED_OWNER_BOXES = 2525
EXPECTED_EXACT_STAR_ROWS = 104
EXPECTED_COUNTERCHECK_REVIEWED = 390
PERMUTATION_SEED = 20260821
PERMUTATIONS = 10_000
ALLOWED_DECISIONS = {"KEEP", "REMOVE", "UNCERTAIN"}
HIRES_CANVAS = (1280, 742)


class RopeReviewBuildError(RuntimeError):
    """Raised when the fixed review/calibration lineage does not close."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def yolo_iou(left: Sequence[float], right: Sequence[float]) -> float:
    """Return IoU for two ``xc,yc,w,h`` boxes."""

    def corners(box: Sequence[float]) -> tuple[float, float, float, float]:
        xc, yc, width, height = (float(value) for value in box)
        return xc - width / 2, yc - height / 2, xc + width / 2, yc + height / 2

    lx0, ly0, lx1, ly1 = corners(left)
    rx0, ry0, rx1, ry1 = corners(right)
    iw = max(0.0, min(lx1, rx1) - max(lx0, rx0))
    ih = max(0.0, min(ly1, ry1) - max(ly0, ry0))
    intersection = iw * ih
    union = (lx1 - lx0) * (ly1 - ly0) + (rx1 - rx0) * (ry1 - ry0) - intersection
    return intersection / union if union > 0 else 0.0


def load_star_boxes(path: Path) -> dict[str, list[tuple[float, float, float, float]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    exemplars = payload.get("exemplars")
    if not isinstance(exemplars, dict):
        raise RopeReviewBuildError("star registry has no exemplar mapping")
    result: dict[str, list[tuple[float, float, float, float]]] = {}
    for stem, record in exemplars.items():
        boxes = []
        for box in (record or {}).get("boxes") or []:
            boxes.append((float(box["cx"]), float(box["cy"]), float(box["w"]), float(box["h"])))
        result[str(stem)] = boxes
    return result


def exact_star_ids(review_sheet: Path, star_registry: Path) -> set[str]:
    frame = pd.read_csv(review_sheet)
    stars = load_star_boxes(star_registry)
    matched: set[str] = set()
    for row in frame.itertuples(index=False):
        candidate = (row.yolo_xc, row.yolo_yc, row.yolo_w, row.yolo_h)
        best = max((yolo_iou(candidate, box) for box in stars.get(str(row.stem), [])), default=0.0)
        if best >= 0.999:
            matched.add(str(row.box_id))
    if len(matched) != EXPECTED_EXACT_STAR_ROWS:
        raise RopeReviewBuildError(
            f"exact star population changed: {len(matched)} != {EXPECTED_EXACT_STAR_ROWS}"
        )
    return matched


def lower_quantile(values: Sequence[float], fraction: float) -> float:
    """Deterministic lower quantile independent of NumPy method defaults."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise RopeReviewBuildError("cannot calibrate a quantile from zero values")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be in [0, 1]")
    index = int(math.floor(fraction * (len(ordered) - 1)))
    return ordered[index]


def tier_for_score(score: float, *, core_threshold: float, broad_threshold: float) -> str:
    if score >= core_threshold:
        return "A_CORE"
    if score >= broad_threshold:
        return "B_BROAD"
    return "C_REST"


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total <= 0:
        return [0.0, 1.0]
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def rank_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    ranks = pd.Series(scores).rank(method="average").to_numpy(dtype=float)
    positives = labels == 1
    n_positive = int(positives.sum())
    n_negative = len(labels) - n_positive
    if n_positive == 0 or n_negative == 0:
        raise RopeReviewBuildError("AUC requires both keep and drop rows")
    u = ranks[positives].sum() - n_positive * (n_positive + 1) / 2
    return float(u / (n_positive * n_negative))


def evaluate_countercheck(
    rows: Sequence[Mapping[str, Any]],
    *,
    core_threshold: float,
    broad_threshold: float,
) -> dict[str, Any]:
    reviewed = [row for row in rows if row.get("review_status") in {"keep", "drop"}]
    if len(reviewed) != EXPECTED_COUNTERCHECK_REVIEWED:
        raise RopeReviewBuildError(
            f"countercheck reviewed population changed: {len(reviewed)} != {EXPECTED_COUNTERCHECK_REVIEWED}"
        )
    scores = np.asarray([float(row["rope_score"]) for row in reviewed], dtype=float)
    labels = np.asarray([1 if row["review_status"] == "keep" else 0 for row in reviewed], dtype=np.int8)
    n_keep = int(labels.sum())
    base_rate = n_keep / len(labels)

    selections: dict[str, Any] = {}
    for name, threshold in (("A_CORE", core_threshold), ("A_OR_B_BROAD", broad_threshold)):
        selected = scores >= threshold
        n_selected = int(selected.sum())
        selected_keep = int(labels[selected].sum())
        precision = selected_keep / n_selected if n_selected else 0.0
        selections[name] = {
            "threshold": threshold,
            "n_selected": n_selected,
            "n_keep": selected_keep,
            "precision": precision,
            "precision_wilson95": wilson_interval(selected_keep, n_selected),
            "recall": selected_keep / n_keep,
            "lift_vs_base": precision / base_rate if base_rate else None,
        }

    observed = float(scores[labels == 1].mean() - scores[labels == 0].mean())
    rng = np.random.default_rng(PERMUTATION_SEED)
    greater_or_equal = 0
    for _ in range(PERMUTATIONS):
        shuffled = rng.permutation(labels)
        difference = float(scores[shuffled == 1].mean() - scores[shuffled == 0].mean())
        greater_or_equal += difference >= observed - 1e-15
    permutation_p = (greater_or_equal + 1) / (PERMUTATIONS + 1)

    broad = selections["A_OR_B_BROAD"]
    auto_filter_supported = bool(
        broad["precision_wilson95"][0] > base_rate
        and rank_auc(scores, labels) > 0.5
        and permutation_p < 0.05
    )
    return {
        "population": "owner_short_tip_v1b reviewed keep/drop only",
        "n": len(reviewed),
        "n_keep": n_keep,
        "n_drop": len(reviewed) - n_keep,
        "base_keep_rate": base_rate,
        "auc": rank_auc(scores, labels),
        "mean_score_keep": float(scores[labels == 1].mean()),
        "mean_score_drop": float(scores[labels == 0].mean()),
        "mean_difference_keep_minus_drop": observed,
        "permutation_null": {
            "hypothesis": "Owner keep/drop labels are exchangeable with respect to rope_score",
            "tail": "one_sided_keep_score_greater",
            "seed": PERMUTATION_SEED,
            "n_permutations": PERMUTATIONS,
            "p_value": permutation_p,
        },
        "selections": selections,
        "auto_filter_supported": auto_filter_supported,
        "verdict": (
            "automatic deletion gate supported"
            if auto_filter_supported
            else "ranking aid only; do not auto-delete or auto-keep"
        ),
    }


PAGE_TEMPLATE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
:root{color-scheme:dark;--bg:#0d1110;--panel:#171d1a;--ink:#eef4f0;--muted:#aab4ad;--line:#39443d;--a:#46d995;--b:#d8a93e;--c:#78847c;--red:#c64b43}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header{padding:10px 16px 8px;border-bottom:1px solid var(--line)}h1{font-size:19px;margin:0 0 5px}.contract{font-size:13px;color:#eadca8}.hint,.metrics{font-size:12px;color:var(--muted);margin-top:4px}.top{position:absolute;right:16px;top:13px;font-size:13px}.progress{height:5px;background:#28302b;margin-top:7px;border-radius:4px;overflow:hidden}.bar{height:100%;background:var(--a);width:0}main{padding:10px}.panel{max-width:1200px;margin:auto;padding:10px;background:var(--panel);border:1px solid var(--line);border-radius:10px}.controls,.nav{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}.viewer{height:calc(100vh - 306px);min-height:430px;background:#fff;border-radius:8px;overflow:hidden;position:relative;margin-top:8px}.viewer canvas{display:block;width:100%;height:100%}.viewhint{position:absolute;right:10px;top:9px;padding:4px 8px;border-radius:6px;background:rgba(15,25,20,.72);color:#d9e8de;font-size:12px;pointer-events:none}.position{font-weight:700}.tier{padding:2px 8px;border-radius:10px;color:#101713}.tier.A_CORE{background:var(--a)}.tier.B_BROAD{background:var(--b)}.tier.C_REST{background:var(--c);color:white}.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}.actions button{padding:11px;font-size:16px;font-weight:750}.keep{background:#167a58}.remove{background:var(--red)}.maybe{background:#8d6d1c}.actions button.active{outline:3px solid white;outline-offset:-4px}.nav{margin-top:8px}.nav>div{display:flex;gap:7px;flex-wrap:wrap}button,select,input{font:inherit;color:var(--ink);background:#252c28;border:1px solid var(--line);border-radius:7px;padding:7px 10px}button{cursor:pointer}.primary{background:#d8f2e4;color:#102319}.note{width:min(480px,100%)}kbd{border:1px solid #667169;border-bottom-width:2px;border-radius:4px;padding:1px 5px;background:#252c28}.warning{color:#ffcf7b}.hidden{display:none!important}@media(max-width:760px){.top{position:static;margin-top:4px}.viewer{height:53vh;min-height:360px}.actions button{font-size:14px}}
</style></head><body><header><h1>__TITLE__</h1><div class="top"><span id="done"></span>　<span id="counts"></span></div>
<div class="contract">__CONTRACT__ <span class="warning">代码只负责排序，不能自动删图</span>；390 条独立反证未证明它能替代你的判断。</div>
<div class="hint"><kbd>K</kbd>/<kbd>1</kbd> 保留　<kbd>X</kbd>/<kbd>2</kbd> 去掉　<kbd>?</kbd>/<kbd>3</kbd> 待定　<kbd>J</kbd>/<kbd>←</kbd> 上一张　<kbd>L</kbd>/<kbd>→</kbd>/<kbd>空格</kbd> 下一张　<kbd>U</kbd> 撤销</div><div class="progress"><div class="bar" id="bar"></div></div></header>
<main><section class="panel"><div class="controls"><div class="position"><span id="position"></span> <span id="tier"></span> <span id="decision"></span></div><div><select id="tierFilter"><option value="A_CORE" selected>A 核心档</option><option value="B_BROAD">B 扩展档</option><option value="C_REST">C 其余</option><option value="ALL">全部档位</option></select> <select id="sideFilter"><option value="ALL" __SIDE_ALL__>全部方向</option><option value="long" __SIDE_LONG__>只看 long</option><option value="short" __SIDE_SHORT__>只看 short</option><option value="skip" __SIDE_SKIP__>只看 skip</option></select> <select id="answerFilter"><option value="ALL">全部状态</option><option value="UNREVIEWED" selected>只看未审核</option><option value="KEEP">只看保留</option><option value="REMOVE">只看去掉</option><option value="UNCERTAIN">只看待定</option></select> <label>跳到 <input id="jump" type="number" min="1" step="1"></label> <label><input id="autoNext" type="checkbox" checked> 自动下一张</label></div></div>
<div class="viewer" id="viewer"><canvas id="chart" aria-label="原始K线的绿色框附近视图"></canvas><img id="sourceChart" class="hidden" alt=""><div class="viewhint">__VIEW_HINT__</div></div><div class="metrics" id="metrics"></div>
<div class="actions"><button class="keep" data-decision="KEEP">K / 1 · 保留</button><button class="remove" data-decision="REMOVE">X / 2 · 去掉</button><button class="maybe" data-decision="UNCERTAIN">? / 3 · 待定</button></div>
<div class="nav"><div><button id="prev">J / ← 上一张</button><button id="next">L / → 下一张</button><button id="undo">U · 撤销</button></div><div><input id="note" class="note" placeholder="备注（可空）"><button id="import">导入进度</button><input id="importFile" class="hidden" type="file" accept="application/json"><button id="export" class="primary">导出 JSON</button></div></div></section></main>
<script>
const items=__ITEMS__,packId=__PACK_ID__,key=__STORAGE_KEY__,allowed=new Set(['KEEP','REMOVE','UNCERTAIN']);let index=0,answers={},undoStack=[];try{answers=JSON.parse(localStorage.getItem(key+'::answers')||'{}')}catch(_){answers={}}index=Number(localStorage.getItem(key+'::index')||0);if(index<0||index>=items.length)index=0;const $=id=>document.getElementById(id),tierFilter=$('tierFilter'),sideFilter=$('sideFilter'),answerFilter=$('answerFilter'),note=$('note'),chart=$('chart'),sourceChart=$('sourceChart'),viewer=$('viewer'),jump=$('jump'),ctx=chart.getContext('2d');
function save(){localStorage.setItem(key+'::answers',JSON.stringify(answers));localStorage.setItem(key+'::index',String(index))}function answered(id){return answers[id]&&allowed.has(answers[id].decision)}function matches(i){const x=items[i],a=answers[x.review_id];if(tierFilter.value!=='ALL'&&x.tier!==tierFilter.value)return false;if(sideFilter.value!=='ALL'&&x.side!==sideFilter.value)return false;if(answerFilter.value==='UNREVIEWED')return !answered(x.review_id);if(answerFilter.value!=='ALL')return a&&a.decision===answerFilter.value;return true}function findStep(d){for(let n=1;n<=items.length;n++){const i=(index+d*n+items.length)%items.length;if(matches(i))return i}return index}function step(d){index=findStep(d);save();render()}function decide(value){const id=items[index].review_id,old=answers[id]?{...answers[id]}:null;undoStack.push({index,old});answers[id]={review_id:id,sample_id:items[index].sample_id,decision:value,note:note.value||'',decided_at:new Date().toISOString()};save();if($('autoNext').checked)step(1);else render()}function undo(){const x=undoStack.pop();if(!x)return;index=x.index;const id=items[index].review_id;if(x.old)answers[id]=x.old;else delete answers[id];save();render()}
function stats(){const c={KEEP:0,REMOVE:0,UNCERTAIN:0};Object.values(answers).forEach(a=>{if(a&&allowed.has(a.decision))c[a.decision]++});const n=c.KEEP+c.REMOVE+c.UNCERTAIN;$('done').textContent=`已审 ${n} / ${items.length}`;$('counts').textContent=`保留 ${c.KEEP} · 去掉 ${c.REMOVE} · 待定 ${c.UNCERTAIN}`;$('bar').style.width=`${100*n/items.length}%`}function clamp(v,lo,hi){return Math.max(lo,Math.min(hi,v))}function drawFocused(){if(!sourceChart.naturalWidth)return;const x=items[index],vw=Math.max(1,viewer.clientWidth),vh=Math.max(1,viewer.clientHeight),dpr=Math.min(window.devicePixelRatio||1,2),iw=sourceChart.naturalWidth,ih=sourceChart.naturalHeight,bw=Number(x.focus_w),bh=Number(x.focus_h),cx=Number(x.focus_x)*iw,cy=Number(x.focus_y)*ih;chart.width=Math.round(vw*dpr);chart.height=Math.round(vh*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#fff';ctx.fillRect(0,0,vw,vh);let sh=Math.min(ih,Math.max(ih*.48,ih*bh*4)),sw=Math.min(iw,Math.max(iw*.72,iw*bw*10,sh*(vw/vh))),sx=clamp(cx-sw/2,0,iw-sw),sy=clamp(cy-sh/2,0,ih-sh),scale=Math.min(vw/sw,vh/sh),dw=sw*scale,dh=sh*scale,dx=(vw-dw)/2,dy=(vh-dh)/2;ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';ctx.drawImage(sourceChart,sx,sy,sw,sh,dx,dy,dw,dh);const bx=dx+(cx-iw*bw/2-sx)*scale,by=dy+(cy-ih*bh/2-sy)*scale,bww=iw*bw*scale,bhh=ih*bh*scale;ctx.strokeStyle='#28dc50';ctx.lineWidth=3;ctx.strokeRect(Math.round(bx)+.5,Math.round(by)+.5,Math.max(1,Math.round(bww)),Math.max(1,Math.round(bhh)))}function render(){const x=items[index],a=answers[x.review_id]||{};$('position').textContent=`${index+1} / ${items.length} · ${x.sample_id}`;$('tier').textContent=x.tier==='A_CORE'?'A 核心':x.tier==='B_BROAD'?'B 扩展':'C 其余';$('tier').className='tier '+x.tier;$('decision').textContent=a.decision?`· 当前 ${a.decision}`:'· 未审核';sourceChart.onload=drawFocused;sourceChart.dataset.fallback=x.image;sourceChart.dataset.usedFallback=x.hires_image?'0':'1';sourceChart.src=x.hires_image||x.image;note.value=a.note||'';jump.value=String(index+1);const side=x.side?` · 方向 ${x.side}`:'';$('metrics').textContent=`绳结分 ${x.score.toFixed(3)}${side} · 六线带宽 ${(x.bandwidth*10000).toFixed(1)}bp · 交叉 ${(x.cross_density*100).toFixed(1)}% · 实体接触 ${(x.body_touch*100).toFixed(1)}% · 实体穿束 ${(x.body_cross*100).toFixed(1)}% · 密集持续 ${(x.persistence*100).toFixed(1)}%`;document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===a.decision));stats()}sourceChart.onerror=()=>{if(sourceChart.dataset.usedFallback==='0'&&sourceChart.dataset.fallback&&sourceChart.src!==sourceChart.dataset.fallback){sourceChart.dataset.usedFallback='1';sourceChart.src=sourceChart.dataset.fallback;return}ctx.fillStyle='#fff';ctx.fillRect(0,0,chart.width,chart.height);ctx.fillStyle='#b42318';ctx.font='16px sans-serif';ctx.fillText('原始审核图加载失败，请停止审核并报告编号。',20,35)};new ResizeObserver(drawFocused).observe(viewer);document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>decide(b.dataset.decision));$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('undo').onclick=undo;for(const f of [tierFilter,sideFilter,answerFilter])f.onchange=()=>{if(!matches(index))index=findStep(1);f.blur();save();render()};jump.onchange=()=>{const n=Number(jump.value);if(Number.isInteger(n)&&n>=1&&n<=items.length){index=n-1;save();render()}jump.blur()};note.oninput=()=>{const id=items[index].review_id;if(answers[id]){answers[id].note=note.value||'';save()}};
$('export').onclick=()=>{const rows=items.map(x=>answers[x.review_id]).filter(Boolean),out={schema_version:1,pack_id:packId,exported_at:new Date().toISOString(),n_total:items.length,n_answered:rows.length,complete:rows.length===items.length,answers:rows},blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='ma_rope_prefilter_v1_answers.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)};$('import').onclick=()=>$('importFile').click();$('importFile').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{const x=JSON.parse(await f.text());if(x.pack_id!==packId||!Array.isArray(x.answers))throw new Error('审核包不匹配');const known=new Set(items.map(i=>i.review_id));for(const a of x.answers){if(known.has(a.review_id)&&allowed.has(a.decision))answers[a.review_id]=a}save();render()}catch(err){alert('导入失败：'+err.message)}e.target.value=''};document.addEventListener('keydown',e=>{if(e.target===note){if(e.key==='Escape')note.blur();return}const k=e.key.toLowerCase();if(k==='k'||e.key==='1'){e.preventDefault();decide('KEEP')}else if(k==='x'||e.key==='2'){e.preventDefault();decide('REMOVE')}else if(e.key==='?'||e.key==='3'){e.preventDefault();decide('UNCERTAIN')}else if(k==='j'||e.key==='ArrowLeft'){e.preventDefault();step(-1)}else if(k==='l'||e.key==='ArrowRight'||e.key===' '){e.preventDefault();step(1)}else if(k==='u')undo()});if(!matches(index))index=findStep(1);render();
</script></body></html>"""


def render_page(
    items: Sequence[Mapping[str, Any]],
    storage_key: str,
    *,
    pack_id: str,
    title: str,
    contract: str,
    default_side: str = "ALL",
    view_hint: str = "1280×742 无损原图 · 轻度聚焦",
) -> str:
    if default_side not in {"ALL", "long", "short", "skip"}:
        raise ValueError(f"unsupported default side: {default_side}")
    return (
        PAGE_TEMPLATE.replace("__ITEMS__", json.dumps(list(items), ensure_ascii=False, separators=(",", ":")))
        .replace("__PACK_ID__", json.dumps(pack_id))
        .replace("__STORAGE_KEY__", json.dumps(storage_key))
        .replace("__TITLE__", title)
        .replace("__CONTRACT__", contract)
        .replace("__VIEW_HINT__", view_hint)
        .replace("__SIDE_ALL__", "selected" if default_side == "ALL" else "")
        .replace("__SIDE_LONG__", "selected" if default_side == "long" else "")
        .replace("__SIDE_SHORT__", "selected" if default_side == "short" else "")
        .replace("__SIDE_SKIP__", "selected" if default_side == "skip" else "")
    )


def _focus_geometry(row: Mapping[str, Any]) -> dict[str, float]:
    values = {
        "focus_x": float(row["yolo_xc"]),
        "focus_y": float(row["yolo_yc"]),
        "focus_w": float(row["yolo_w"]),
        "focus_h": float(row["yolo_h"]),
    }
    if not 0.0 <= values["focus_x"] <= 1.0 or not 0.0 <= values["focus_y"] <= 1.0:
        raise RopeReviewBuildError("review focus center is outside the source image")
    if not 0.0 < values["focus_w"] <= 1.0 or not 0.0 < values["focus_h"] <= 1.0:
        raise RopeReviewBuildError("review focus size is outside the source image")
    return values


def attach_focus_geometry(
    items: Sequence[Mapping[str, Any]], owner_metadata: pd.DataFrame
) -> list[dict[str, Any]]:
    """Attach original Owner-box coordinates for review-only canvas cropping."""

    page_items: list[dict[str, Any]] = []
    for item in items:
        sample_id = str(item.get("sample_id") or "")
        if not sample_id or sample_id not in owner_metadata.index:
            raise RopeReviewBuildError(f"missing review focus geometry for {sample_id!r}")
        metadata = owner_metadata.loc[sample_id]
        if isinstance(metadata, pd.DataFrame):
            raise RopeReviewBuildError(f"duplicate review focus geometry for {sample_id}")
        page_items.append({**dict(item), **_focus_geometry(metadata)})
    return page_items


def materialize_hires_review_images(
    items: Sequence[Mapping[str, Any]],
    *,
    public_dir: Path,
    source_root: Path = DEFAULT_HIRES_SOURCE,
) -> dict[str, Any]:
    """Recover exact archived chart pixels as lossless review-only PNGs.

    ``dense_owner_side_short`` preserved the original 1280x742 BGR canvas as
    NPY even when its PNG symlink later became stale.  This function reads only
    those frozen pixels; it does not load OHLC rows, future context, or holdout.
    The public ranking manifest is intentionally unchanged because these files
    alter only the review view, never sample identity or training lineage.
    """

    image_dir = public_dir / "hires"
    image_dir.mkdir(parents=True, exist_ok=True)
    page_items: list[dict[str, Any]] = []
    receipts: list[str] = []
    for item in items:
        sample_id = str(item.get("sample_id") or "")
        review_id = str(item.get("review_id") or "")
        stem, marker, box_index = sample_id.rpartition("__b")
        if not review_id or not marker or not stem or not box_index.isdigit():
            raise RopeReviewBuildError(f"invalid high-resolution review identity: {sample_id!r}")
        sources = sorted((source_root / "images").glob(f"*/{stem}.npy"))
        if len(sources) != 1:
            raise RopeReviewBuildError(
                f"{sample_id}: expected one archived NPY source, found {len(sources)}"
            )
        pixels = np.load(sources[0], allow_pickle=False)
        if pixels.dtype != np.uint8 or pixels.shape != (HIRES_CANVAS[1], HIRES_CANVAS[0], 3):
            raise RopeReviewBuildError(
                f"{sample_id}: archived canvas {pixels.shape}/{pixels.dtype} != "
                f"{HIRES_CANVAS[0]}x{HIRES_CANVAS[1]} uint8 BGR"
            )
        target = image_dir / f"{review_id}.png"
        rgb = np.ascontiguousarray(pixels[:, :, ::-1])
        Image.fromarray(rgb).save(
            target, format="PNG", optimize=False, compress_level=4
        )
        with Image.open(target) as written:
            if written.size != HIRES_CANVAS or written.format != "PNG":
                raise RopeReviewBuildError(f"{sample_id}: lossless review PNG validation failed")
        target_sha = sha256_file(target)
        receipts.append(f"{review_id} {target_sha}")
        page_items.append({**dict(item), "hires_image": f"hires/{review_id}.png"})
    return {
        "items": page_items,
        "count": len(page_items),
        "set_sha256": hashlib.sha256("\n".join(sorted(receipts)).encode()).hexdigest(),
        "canvas": list(HIRES_CANVAS),
        "holdout_read": False,
    }


def build_pack(
    *,
    positive_manifest: Path = DEFAULT_POSITIVE_MANIFEST,
    owner_review_sheet: Path = DEFAULT_REVIEW_SHEET,
    short_tip_review: Path = DEFAULT_SHORT_TIP_REVIEW,
    star_registry: Path = DEFAULT_STAR_REGISTRY,
    source_pack: Path = DEFAULT_SOURCE_PACK,
    data_root: Path = DEFAULT_DATA_ROOT,
    output_dir: Path = DEFAULT_OUTPUT,
    generator_commit: str,
) -> dict[str, Any]:
    """Build scores, countercheck evidence, and the ranked review page."""

    config = RopeFilterConfig()
    positives = read_positive_manifest(positive_manifest)
    owner_rows = read_review_sheet(owner_review_sheet)
    owner_metadata = pd.read_csv(owner_review_sheet).set_index("box_id", drop=False)
    counter_rows = read_review_sheet(short_tip_review)
    if len(positives) != EXPECTED_POSITIVES or len(owner_rows) != EXPECTED_OWNER_BOXES:
        raise RopeReviewBuildError("fixed population count changed")

    positive_report = score_rows(
        positives, population="positive_manifest", data_root=data_root, config=config
    )
    owner_report = score_rows(
        owner_rows, population="owner_direction_review", data_root=data_root, config=config
    )
    counter_report = score_rows(
        counter_rows, population="short_tip_countercheck", data_root=data_root, config=config
    )
    for name, report, expected in (
        ("positive", positive_report, EXPECTED_POSITIVES),
        ("owner", owner_report, EXPECTED_OWNER_BOXES),
        ("counter", counter_report, len(counter_rows)),
    ):
        if report["n_scored"] != expected:
            raise RopeReviewBuildError(f"{name} score failures: {report['status_counts']}")

    stars = exact_star_ids(owner_review_sheet, star_registry)
    owner_by_id = {str(row["sample_id"]): row for row in owner_report["rows"]}
    star_scores = [float(owner_by_id[box_id]["rope_score"]) for box_id in sorted(stars)]
    broad_threshold = lower_quantile(star_scores, 0.10)
    core_threshold = lower_quantile(star_scores, 0.50)
    countercheck = evaluate_countercheck(
        counter_report["rows"],
        core_threshold=core_threshold,
        broad_threshold=broad_threshold,
    )

    source_truth = read_jsonl(source_pack / "admin" / "truth.jsonl")
    truth_by_sample = {str(row["sample_id"]): row for row in source_truth}
    if len(truth_by_sample) != EXPECTED_POSITIVES:
        raise RopeReviewBuildError("source review truth does not contain 1,345 unique samples")

    public_items: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    tier_counts: Counter[str] = Counter()
    score_by_sample = {str(row["sample_id"]): row for row in positive_report["rows"]}
    for sample_id in sorted(score_by_sample):
        score_row = score_by_sample[sample_id]
        source = truth_by_sample.get(sample_id)
        if source is None:
            raise RopeReviewBuildError(f"missing original review image for {sample_id}")
        review_id = str(source["review_id"])
        image_path = source_pack / "public" / "images" / f"{review_id}.jpg"
        if not image_path.is_file() or sha256_file(image_path) != source["owner_preview_sha256"]:
            raise RopeReviewBuildError(f"source review image hash mismatch for {sample_id}")
        score = float(score_row["rope_score"])
        tier = tier_for_score(score, core_threshold=core_threshold, broad_threshold=broad_threshold)
        tier_counts[tier] += 1
        public_items.append(
            {
                "review_id": review_id,
                "sample_id": sample_id,
                "image": f"../../owner_positive_refilter_v1/public/images/{review_id}.jpg",
                "tier": tier,
                "score": score,
                "bandwidth": float(score_row["six_ma_bandwidth"]),
                "cross_density": float(score_row["pairwise_cross_density"]),
                "body_touch": float(score_row["body_bundle_touch_rate"]),
                "body_cross": float(score_row["body_bundle_cross_rate"]),
                "persistence": float(score_row["rope_persistence_rate"]),
            }
        )
        private_rows.append({**score_row, "review_id": review_id, "tier": tier})
    public_items.sort(key=lambda row: (-float(row["score"]), str(row["sample_id"])))
    public_page_items = attach_focus_geometry(public_items, owner_metadata)

    owner_public_items: list[dict[str, Any]] = []
    for score_row in owner_report["rows"]:
        box_id = str(score_row["sample_id"])
        if box_id not in owner_metadata.index:
            raise RopeReviewBuildError(f"missing 2,525-row preview metadata for {box_id}")
        metadata = owner_metadata.loc[box_id]
        preview_path = DEFAULT_OWNER_REVIEW_ROOT / str(metadata["preview_path"])
        if not preview_path.is_file():
            raise RopeReviewBuildError(f"missing 2,525-row preview image: {preview_path}")
        score = float(score_row["rope_score"])
        tier = tier_for_score(score, core_threshold=core_threshold, broad_threshold=broad_threshold)
        review_id = "mb_" + hashlib.sha256(f"{PACK_ID}\0{box_id}".encode()).hexdigest()[:20]
        owner_public_items.append(
            {
                "review_id": review_id,
                "sample_id": box_id,
                "image": Path(os.path.relpath(preview_path, output_dir / "public")).as_posix(),
                "tier": tier,
                "side": str(score_row.get("owner_side") or ""),
                "score": score,
                "bandwidth": float(score_row["six_ma_bandwidth"]),
                "cross_density": float(score_row["pairwise_cross_density"]),
                "body_touch": float(score_row["body_bundle_touch_rate"]),
                "body_cross": float(score_row["body_bundle_cross_rate"]),
                "persistence": float(score_row["rope_persistence_rate"]),
            }
        )
    owner_public_items.sort(key=lambda row: (-float(row["score"]), str(row["sample_id"])))
    owner_page_items = attach_focus_geometry(owner_public_items, owner_metadata)

    public_dir = output_dir / "public"
    admin_dir = output_dir / "admin"
    public_dir.mkdir(parents=True, exist_ok=True)
    admin_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_results").mkdir(parents=True, exist_ok=True)
    hires = materialize_hires_review_images(public_page_items, public_dir=public_dir)
    public_page_items = hires["items"]

    calibration = {
        "schema_version": 1,
        "source": "104 exact pre-holdout Owner-star boxes from the 2,525-box direction sheet",
        "n_exact_star_rows": len(star_scores),
        "core_threshold_star_p50_lower": core_threshold,
        "broad_threshold_star_p10_lower": broad_threshold,
        "star_recall_core": sum(value >= core_threshold for value in star_scores) / len(star_scores),
        "star_recall_broad": sum(value >= broad_threshold for value in star_scores) / len(star_scores),
        "outcome_or_return_used": False,
        "short_tip_keep_drop_used_to_fit": False,
    }
    prereg = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "config": asdict(config),
        "queue_contract": {
            "A_CORE": "score >= exact-star lower median; review first",
            "B_BROAD": "exact-star lower p10 <= score < lower median; review second",
            "C_REST": "score < exact-star lower p10; do not auto-delete",
        },
        "selection_policy": "ranking_only_no_auto_delete_no_auto_keep",
        "countercheck_is_independent_of_fitting": True,
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
    }
    storage_key = hashlib.sha256(
        (sha256_file(positive_manifest) + json.dumps(asdict(config), sort_keys=True)).encode()
    ).hexdigest()
    write_json(public_dir / "manifest.json", {"pack_id": PACK_ID, "items": public_items})
    write_json(
        public_dir / "owner_2525_manifest.json",
        {"pack_id": PACK_ID + "_owner_2525", "items": owner_public_items},
    )
    (public_dir / "index.html").write_text(
        render_page(
            public_page_items,
            storage_key,
            pack_id=PACK_ID,
            title="六均线绳结 · 1,345 张旧训练正例代码预筛",
            contract="默认先看 A 核心档：六线更窄、交叉更多、K 线实体更常穿过线束。",
        ),
        encoding="utf-8",
    )
    (public_dir / "owner_2525.html").write_text(
        render_page(
            owner_page_items,
            storage_key + "::owner2525",
            pack_id=PACK_ID + "_owner_2525",
            title="六均线绳结 · 2,525 个 Owner 方向复核框代码预筛",
            contract="这是扩数据入口，包含 long 1,152、short 1,361、skip 12；默认先看 A 核心档。",
            default_side="long",
            view_hint="900×521 原始预览 · 轻度聚焦",
        ),
        encoding="utf-8",
    )
    write_jsonl(admin_dir / "positive_1345_scores.jsonl", private_rows)
    write_jsonl(admin_dir / "owner_2525_scores.jsonl", owner_report["rows"])
    write_jsonl(admin_dir / "short_tip_1000_scores.jsonl", counter_report["rows"])
    write_json(output_dir / "calibration.json", calibration)
    write_json(output_dir / "countercheck.json", countercheck)
    write_json(output_dir / "prereg.json", prereg)
    (output_dir / "review_results" / "README.md").write_text(
        "# Owner review exports\n\nPlace page exports here; they never mutate the frozen dataset.\n",
        encoding="utf-8",
    )

    owner_tiers = Counter(
        tier_for_score(float(row["rope_score"]), core_threshold=core_threshold, broad_threshold=broad_threshold)
        for row in owner_report["rows"]
    )
    summary = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator_commit": generator_commit,
        "positive_population": EXPECTED_POSITIVES,
        "owner_direction_population": EXPECTED_OWNER_BOXES,
        "countercheck_population": len(counter_rows),
        "positive_tier_counts": dict(sorted(tier_counts.items())),
        "owner_2525_tier_counts": dict(sorted(owner_tiers.items())),
        "positive_symbol_groups": positive_report["n_symbol_groups"],
        "positive_series_computations": positive_report["series_computations"],
        "stale_positive_source_names": positive_report["source_resolution_counts"].get(
            "symbol_candidate_index_time_verified_recorded_name_stale", 0
        ),
        "calibration": calibration,
        "countercheck": countercheck,
        "positive_manifest_sha256": sha256_file(positive_manifest),
        "owner_review_sheet_sha256": sha256_file(owner_review_sheet),
        "short_tip_review_sha256": sha256_file(short_tip_review),
        "star_registry_sha256": sha256_file(star_registry),
        "source_pack_truth_sha256": sha256_file(source_pack / "admin" / "truth.jsonl"),
        "public_manifest_sha256": sha256_file(public_dir / "manifest.json"),
        "owner_2525_public_manifest_sha256": sha256_file(
            public_dir / "owner_2525_manifest.json"
        ),
        "page_sha256": sha256_file(public_dir / "index.html"),
        "owner_2525_page_sha256": sha256_file(public_dir / "owner_2525.html"),
        "hires_review_image_count": hires["count"],
        "hires_review_set_sha256": hires["set_sha256"],
        "hires_review_canvas": hires["canvas"],
        "positive_scores_sha256": sha256_file(admin_dir / "positive_1345_scores.jsonl"),
        "owner_scores_sha256": sha256_file(admin_dir / "owner_2525_scores.jsonl"),
        "counter_scores_sha256": sha256_file(admin_dir / "short_tip_1000_scores.jsonl"),
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
        "automatic_filter_supported": countercheck["auto_filter_supported"],
    }
    write_json(output_dir / "build_summary.json", summary)
    return summary


def rerender_review_pages(
    *,
    positive_manifest: Path = DEFAULT_POSITIVE_MANIFEST,
    owner_review_sheet: Path = DEFAULT_REVIEW_SHEET,
    output_dir: Path = DEFAULT_OUTPUT,
    generator_commit: str,
) -> dict[str, Any]:
    """Replace only the review UI with an Owner-box-focused canvas view."""

    summary_path = output_dir / "build_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    public_manifest_path = output_dir / "public" / "manifest.json"
    owner_manifest_path = output_dir / "public" / "owner_2525_manifest.json"
    expected_hashes = {
        "positive_manifest_sha256": positive_manifest,
        "owner_review_sheet_sha256": owner_review_sheet,
        "public_manifest_sha256": public_manifest_path,
        "owner_2525_public_manifest_sha256": owner_manifest_path,
    }
    for field, path in expected_hashes.items():
        if summary.get(field) != sha256_file(path):
            raise RopeReviewBuildError(f"refusing UI rerender after {field} drift")

    public_payload = json.loads(public_manifest_path.read_text(encoding="utf-8"))
    owner_payload = json.loads(owner_manifest_path.read_text(encoding="utf-8"))
    public_items = public_payload.get("items") or []
    owner_items = owner_payload.get("items") or []
    if public_payload.get("pack_id") != PACK_ID or len(public_items) != EXPECTED_POSITIVES:
        raise RopeReviewBuildError("primary review manifest identity changed")
    if len(owner_items) != EXPECTED_OWNER_BOXES:
        raise RopeReviewBuildError("Owner direction review manifest population changed")

    owner_metadata = pd.read_csv(owner_review_sheet).set_index("box_id", drop=False)
    public_page_items = attach_focus_geometry(public_items, owner_metadata)
    owner_page_items = attach_focus_geometry(owner_items, owner_metadata)
    storage_key = hashlib.sha256(
        (sha256_file(positive_manifest) + json.dumps(asdict(RopeFilterConfig()), sort_keys=True)).encode()
    ).hexdigest()
    public_dir = output_dir / "public"
    hires = materialize_hires_review_images(public_page_items, public_dir=public_dir)
    public_page_items = hires["items"]
    (public_dir / "index.html").write_text(
        render_page(
            public_page_items,
            storage_key,
            pack_id=PACK_ID,
            title="六均线绳结 · 1,345 张旧训练正例局部放大精筛",
            contract="默认只放大绿色 Owner 框附近，直接判断这段 K 线和六条均线是否是目标形态。",
        ),
        encoding="utf-8",
    )
    (public_dir / "owner_2525.html").write_text(
        render_page(
            owner_page_items,
            storage_key + "::owner2525",
            pack_id=PACK_ID + "_owner_2525",
            title="六均线绳结 · 2,525 个 Owner 方向框局部放大精筛",
            contract="扩数据入口也只放大绿色 Owner 框附近；默认仍按 A 核心档排序。",
            default_side="short",
            view_hint="900×521 原始预览 · 轻度聚焦",
        ),
        encoding="utf-8",
    )
    summary.update(
        {
            "ui_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ui_generator_commit": generator_commit,
            "ui_mode": "owner_box_focused_hires_png_no_full_image_toggle",
            "hires_review_image_count": hires["count"],
            "hires_review_set_sha256": hires["set_sha256"],
            "hires_review_canvas": hires["canvas"],
            "page_sha256": sha256_file(public_dir / "index.html"),
            "owner_2525_page_sha256": sha256_file(public_dir / "owner_2525.html"),
            "holdout_read": False,
            "training_performed": False,
            "training_eligible_changed": False,
        }
    )
    write_json(summary_path, summary)
    return {
        "ok": True,
        "pack_id": PACK_ID,
        "n_items": len(public_page_items),
        "n_owner_2525_items": len(owner_page_items),
        "ui_mode": summary["ui_mode"],
        "page_sha256": summary["page_sha256"],
        "owner_2525_page_sha256": summary["owner_2525_page_sha256"],
        "holdout_read": False,
        "training_eligible_changed": False,
    }


def verify_pack(output_dir: Path = DEFAULT_OUTPUT, source_pack: Path = DEFAULT_SOURCE_PACK) -> dict[str, Any]:
    manifest = json.loads((output_dir / "public" / "manifest.json").read_text(encoding="utf-8"))
    items = manifest.get("items") or []
    if manifest.get("pack_id") != PACK_ID or len(items) != EXPECTED_POSITIVES:
        raise RopeReviewBuildError("public manifest identity/population mismatch")
    if Counter(str(row["tier"]) for row in items).keys() != {"A_CORE", "B_BROAD", "C_REST"}:
        raise RopeReviewBuildError("public manifest does not contain all three tiers")
    for row in items:
        image = source_pack / "public" / "images" / f"{row['review_id']}.jpg"
        if not image.is_file():
            raise RopeReviewBuildError(f"missing linked review image: {image}")
    owner_manifest = json.loads(
        (output_dir / "public" / "owner_2525_manifest.json").read_text(encoding="utf-8")
    )
    owner_items = owner_manifest.get("items") or []
    if len(owner_items) != EXPECTED_OWNER_BOXES:
        raise RopeReviewBuildError("2,525-row public manifest population mismatch")
    for row in owner_items:
        image = (output_dir / "public" / str(row["image"])).resolve()
        if not image.is_file():
            raise RopeReviewBuildError(f"missing linked 2,525-row preview: {image}")
    page = (output_dir / "public" / "index.html").read_text(encoding="utf-8")
    for token in (
        "K / 1",
        "X / 2",
        "? / 3",
        "A 核心档",
        "代码只负责排序",
        "1280×742 无损原图 · 轻度聚焦",
        '<canvas id="chart"',
    ):
        if token not in page:
            raise RopeReviewBuildError(f"review page missing contract token: {token}")
    summary = json.loads((output_dir / "build_summary.json").read_text(encoding="utf-8"))
    if summary.get("automatic_filter_supported") is not False:
        raise RopeReviewBuildError("current countercheck must not be represented as an automatic gate")
    return {
        "ok": True,
        "pack_id": PACK_ID,
        "n_items": len(items),
        "n_owner_2525_items": len(owner_items),
    }


def summarize_answers(
    answer_path: Path,
    *,
    output_dir: Path = DEFAULT_OUTPUT,
    positive_manifest: Path = DEFAULT_POSITIVE_MANIFEST,
    source_pack: Path = DEFAULT_SOURCE_PACK,
) -> dict[str, Any]:
    """Validate a review export and join it to the frozen short-positive lineage.

    This is deliberately a receipt builder, not a dataset builder.  Review-only
    images may contain future context, so the joined rows retain the frozen
    training image/label paths and hashes from ``positive_manifest``.  Every row
    remains fail-closed until the owner separately approves a new materialized
    dataset version.
    """

    build_summary = json.loads((output_dir / "build_summary.json").read_text(encoding="utf-8"))
    lineage_paths = {
        "public_manifest_sha256": output_dir / "public" / "manifest.json",
        "positive_manifest_sha256": positive_manifest,
        "source_pack_truth_sha256": source_pack / "admin" / "truth.jsonl",
        "positive_scores_sha256": output_dir / "admin" / "positive_1345_scores.jsonl",
    }
    for field, path in lineage_paths.items():
        recorded = str(build_summary.get(field) or "")
        actual = sha256_file(path)
        if not recorded or recorded != actual:
            raise RopeReviewBuildError(f"{field} lineage SHA mismatch")

    public_payload = json.loads(lineage_paths["public_manifest_sha256"].read_text(encoding="utf-8"))
    public_items = public_payload.get("items") or []
    positives = read_jsonl(positive_manifest)
    truth = read_jsonl(lineage_paths["source_pack_truth_sha256"])
    scores = read_jsonl(lineage_paths["positive_scores_sha256"])
    populations = {
        "public review manifest": public_items,
        "positive manifest": positives,
        "source review truth": truth,
        "rope scores": scores,
    }
    for name, rows in populations.items():
        if len(rows) != EXPECTED_POSITIVES:
            raise RopeReviewBuildError(
                f"{name} population changed: {len(rows)} != {EXPECTED_POSITIVES}"
            )
    if public_payload.get("pack_id") != PACK_ID:
        raise RopeReviewBuildError("public manifest pack_id mismatch")

    def unique_by(
        rows: Sequence[Mapping[str, Any]], field: str, name: str
    ) -> dict[str, Mapping[str, Any]]:
        result = {str(row.get(field) or ""): row for row in rows}
        if "" in result or len(result) != len(rows):
            raise RopeReviewBuildError(f"{name} has missing or duplicate {field}")
        return result

    public_by_review = unique_by(public_items, "review_id", "public manifest")
    positive_by_sample = unique_by(positives, "sample_id", "positive manifest")
    truth_by_review = unique_by(truth, "review_id", "source review truth")
    score_by_review = unique_by(scores, "review_id", "rope scores")
    public_samples = {str(row["sample_id"]) for row in public_items}
    if public_samples != set(positive_by_sample):
        raise RopeReviewBuildError("public and positive-manifest sample sets differ")
    if set(public_by_review) != set(truth_by_review) or set(public_by_review) != set(score_by_review):
        raise RopeReviewBuildError("review-id sets differ across public, truth, and score ledgers")
    for review_id, item in public_by_review.items():
        sample_id = str(item["sample_id"])
        if str(truth_by_review[review_id].get("sample_id")) != sample_id:
            raise RopeReviewBuildError(f"{review_id}: source-truth sample mismatch")
        if str(score_by_review[review_id].get("sample_id")) != sample_id:
            raise RopeReviewBuildError(f"{review_id}: score sample mismatch")

    payload = json.loads(answer_path.read_text(encoding="utf-8"))
    answers = payload.get("answers")
    if payload.get("schema_version") != 1 or payload.get("pack_id") != PACK_ID:
        raise RopeReviewBuildError("answer export does not belong to this pack")
    if not isinstance(answers, list):
        raise RopeReviewBuildError("answer export has no answers list")
    if payload.get("n_total") != EXPECTED_POSITIVES:
        raise RopeReviewBuildError("answer export n_total mismatch")
    if payload.get("n_answered") != len(answers):
        raise RopeReviewBuildError("answer export n_answered mismatch")
    if bool(payload.get("complete")) != (len(answers) == EXPECTED_POSITIVES):
        raise RopeReviewBuildError("answer export complete flag mismatch")

    answer_by_review: dict[str, Mapping[str, Any]] = {}
    for answer in answers:
        if not isinstance(answer, Mapping):
            raise RopeReviewBuildError("answer rows must be objects")
        review_id = str(answer.get("review_id") or "")
        if review_id in answer_by_review or review_id not in public_by_review:
            raise RopeReviewBuildError(f"unknown or duplicate review_id: {review_id}")
        expected_sample = str(public_by_review[review_id]["sample_id"])
        if str(answer.get("sample_id") or "") != expected_sample:
            raise RopeReviewBuildError(f"{review_id}: answer sample_id mismatch")
        decision = str(answer.get("decision") or "")
        if decision not in ALLOWED_DECISIONS:
            raise RopeReviewBuildError(f"{review_id}: invalid decision {decision}")
        if answer.get("note") is not None and not isinstance(answer.get("note"), str):
            raise RopeReviewBuildError(f"{review_id}: note must be text or null")
        if answer.get("decided_at") is not None and not isinstance(answer.get("decided_at"), str):
            raise RopeReviewBuildError(f"{review_id}: decided_at must be text or null")
        answer_by_review[review_id] = answer

    counts: Counter[str] = Counter()
    joined_rows: list[dict[str, Any]] = []
    for rank, item in enumerate(public_items, start=1):
        review_id = str(item["review_id"])
        sample_id = str(item["sample_id"])
        answer = answer_by_review.get(review_id)
        decision = str(answer["decision"]) if answer else "PENDING"
        counts[decision] += 1
        source = truth_by_review[review_id]
        positive = dict(positive_by_sample[sample_id])
        if positive.get("image_sha256") != source.get("training_image_sha256"):
            raise RopeReviewBuildError(f"{review_id}: training image SHA lineage mismatch")
        if positive.get("label_sha256") != source.get("training_label_sha256"):
            raise RopeReviewBuildError(f"{review_id}: training label SHA lineage mismatch")
        joined_rows.append(
            {
                **positive,
                "review_pack_id": PACK_ID,
                "review_id": review_id,
                "review_rank": rank,
                "review_tier": item["tier"],
                "ma_rope_score": item["score"],
                "owner_refilter_decision": decision,
                "owner_refilter_note": answer.get("note") if answer else None,
                "owner_refilter_decided_at": answer.get("decided_at") if answer else None,
                "review_image_used_as_model_input": False,
                "training_eligible": False,
                "production_eligible": False,
            }
        )

    return {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "review_export_path": str(answer_path),
        "review_export_sha256": sha256_file(answer_path),
        "n_total": EXPECTED_POSITIVES,
        "n_answered": len(answer_by_review),
        "owner_review_complete": len(answer_by_review) == EXPECTED_POSITIVES,
        "counts": {
            key: counts.get(key, 0)
            for key in ("KEEP", "REMOVE", "UNCERTAIN", "PENDING")
        },
        "lineage_sha256": {
            field: sha256_file(path) for field, path in lineage_paths.items()
        },
        "holdout_read": False,
        "training_performed": False,
        "new_dataset_materialized": False,
        "training_eligible_changed": False,
        "joined_rows": joined_rows,
    }


def _ensure_new_outputs(paths: Sequence[Path | None]) -> None:
    concrete = [path for path in paths if path is not None]
    if len({path.resolve() for path in concrete}) != len(concrete):
        raise RopeReviewBuildError("receipt and joined outputs must be different files")
    existing = [str(path) for path in concrete if path.exists()]
    if existing:
        raise RopeReviewBuildError(f"refusing to overwrite review receipt: {existing}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--positive-manifest", type=Path, default=DEFAULT_POSITIVE_MANIFEST)
    build.add_argument("--owner-review-sheet", type=Path, default=DEFAULT_REVIEW_SHEET)
    build.add_argument("--short-tip-review", type=Path, default=DEFAULT_SHORT_TIP_REVIEW)
    build.add_argument("--star-registry", type=Path, default=DEFAULT_STAR_REGISTRY)
    build.add_argument("--source-pack", type=Path, default=DEFAULT_SOURCE_PACK)
    build.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    build.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument("--generator-commit", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    verify.add_argument("--source-pack", type=Path, default=DEFAULT_SOURCE_PACK)
    rerender = sub.add_parser("rerender")
    rerender.add_argument("--positive-manifest", type=Path, default=DEFAULT_POSITIVE_MANIFEST)
    rerender.add_argument("--owner-review-sheet", type=Path, default=DEFAULT_REVIEW_SHEET)
    rerender.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    rerender.add_argument("--generator-commit", required=True)
    summarize = sub.add_parser("summarize")
    summarize.add_argument("answers", type=Path)
    summarize.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    summarize.add_argument("--positive-manifest", type=Path, default=DEFAULT_POSITIVE_MANIFEST)
    summarize.add_argument("--source-pack", type=Path, default=DEFAULT_SOURCE_PACK)
    summarize.add_argument("--joined-out", type=Path)
    summarize.add_argument("--receipt-out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        result = build_pack(
            positive_manifest=args.positive_manifest,
            owner_review_sheet=args.owner_review_sheet,
            short_tip_review=args.short_tip_review,
            star_registry=args.star_registry,
            source_pack=args.source_pack,
            data_root=args.data_root,
            output_dir=args.output_dir,
            generator_commit=args.generator_commit,
        )
    elif args.command == "verify":
        result = verify_pack(args.output_dir, args.source_pack)
    elif args.command == "rerender":
        result = rerender_review_pages(
            positive_manifest=args.positive_manifest,
            owner_review_sheet=args.owner_review_sheet,
            output_dir=args.output_dir,
            generator_commit=args.generator_commit,
        )
    else:
        result = summarize_answers(
            args.answers,
            output_dir=args.output_dir,
            positive_manifest=args.positive_manifest,
            source_pack=args.source_pack,
        )
        joined_rows = result.pop("joined_rows")
        _ensure_new_outputs((args.joined_out, args.receipt_out))
        if args.joined_out:
            write_jsonl(args.joined_out, joined_rows)
            result["joined_output"] = str(args.joined_out)
            result["joined_output_sha256"] = sha256_file(args.joined_out)
        if args.receipt_out:
            write_json(args.receipt_out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
