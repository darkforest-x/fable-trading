"""Build a uniform causal OHLC review pack for the frozen 2,649-row Gold set.

The source CSV suffix and stored ``decision_bar`` belong to an older snapshot and
are not trusted.  Each event is re-anchored by its exact ``decision_time`` in the
single current OKX 15m CSV for that symbol.  The loader streams only through the
latest requested pre-holdout decision, computes moving averages causally, and
renders a fixed 200-bar window ending at the decision bar.  No migrated W10
window/core geometry, post-decision bar, holdout row, label mutation, training,
or eligibility change is used.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.data.loader import OHLCV_COLUMNS
from yoyo.datasets.fixed_w10_blind_audit import (
    AuditBuildError,
    read_jsonl,
    sha256_file,
    validate_dataset,
    write_json,
    write_jsonl,
)
from yoyo.datasets.fixed_w10_original_review import OriginalSourceCatalog
from yoyo.datasets.legacy_gold_migration.io import git_head
from yoyo.datasets.window_render import enrich
from yoyo.layers.l1_detection.render import IMG_HEIGHT, IMG_WIDTH, render_chart


SCHEMA_VERSION = 1
PACK_ID = "fixed_w10_core4_confirm1_v1_canonical_ohlc_triage_v2"
RENDER_SPEC_ID = "owner_triage_causal_w200_v1"
DEFAULT_SEED = 20260821
WINDOW_BARS = 200
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00+00:00")
VALID_DECISIONS = frozenset({"KEEP", "REMOVE", "UNCERTAIN"})
DECISION_COLOR_BGR = (143, 131, 0)


def _stable_id(seed: int, gold_id: str) -> str:
    payload = f"{seed}|canonical-ohlc-v2|{gold_id}".encode("utf-8")
    return f"cv_{hashlib.sha256(payload).hexdigest()[:20]}"


def _stable_rank(seed: int, gold_id: str) -> str:
    return hashlib.sha256(f"{seed}|canonical-order-v2|{gold_id}".encode("utf-8")).hexdigest()


def _utc(value: Any, *, field: str) -> pd.Timestamp:
    try:
        if isinstance(value, pd.Timestamp):
            parsed = value
        elif isinstance(value, datetime):
            parsed = pd.Timestamp(value)
        else:
            parsed = pd.Timestamp(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize("UTC")
        else:
            parsed = parsed.tz_convert("UTC")
    except (TypeError, ValueError) as exc:
        raise AuditBuildError(f"invalid {field}: {value!r}") from exc
    return parsed


def _utc_datetime(value: Any, *, field: str) -> datetime:
    """Fast scalar parser for the multi-million-row streaming path."""

    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise AuditBuildError(f"invalid {field}: {value!r}") from exc


def resolve_current_source(data_root: Path, symbol: str, timeframe: str) -> Path:
    """Resolve exactly one current fetched file without trusting the stale suffix."""

    matches = sorted(data_root.resolve().glob(f"okx_{symbol}_{timeframe}_*.csv"))
    if len(matches) != 1:
        raise AuditBuildError(
            f"expected one current OHLC source for {symbol} {timeframe}, "
            f"found {len(matches)}: {matches}"
        )
    return matches[0].resolve()


def load_preholdout_symbol_prefix(
    path: Path,
    decision_times: Iterable[str],
    *,
    holdout_start: pd.Timestamp = HOLDOUT_START,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Stream through the latest requested decision and return exact time anchors.

    Only the rows through ``max(decision_times)`` are read.  The function stops
    immediately after that exact row, so it cannot materialize a later row or a
    holdout row.  Source rows must already be strictly time ordered and unique.
    """

    targets = {_utc_datetime(value, field="decision_time") for value in decision_times}
    if not targets:
        raise AuditBuildError("decision_times is empty")
    holdout = _utc_datetime(holdout_start.to_pydatetime(), field="holdout_start")
    if max(targets) >= holdout:
        raise AuditBuildError("requested decision reaches holdout")

    rows: list[dict[str, Any]] = []
    anchors: dict[datetime, int] = {}
    previous: datetime | None = None
    max_target = max(targets)
    reached_max = False
    path = path.resolve()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing_columns = set(OHLCV_COLUMNS).difference(reader.fieldnames or [])
        if missing_columns:
            raise AuditBuildError(f"bad OHLC schema {path}: missing {sorted(missing_columns)}")
        for raw in reader:
            current = _utc_datetime(raw.get("open_time"), field=f"{path}:open_time")
            if current >= holdout:
                raise AuditBuildError(f"holdout row reached while resolving {path}: {current}")
            if previous is not None and current <= previous:
                raise AuditBuildError(f"OHLC time is not strictly increasing in {path}: {current}")
            previous = current
            row = {column: raw.get(column) for column in OHLCV_COLUMNS}
            row["open_time"] = current
            rows.append(row)
            index = len(rows) - 1
            if current in targets:
                anchors[current] = index
            if current == max_target:
                reached_max = True
                break
            if current > max_target:
                raise AuditBuildError(f"latest decision_time missing from {path}: {max_target}")

    if not reached_max:
        raise AuditBuildError(f"latest decision_time missing from {path}: {max_target}")
    missing = sorted(targets.difference(anchors))
    if missing:
        raise AuditBuildError(f"decision_time values missing from {path}: {missing[:5]}")

    frame = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    bad = frame[["open", "high", "low", "close"]].isna().any(axis=1)
    if bool(bad.any()):
        raise AuditBuildError(f"non-numeric OHLC before latest decision in {path}")
    by_text = {pd.Timestamp(timestamp).isoformat(): index for timestamp, index in anchors.items()}
    return frame, by_text


def _draw_dashed_decision(image: np.ndarray, x: int) -> None:
    for top in range(12, image.shape[0] - 12, 16):
        cv2.line(
            image,
            (x, top),
            (x, min(top + 9, image.shape[0] - 13)),
            DECISION_COLOR_BGR,
            2,
            cv2.LINE_AA,
        )
    cv2.putText(
        image,
        "DECISION",
        (max(12, x - 102), 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        DECISION_COLOR_BGR,
        1,
        cv2.LINE_AA,
    )


def render_canonical_primary(
    enriched: pd.DataFrame,
    decision_index: int,
    *,
    window_bars: int = WINDOW_BARS,
) -> dict[str, Any]:
    """Render one label-blind causal window ending exactly at decision_time."""

    decision_index = int(decision_index)
    window_bars = int(window_bars)
    start = decision_index - window_bars + 1
    if start < 0:
        raise AuditBuildError(
            f"not enough history: decision_index={decision_index}, window_bars={window_bars}"
        )
    window = enriched.iloc[start : decision_index + 1].reset_index(drop=True)
    if len(window) != window_bars:
        raise AuditBuildError("canonical causal window length mismatch")
    image, transform = render_chart(window, width=IMG_WIDTH, height=IMG_HEIGHT, out_path=None)
    _draw_dashed_decision(image, transform.x_at(window_bars - 1))
    return {
        "image": image,
        "window_start_index": start,
        "window_end_exclusive_index": decision_index + 1,
        "visible_end_index": decision_index,
        "window_bars": window_bars,
        "future_bars": 0,
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
    }


def _write_png(path: Path, image: np.ndarray) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise AuditBuildError(f"failed to encode PNG: {path}")
    path.write_bytes(encoded.tobytes())
    return sha256_file(path)


def _copy_reference(source: Path, target: Path, expected_sha256: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or sha256_file(target) != expected_sha256:
        shutil.copy2(source, target)
    actual = sha256_file(target)
    if actual != expected_sha256:
        raise AuditBuildError(f"historical reference hash mismatch: {target}")
    return actual


def _page_html(items: list[dict[str, Any]], *, gold_sha256: str) -> str:
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    storage_key = json.dumps(f"canonical-ohlc-triage::{PACK_ID}::{gold_sha256}")
    export_name = json.dumps(f"{PACK_ID}_answers.json")
    template = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>2,649 张统一原始 K 线筛选 v2</title>
<style>
:root{--bg:#101312;--panel:#1a1f1c;--ink:#f6f6ef;--muted:#abb3ad;--line:#3a443d;--keep:#157f55;--remove:#a63c34;--maybe:#8b6b1f;--accent:#8ed1b0;--warn:#ffd69a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow-x:hidden}
header{position:sticky;top:0;z-index:5;background:rgba(16,19,18,.98);border-bottom:1px solid var(--line);padding:9px 14px}.top,.stats,.controls,.nav{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.top{justify-content:space-between}h1{font-size:17px;margin:0}.stats,.hint,.contract{font-size:12px;color:var(--muted)}.contract{color:var(--warn);margin-top:5px}.progress{height:5px;background:#303630;border-radius:8px;overflow:hidden;margin-top:7px}.bar{height:100%;background:var(--accent);width:0}
main{width:min(1500px,100%);margin:auto;padding:10px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}.controls{justify-content:space-between;margin-bottom:8px}.position{font-size:14px}.controls input[type=number]{width:88px}
.viewer{height:calc(100vh - 265px);min-height:360px;background:#fff;border-radius:7px;overflow:auto;display:flex;align-items:center;justify-content:center;gap:8px}.viewer img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain}.viewer.split img{max-width:49%}.viewer.zoom img{max-width:none;max-height:none}.historical{display:none}.historical.show{display:block}
button,select,input{font:inherit}button,select,input[type=number]{color:var(--ink);background:#252b27;border:1px solid var(--line);border-radius:7px;padding:7px 10px}button{cursor:pointer}button:hover{border-color:var(--accent)}.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:9px}.actions button{font-size:17px;font-weight:700;padding:12px}.keep{background:var(--keep)}.remove{background:var(--remove)}.maybe{background:var(--maybe)}.actions button.active{outline:3px solid #fff;outline-offset:-4px}.nav{justify-content:space-between;margin-top:8px}.nav>div{display:flex;gap:7px;flex-wrap:wrap}.primary{background:#d8f2e4;color:#102319;border-color:#d8f2e4}.note{width:min(500px,100%);padding:7px 10px;color:var(--ink);background:#252b27;border:1px solid var(--line);border-radius:7px}kbd{border:1px solid #626b63;border-bottom-width:2px;border-radius:4px;padding:1px 5px;color:#e7ece7;background:#252b27}.error{color:#ff9e94;font-size:13px}.refwarn{display:none;color:#ffb5a9;font-size:12px;margin-top:6px}.refwarn.show{display:block}.hidden{display:none!important}
@media(max-width:760px){header{padding:8px}main{padding:6px}.viewer{height:calc(100vh - 330px);min-height:300px}.actions{grid-template-columns:1fr}.viewer.split{display:block;height:auto}.viewer.split img{max-width:100%;margin:auto}}
</style></head><body>
<header><div class="top"><h1>2,649 张统一原始 K 线筛选 v2</h1><div class="stats"><span id="done"></span><span id="counts"></span></div></div>
<div class="contract">主图统一为原始 OHLC 因果 W200：最右侧青色虚线是 decision，图内未来 K 线为 0。<kbd>R</kbd> 仅查看历史原文件，可能含未来，不能作为主裁决依据。</div>
<div class="hint"><kbd>K</kbd>/<kbd>1</kbd> 保留　<kbd>X</kbd>/<kbd>2</kbd> 去掉　<kbd>?</kbd>/<kbd>3</kbd> 待定　<kbd>J</kbd>/<kbd>←</kbd> 上一张　<kbd>L</kbd>/<kbd>→</kbd>/<kbd>空格</kbd> 下一张　<kbd>U</kbd> 撤销　<kbd>R</kbd> 历史原文件　<kbd>Z</kbd> 原尺寸</div><div class="progress"><div class="bar" id="bar"></div></div></header>
<main><section class="panel"><div class="controls"><div class="position"><span id="position"></span> <span id="decision"></span></div><div><select id="filter"><option value="ALL">全部</option><option value="UNREVIEWED" selected>只看未审核</option><option value="KEEP">只看保留</option><option value="REMOVE">只看去掉</option><option value="UNCERTAIN">只看待定</option></select> <label>跳到 <input id="jump" type="number" min="1" step="1"></label> <label><input id="autoNext" type="checkbox" checked> 选择后自动下一张</label></div></div>
<div class="viewer" id="viewer"><img id="primary" alt="统一因果原始K线"><img id="historical" class="historical" alt="历史原文件"></div><div id="refwarn" class="refwarn">当前并排图中的历史原文件可能带旧框、旧标签或 decision 后上下文；只核对来源，不据此改主裁决。</div><div class="error" id="imageError"></div>
<div class="actions"><button class="keep" data-decision="KEEP">K / 1 · 保留</button><button class="remove" data-decision="REMOVE">X / 2 · 去掉</button><button class="maybe" data-decision="UNCERTAIN">? / 3 · 待定</button></div>
<div class="nav"><div><button id="prev">J / ← 上一张</button><button id="next">L / → 下一张</button><button id="undo">U · 撤销</button><button id="toggleReference">R · 历史原文件</button><button id="zoom">Z · 原尺寸</button></div><div><input id="note" class="note" placeholder="备注（可空）"><button id="import">导入进度</button><input id="importFile" class="hidden" type="file" accept="application/json"><button id="export" class="primary">导出 JSON</button></div></div></section></main>
<script>
const items=__ITEMS__,packId=__PACK_ID__,key=__STORAGE_KEY__,exportName=__EXPORT_NAME__,allowed=new Set(['KEEP','REMOVE','UNCERTAIN']);
let index=Number(localStorage.getItem(key+'::index')||0);if(index<0||index>=items.length)index=0;let answers={};try{answers=JSON.parse(localStorage.getItem(key+'::answers')||'{}')}catch(_){answers={}}let undoStack=[],referenceShown=false;
const $=id=>document.getElementById(id),primary=$('primary'),historical=$('historical'),viewer=$('viewer'),note=$('note'),filter=$('filter');
function save(){localStorage.setItem(key+'::answers',JSON.stringify(answers));localStorage.setItem(key+'::index',String(index))}function answered(id){return answers[id]&&allowed.has(answers[id].decision)}
function matches(i){const mode=filter.value,a=answers[items[i].review_id];if(mode==='ALL')return true;if(mode==='UNREVIEWED')return !answered(items[i].review_id);return a&&a.decision===mode}function findStep(d){for(let n=1;n<=items.length;n++){const i=(index+d*n+items.length)%items.length;if(matches(i))return i}return index}
function step(d){index=findStep(d);referenceShown=false;save();render()}function decide(value){const id=items[index].review_id,old=answers[id]?{...answers[id]}:null;undoStack.push({index,old});answers[id]={review_id:id,decision:value,note:note.value||'',decided_at:new Date().toISOString()};save();if($('autoNext').checked)step(1);else render()}function undo(){const last=undoStack.pop();if(!last)return;index=last.index;const id=items[index].review_id;if(last.old)answers[id]=last.old;else delete answers[id];save();render()}
function stats(){const c={KEEP:0,REMOVE:0,UNCERTAIN:0};Object.values(answers).forEach(a=>{if(a&&allowed.has(a.decision))c[a.decision]++});const n=c.KEEP+c.REMOVE+c.UNCERTAIN;$('done').textContent=`已审 ${n} / ${items.length}`;$('counts').textContent=`保留 ${c.KEEP} · 去掉 ${c.REMOVE} · 待定 ${c.UNCERTAIN}`;$('bar').style.width=`${100*n/items.length}%`}
function render(){const item=items[index],a=answers[item.review_id]||{};$('position').textContent=`${index+1} / ${items.length} · ${item.review_id}`;$('decision').textContent=a.decision?`· 当前：${{KEEP:'保留',REMOVE:'去掉',UNCERTAIN:'待定'}[a.decision]}`:'· 未审核';primary.src=item.image;historical.src=item.historical_image;historical.classList.toggle('show',referenceShown);viewer.classList.toggle('split',referenceShown);$('refwarn').classList.toggle('show',referenceShown);$('toggleReference').textContent=referenceShown?'R · 隐藏历史原文件':'R · 历史原文件';note.value=a.note||'';$('jump').value=String(index+1);document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===a.decision));$('imageError').textContent='';stats()}
primary.onerror=()=>{$('imageError').textContent='统一主图加载失败，请停止审核并报告此编号。'};historical.onerror=()=>{$('imageError').textContent='历史原文件加载失败；主图裁决仍可继续，但请报告此编号。'};document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>decide(b.dataset.decision));$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('undo').onclick=undo;$('toggleReference').onclick=()=>{referenceShown=!referenceShown;render()};$('zoom').onclick=()=>viewer.classList.toggle('zoom');filter.onchange=()=>{if(!matches(index))index=findStep(1);save();render()};$('jump').onchange=()=>{const n=Number($('jump').value);if(Number.isInteger(n)&&n>=1&&n<=items.length){index=n-1;referenceShown=false;save();render()}};note.onchange=()=>{const id=items[index].review_id;if(answers[id]){answers[id].note=note.value||'';save()}};
$('export').onclick=()=>{const rows=items.map(x=>answers[x.review_id]).filter(a=>a&&allowed.has(a.decision)),out={schema_version:1,pack_id:packId,render_spec_id:'__RENDER_SPEC_ID__',exported_at:new Date().toISOString(),complete:rows.length===items.length,n_total:items.length,n_answered:rows.length,answers:rows},blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=exportName;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)};$('import').onclick=()=>$('importFile').click();$('importFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{const out=JSON.parse(await file.text());if(out.pack_id!==packId||!Array.isArray(out.answers))throw new Error('不是本审核包导出的 JSON');const known=new Set(items.map(x=>x.review_id));for(const a of out.answers){if(known.has(a.review_id)&&allowed.has(a.decision))answers[a.review_id]=a}save();render();alert(`已导入 ${out.answers.length} 条`)}catch(err){alert(`导入失败：${err.message}`)}e.target.value=''};
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;const k=e.key.toLowerCase();if(k==='k'||e.key==='1')decide('KEEP');else if(k==='x'||e.key==='2')decide('REMOVE');else if(e.key==='?'||e.key==='3')decide('UNCERTAIN');else if(k==='j'||e.key==='ArrowLeft'){e.preventDefault();step(-1)}else if(k==='l'||e.key==='ArrowRight'||e.key===' '){e.preventDefault();step(1)}else if(k==='u')undo();else if(k==='r')$('toggleReference').click();else if(k==='z')$('zoom').click()});render();
</script></body></html>'''
    return (
        template.replace("__ITEMS__", payload)
        .replace("__PACK_ID__", json.dumps(PACK_ID))
        .replace("__STORAGE_KEY__", storage_key)
        .replace("__EXPORT_NAME__", export_name)
        .replace("__RENDER_SPEC_ID__", RENDER_SPEC_ID)
    )


def build_canonical_review(
    project_root: Path,
    archive_root: Path,
    data_root: Path,
    dataset_root: Path,
    pack_root: Path,
    *,
    seed: int = DEFAULT_SEED,
    window_bars: int = WINDOW_BARS,
) -> dict[str, Any]:
    """Render and package all 2,649 rows without reading holdout or W10 pixels."""

    project_root = project_root.resolve()
    pack_root = pack_root.resolve()
    validated = validate_dataset(dataset_root)
    events = validated["events"]
    if len(events) != 2649:
        raise AuditBuildError(f"expected 2,649 Gold rows, got {len(events)}")
    if window_bars != WINDOW_BARS:
        raise AuditBuildError(
            f"render contract is frozen at {WINDOW_BARS} bars; requested {window_bars}"
        )

    public = pack_root / "public"
    admin = pack_root / "admin"
    image_dir = public / "images"
    historical_dir = public / "historical_original"
    for directory in (public, admin, image_dir, historical_dir):
        directory.mkdir(parents=True, exist_ok=True)

    catalog = OriginalSourceCatalog(project_root, archive_root)
    original_by_gold = {row["gold_id"]: catalog.resolve(row) for row in events}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(str(event["symbol"]), str(event["timeframe"]))].append(event)

    rendered: dict[str, dict[str, Any]] = {}
    source_inventory: list[dict[str, Any]] = []
    index_deltas: list[int] = []
    max_materialized_time: pd.Timestamp | None = None
    image_bytes = 0
    for (symbol, timeframe), group in sorted(grouped.items()):
        source_path = resolve_current_source(data_root, symbol, timeframe)
        frame, anchors = load_preholdout_symbol_prefix(
            source_path,
            [str(row["decision_time"]) for row in group],
        )
        enriched_frame = enrich(frame)
        source_max = _utc(frame.iloc[-1]["open_time"], field="materialized_time")
        max_materialized_time = max(max_materialized_time or source_max, source_max)
        source_inventory.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "source_path": str(source_path),
                "rows_materialized": len(frame),
                "max_materialized_time": source_max.isoformat(),
                "holdout_read": False,
            }
        )
        for event in group:
            decision_time = _utc(event["decision_time"], field="decision_time")
            current_index = anchors.get(decision_time.isoformat())
            if current_index is None:
                raise AuditBuildError(f"missing current decision anchor: {event['gold_id']}")
            result = render_canonical_primary(
                enriched_frame,
                current_index,
                window_bars=window_bars,
            )
            gold_id = str(event["gold_id"])
            review_id = _stable_id(seed, gold_id)
            image_target = image_dir / f"{review_id}.png"
            image_sha = _write_png(image_target, result.pop("image"))
            image_bytes += image_target.stat().st_size
            result.update(
                {
                    "current_source_path": str(source_path),
                    "current_decision_index": current_index,
                    "decision_time": decision_time.isoformat(),
                    "canonical_image_sha256": image_sha,
                }
            )
            rendered[gold_id] = result
            index_deltas.append(current_index - int(event["decision_bar"]))

    ordered = sorted(events, key=lambda row: _stable_rank(seed, str(row["gold_id"])))
    public_items: list[dict[str, Any]] = []
    truth: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    historical_bytes = 0
    for event in ordered:
        gold_id = str(event["gold_id"])
        review_id = _stable_id(seed, gold_id)
        render = rendered[gold_id]
        image_sha = str(render["canonical_image_sha256"])

        original = original_by_gold[gold_id]
        historical = original["primary"]
        source_path = Path(historical["path"])
        suffix = source_path.suffix.lower() or ".png"
        historical_target = historical_dir / f"{review_id}{suffix}"
        historical_sha = _copy_reference(
            source_path,
            historical_target,
            str(historical["sha256"]),
        )
        historical_bytes += historical_target.stat().st_size

        image_rel = f"images/{review_id}.png"
        historical_rel = f"historical_original/{historical_target.name}"
        public_items.append(
            {
                "review_id": review_id,
                "image": image_rel,
                "historical_image": historical_rel,
            }
        )
        truth.append(
            {
                "review_id": review_id,
                "gold_id": gold_id,
                "source_kind": original["source_kind"],
                "source_dataset": event["source_dataset"],
                "source_record_id": event["source_record_id"],
                "source_annotation_type": event.get("source_annotation_type"),
                "shape_label": event.get("shape_label"),
                "split": event.get("split"),
                "decision_time": render["decision_time"],
                "stored_decision_bar_not_used": event["decision_bar"],
                "stored_source_path_not_used": event.get("source_path"),
                "current_source_path": render["current_source_path"],
                "current_decision_index": render["current_decision_index"],
                "window_start_index": render["window_start_index"],
                "window_end_exclusive_index": render["window_end_exclusive_index"],
                "visible_end_index": render["visible_end_index"],
                "window_bars": render["window_bars"],
                "future_bars": 0,
                "render_spec_id": RENDER_SPEC_ID,
                "canonical_image": image_rel,
                "canonical_image_sha256": image_sha,
                "canonical_width": render["width"],
                "canonical_height": render["height"],
                "canonical_format": "PNG/RGB",
                "historical_original": historical_rel,
                "historical_original_sha256": historical_sha,
                "historical_original_source_path": historical["path"],
                "migration_w10_geometry_used": False,
                "holdout_read": False,
            }
        )
        source_counts[str(original["source_kind"])] += 1
        label_counts[str(event.get("shape_label"))] += 1

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "purpose": "owner keep/remove triage over uniform causal raw-OHLC renders",
        "gold_events_sha256": str(validated["gold_sha256"]),
        "render_spec_id": RENDER_SPEC_ID,
        "render_spec": {
            "window_bars": window_bars,
            "visible_end": "decision_time",
            "future_bars": 0,
            "width": IMG_WIDTH,
            "height": IMG_HEIGHT,
            "format": "PNG/RGB",
            "decision_marker": "fixed cyan dashed line at final visible candle",
            "label_blind": True,
        },
        "n_items": len(public_items),
        "items": public_items,
        "truth_fields_exposed": False,
        "historical_original_default_hidden": True,
        "migration_w10_geometry_used": False,
        "holdout_read": False,
    }
    write_json(public / "manifest.json", manifest)
    write_jsonl(admin / "truth.jsonl", truth)
    write_jsonl(admin / "source_inventory.jsonl", source_inventory)
    (public / "index.html").write_text(
        _page_html(public_items, gold_sha256=str(validated["gold_sha256"])),
        encoding="utf-8",
    )
    prereg = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "gold_events_sha256": str(validated["gold_sha256"]),
        "population_n": len(public_items),
        "render_spec_id": RENDER_SPEC_ID,
        "window_bars": window_bars,
        "window_parameter_is_review_only_not_w10": True,
        "decision_anchor": "exact decision_time in current OHLC source",
        "stored_decision_bar_used": False,
        "stored_source_path_suffix_used": False,
        "future_bars": 0,
        "historical_original_default_hidden": True,
        "decisions": sorted(VALID_DECISIONS),
        "completion_required_before_dataset_rebuild": True,
        "current_dataset_is_not_modified": True,
        "training_eligible_changed": False,
        "holdout_read": False,
    }
    write_json(pack_root / "prereg.json", prereg)
    results_dir = pack_root / "review_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "README.md").write_text(
        "# Canonical OHLC triage results\n\n"
        "请在统一因果 W200 主图上完成全部 2,649 项后导出 JSON。"
        "历史原文件仅用于来源核对；不得覆盖旧数据集或直接改变 training_eligible。\n",
        encoding="utf-8",
    )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_items": len(public_items),
        "n_current_ohlc_sources": len(source_inventory),
        "n_canonical_images": len(public_items),
        "n_historical_original_images": len(public_items),
        "source_counts": dict(sorted(source_counts.items())),
        "label_counts_private": dict(sorted(label_counts.items())),
        "render_spec_id": RENDER_SPEC_ID,
        "window_bars": window_bars,
        "future_bars": 0,
        "canonical_width": IMG_WIDTH,
        "canonical_height": IMG_HEIGHT,
        "gold_events_sha256": str(validated["gold_sha256"]),
        "public_manifest_sha256": sha256_file(public / "manifest.json"),
        "truth_sha256": sha256_file(admin / "truth.jsonl"),
        "source_inventory_sha256": sha256_file(admin / "source_inventory.jsonl"),
        "page_sha256": sha256_file(public / "index.html"),
        "canonical_image_bytes": image_bytes,
        "historical_original_bytes": historical_bytes,
        "decision_index_delta_min": min(index_deltas),
        "decision_index_delta_max": max(index_deltas),
        "decision_index_delta_nonzero": sum(delta != 0 for delta in index_deltas),
        "max_materialized_time": max_materialized_time.isoformat()
        if max_materialized_time is not None
        else None,
        "migration_w10_geometry_used": False,
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
        "dataset_root": str(Path(dataset_root).resolve()),
        "project_root": str(project_root),
        "archive_root": str(Path(archive_root).resolve()),
        "data_root": str(Path(data_root).resolve()),
        "generator_commit": git_head(project_root),
    }
    write_json(pack_root / "build_summary.json", summary)
    return summary


def summarize_export(pack_root: Path, answers_path: Path) -> dict[str, Any]:
    """Validate an export and join decisions to private lineage without mutation."""

    pack_root = pack_root.resolve()
    manifest = json.loads((pack_root / "public" / "manifest.json").read_text(encoding="utf-8"))
    payload = json.loads(answers_path.read_text(encoding="utf-8"))
    if payload.get("pack_id") != PACK_ID or manifest.get("pack_id") != PACK_ID:
        raise AuditBuildError("answer export or manifest belongs to another review pack")
    truth = read_jsonl(pack_root / "admin" / "truth.jsonl")
    truth_by_id = {str(row["review_id"]): row for row in truth}
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise AuditBuildError("answer export has no answers list")
    joined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for answer in answers:
        review_id = str(answer.get("review_id") or "")
        decision = str(answer.get("decision") or "")
        if review_id in seen or review_id not in truth_by_id:
            raise AuditBuildError(f"unknown or duplicate review_id: {review_id}")
        if decision not in VALID_DECISIONS:
            raise AuditBuildError(f"{review_id}: invalid decision {decision}")
        seen.add(review_id)
        joined.append(
            {
                **truth_by_id[review_id],
                "owner_triage_decision": decision,
                "note": answer.get("note"),
            }
        )
    counts = Counter(row["owner_triage_decision"] for row in joined)
    return {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "n_total": len(truth),
        "n_answered": len(joined),
        "complete": len(joined) == len(truth),
        "counts": dict(sorted(counts.items())),
        "joined_rows": joined,
        "training_eligible_changed": False,
    }
