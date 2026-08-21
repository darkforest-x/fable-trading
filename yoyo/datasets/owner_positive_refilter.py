"""Build a one-image review pack for the owner's actual 1,345 training positives.

Sources are ``datasets/owner_short_gold_center_v1/positive_manifest.jsonl`` and
``analysis/output/owner_side_review/review_sheet.csv``.  Every review image is
the 900x521 long-chart preview that the owner originally saw, with the selected
box highlighted in green.  These review-only images may show bars after the
box; they are physically separated from model inputs and are never copied into
training directories.  The builder reads no holdout and never changes dataset
eligibility, labels, splits, thresholds, weights, or production pointers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POSITIVE_MANIFEST = (
    PROJECT_ROOT / "datasets" / "owner_short_gold_center_v1" / "positive_manifest.jsonl"
)
DEFAULT_REVIEW_SHEET = PROJECT_ROOT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
DEFAULT_REVIEW_ROOT = PROJECT_ROOT / "analysis" / "output" / "owner_side_review"
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "datasets"
    / "owner_short_gold_center_v1"
    / "review"
    / "owner_positive_refilter_v1"
)
PACK_ID = "owner_short_gold_center_v1_positive_refilter_v1"
SEED = 20260821
EXPECTED_POSITIVES = 1345
EXPECTED_REVIEW_ROWS = 2525
EXPECTED_OWNER_SHORT = 1361
EXPECTED_DUPLICATE_ALIASES = 15
EXPECTED_CANVAS = (900, 521)
ALLOWED_DECISIONS = {"KEEP", "REMOVE", "UNCERTAIN"}


class RefilterBuildError(RuntimeError):
    """Raised when the frozen positive lineage or a review result is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def stable_review_id(sample_id: str) -> str:
    token = hashlib.sha256(f"{PACK_ID}\0{sample_id}".encode()).hexdigest()[:20]
    return f"op_{token}"


def _read_review_sheet(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_REVIEW_ROWS:
        raise RefilterBuildError(
            f"review sheet row count changed: {len(rows)} != {EXPECTED_REVIEW_ROWS}"
        )
    by_id: dict[str, dict[str, str]] = {}
    for row in rows:
        box_id = str(row.get("box_id") or "")
        if not box_id or box_id in by_id:
            raise RefilterBuildError(f"blank or duplicate box_id in review sheet: {box_id!r}")
        by_id[box_id] = row
    side_counts = Counter(str(row.get("owner_side") or "") for row in rows)
    if side_counts.get("short") != EXPECTED_OWNER_SHORT:
        raise RefilterBuildError(
            f"owner short population changed: {side_counts.get('short')} != {EXPECTED_OWNER_SHORT}"
        )
    return by_id


PAGE_TEMPLATE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>1,345 张旧训练正例原图精筛</title>
<style>
:root{color-scheme:dark;--bg:#0e120f;--panel:#171d18;--ink:#eef3ee;--muted:#a8b1a9;--line:#39433b;--keep:#11885f;--remove:#b64138;--maybe:#9a741c;--accent:#55d69a}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header{padding:10px 16px 8px;border-bottom:1px solid var(--line)}h1{font-size:19px;margin:0 0 5px}.contract{color:#f0d98f;font-size:13px}.hint{color:var(--muted);font-size:12px;margin-top:4px}.topstats{position:absolute;right:16px;top:13px;color:#c8d1c9;font-size:13px}.progress{height:5px;background:#283029;margin-top:7px;border-radius:5px;overflow:hidden}.bar{height:100%;width:0;background:var(--accent)}main{padding:10px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px;max-width:1500px;margin:auto}.controls,.nav{display:flex;align-items:center;justify-content:space-between;gap:9px;flex-wrap:wrap}.position{font-weight:700}.viewer{height:calc(100vh - 278px);min-height:390px;background:#fff;border-radius:8px;overflow:auto;display:flex;align-items:center;justify-content:center;margin-top:8px}.viewer img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain}.viewer.zoom img{max-width:none;max-height:none}.meaning{margin-top:7px;color:#dce5dd;font-size:13px}.meaning b{color:#7ce1aa}.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}.actions button{font-size:17px;font-weight:750;padding:12px}.keep{background:var(--keep)}.remove{background:var(--remove)}.maybe{background:var(--maybe)}.actions button.active{outline:3px solid #fff;outline-offset:-4px}.nav{margin-top:8px}.nav>div{display:flex;gap:7px;flex-wrap:wrap}button,select,input{font:inherit;color:var(--ink);background:#252b27;border:1px solid var(--line);border-radius:7px;padding:7px 10px}button{cursor:pointer}button:hover{border-color:var(--accent)}.primary{background:#d8f2e4;color:#102319;border-color:#d8f2e4}.note{width:min(500px,100%)}kbd{border:1px solid #626b63;border-bottom-width:2px;border-radius:4px;padding:1px 5px;background:#252b27}.error{color:#ff9e94;font-size:13px}.hidden{display:none!important}@media(max-width:760px){.topstats{position:static;margin-top:4px}.viewer{height:55vh}.actions button{font-size:15px}.contract{padding-right:0}}
</style></head><body><header><h1>1,345 张旧训练正例原图精筛</h1><div class="topstats"><span id="done"></span>　<span id="counts"></span></div>
<div class="contract">每次只看一张：这是你以前亲自判为 short、并实际进入旧训练集的原始长图预览。<b>只判断绿色框里的形态</b>；绿色框之后走势只供人工复核，不会进入模型输入。</div>
<div class="hint"><kbd>K</kbd>/<kbd>1</kbd> 保留　<kbd>X</kbd>/<kbd>2</kbd> 去掉　<kbd>?</kbd>/<kbd>3</kbd> 待定　<kbd>J</kbd>/<kbd>←</kbd> 上一张　<kbd>L</kbd>/<kbd>→</kbd>/<kbd>空格</kbd> 下一张　<kbd>U</kbd> 撤销　<kbd>Z</kbd> 原尺寸</div><div class="progress"><div class="bar" id="bar"></div></div></header>
<main><section class="panel"><div class="controls"><div class="position"><span id="position"></span> <span id="decision"></span></div><div><select id="filter"><option value="ALL">全部</option><option value="UNREVIEWED" selected>只看未审核</option><option value="KEEP">只看保留</option><option value="REMOVE">只看去掉</option><option value="UNCERTAIN">只看待定</option></select> <label>跳到 <input id="jump" type="number" min="1" step="1"></label> <label><input id="autoNext" type="checkbox" checked> 选择后自动下一张</label></div></div>
<div class="viewer" id="viewer"><img id="chart" alt="Owner 原始手标正例图"></div><div class="meaning"><b>保留</b>＝进入新正例候选；<b>去掉</b>＝从新版本正例排除；<b>待定</b>＝单独二次仲裁。旧训练集不会被覆盖。</div><div class="error" id="imageError"></div>
<div class="actions"><button class="keep" data-decision="KEEP">K / 1 · 保留</button><button class="remove" data-decision="REMOVE">X / 2 · 去掉</button><button class="maybe" data-decision="UNCERTAIN">? / 3 · 待定</button></div>
<div class="nav"><div><button id="prev">J / ← 上一张</button><button id="next">L / → 下一张</button><button id="undo">U · 撤销</button><button id="zoom">Z · 原尺寸</button></div><div><input id="note" class="note" placeholder="备注（可空；Esc 退出输入）"><button id="import">导入进度</button><input id="importFile" class="hidden" type="file" accept="application/json"><button id="export" class="primary">导出 JSON</button></div></div></section></main>
<script>
const items=__ITEMS__,packId=__PACK_ID__,key=__STORAGE_KEY__,exportName="owner_short_gold_center_v1_positive_refilter_v1_answers.json",allowed=new Set(['KEEP','REMOVE','UNCERTAIN']);
let index=Number(localStorage.getItem(key+'::index')||0);if(index<0||index>=items.length)index=0;let answers={};try{answers=JSON.parse(localStorage.getItem(key+'::answers')||'{}')}catch(_){answers={}}let undoStack=[];
const $=id=>document.getElementById(id),chart=$('chart'),viewer=$('viewer'),note=$('note'),filter=$('filter'),jump=$('jump');
function save(){localStorage.setItem(key+'::answers',JSON.stringify(answers));localStorage.setItem(key+'::index',String(index))}function answered(id){return answers[id]&&allowed.has(answers[id].decision)}
function matches(i){const mode=filter.value,a=answers[items[i].review_id];if(mode==='ALL')return true;if(mode==='UNREVIEWED')return !answered(items[i].review_id);return a&&a.decision===mode}function findStep(d){for(let n=1;n<=items.length;n++){const i=(index+d*n+items.length)%items.length;if(matches(i))return i}return index}
function step(d){index=findStep(d);save();render()}function decide(value){const id=items[index].review_id,old=answers[id]?{...answers[id]}:null;undoStack.push({index,old});answers[id]={review_id:id,decision:value,note:note.value||'',decided_at:new Date().toISOString()};save();if($('autoNext').checked)step(1);else render()}function undo(){const last=undoStack.pop();if(!last)return;index=last.index;const id=items[index].review_id;if(last.old)answers[id]=last.old;else delete answers[id];save();render()}
function stats(){const c={KEEP:0,REMOVE:0,UNCERTAIN:0};Object.values(answers).forEach(a=>{if(a&&allowed.has(a.decision))c[a.decision]++});const n=c.KEEP+c.REMOVE+c.UNCERTAIN;$('done').textContent=`已审 ${n} / ${items.length}`;$('counts').textContent=`保留 ${c.KEEP} · 去掉 ${c.REMOVE} · 待定 ${c.UNCERTAIN}`;$('bar').style.width=`${100*n/items.length}%`}
function prefetch(){for(let n=1;n<=4;n++){const im=new Image();im.src=items[(index+n)%items.length].image}}
function render(){const item=items[index],a=answers[item.review_id]||{};$('position').textContent=`${index+1} / ${items.length} · ${item.review_id}`;$('decision').textContent=a.decision?`· 当前：${{KEEP:'保留',REMOVE:'去掉',UNCERTAIN:'待定'}[a.decision]}`:'· 未审核';chart.src=item.image;note.value=a.note||'';jump.value=String(index+1);document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===a.decision));$('imageError').textContent='';stats();prefetch()}
chart.onerror=()=>{$('imageError').textContent='原始手标图加载失败，请停止审核并报告此编号。'};document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>decide(b.dataset.decision));$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('undo').onclick=undo;$('zoom').onclick=()=>viewer.classList.toggle('zoom');filter.onchange=()=>{if(!matches(index))index=findStep(1);filter.blur();save();render()};jump.onchange=()=>{const n=Number(jump.value);if(Number.isInteger(n)&&n>=1&&n<=items.length){index=n-1;save();render()}jump.blur()};note.oninput=()=>{const id=items[index].review_id;if(answers[id]){answers[id].note=note.value||'';save()}};
$('export').onclick=()=>{const rows=items.map(x=>answers[x.review_id]).filter(a=>a&&allowed.has(a.decision)),out={schema_version:1,pack_id:packId,exported_at:new Date().toISOString(),complete:rows.length===items.length,n_total:items.length,n_answered:rows.length,answers:rows},blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=exportName;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)};$('import').onclick=()=>$('importFile').click();$('importFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{const out=JSON.parse(await file.text());if(out.pack_id!==packId||!Array.isArray(out.answers))throw new Error('不是本审核包导出的 JSON');const known=new Set(items.map(x=>x.review_id));for(const a of out.answers){if(known.has(a.review_id)&&allowed.has(a.decision))answers[a.review_id]=a}save();render();alert(`已导入 ${out.answers.length} 条`)}catch(err){alert(`导入失败：${err.message}`)}e.target.value=''};
document.addEventListener('keydown',e=>{if(e.target===note){if(e.key==='Escape')note.blur();return}if(e.target===jump&&/^[0-9]$/.test(e.key))return;const k=e.key.toLowerCase();if(k==='k'||e.key==='1'){e.preventDefault();decide('KEEP')}else if(k==='x'||e.key==='2'){e.preventDefault();decide('REMOVE')}else if(e.key==='?'||e.key==='3'){e.preventDefault();decide('UNCERTAIN')}else if(k==='j'||e.key==='ArrowLeft'){e.preventDefault();step(-1)}else if(k==='l'||e.key==='ArrowRight'||e.key===' '){e.preventDefault();step(1)}else if(k==='u')undo();else if(k==='z')$('zoom').click()});render();
</script></body></html>"""


def _render_page(items: list[dict[str, str]], storage_key: str) -> str:
    return (
        PAGE_TEMPLATE.replace("__ITEMS__", json.dumps(items, ensure_ascii=False, separators=(",", ":")))
        .replace("__PACK_ID__", json.dumps(PACK_ID))
        .replace("__STORAGE_KEY__", json.dumps(storage_key))
    )


def build_pack(
    *,
    positive_manifest: Path = DEFAULT_POSITIVE_MANIFEST,
    review_sheet: Path = DEFAULT_REVIEW_SHEET,
    review_root: Path = DEFAULT_REVIEW_ROOT,
    output_dir: Path = DEFAULT_OUTPUT,
    generator_commit: str,
    seed: int = SEED,
) -> dict[str, Any]:
    """Build the review-only pack without changing any training artifact."""

    positives = read_jsonl(positive_manifest)
    if len(positives) != EXPECTED_POSITIVES:
        raise RefilterBuildError(f"positive count changed: {len(positives)} != {EXPECTED_POSITIVES}")
    review_by_id = _read_review_sheet(review_sheet)
    sample_ids = [str(row.get("sample_id") or "") for row in positives]
    if len(set(sample_ids)) != len(sample_ids) or any(not value for value in sample_ids):
        raise RefilterBuildError("positive sample_id is blank or duplicated")

    public_dir = output_dir / "public"
    image_dir = public_dir / "images"
    admin_dir = output_dir / "admin"
    image_dir.mkdir(parents=True, exist_ok=True)
    admin_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_results").mkdir(parents=True, exist_ok=True)

    ordered = list(positives)
    random.Random(seed).shuffle(ordered)
    public_items: list[dict[str, str]] = []
    truth: list[dict[str, Any]] = []
    preview_hashes: list[str] = []
    n_aliases = 0

    for row in ordered:
        sample_id = str(row["sample_id"])
        if row.get("class") != "positive" or row.get("source_owner_gold_confirmed") is not True:
            raise RefilterBuildError(f"{sample_id}: row is not an owner-confirmed positive")
        annotation_ids = [str(value) for value in row.get("owner_annotation_ids") or []]
        if not annotation_ids:
            raise RefilterBuildError(f"{sample_id}: owner_annotation_ids is empty")
        n_aliases += len(annotation_ids) - 1
        review_rows: list[dict[str, str]] = []
        for annotation_id in annotation_ids:
            review_row = review_by_id.get(annotation_id)
            if review_row is None:
                raise RefilterBuildError(f"{sample_id}: missing owner review row {annotation_id}")
            if review_row.get("owner_side") != "short":
                raise RefilterBuildError(
                    f"{sample_id}: {annotation_id} is owner_side={review_row.get('owner_side')!r}"
                )
            review_rows.append(review_row)

        source_preview = review_root / review_rows[0]["preview_path"]
        if not source_preview.is_file():
            raise RefilterBuildError(f"{sample_id}: missing original preview {source_preview}")
        with Image.open(source_preview) as image:
            if image.size != EXPECTED_CANVAS:
                raise RefilterBuildError(f"{sample_id}: preview size {image.size} != {EXPECTED_CANVAS}")
            if image.format != "JPEG":
                raise RefilterBuildError(f"{sample_id}: preview format {image.format} != JPEG")

        training_image = PROJECT_ROOT / str(row["image_path"])
        training_label = PROJECT_ROOT / str(row["label_path"])
        if not training_image.is_file() or sha256_file(training_image) != row.get("image_sha256"):
            raise RefilterBuildError(f"{sample_id}: frozen training image SHA mismatch")
        if not training_label.is_file() or sha256_file(training_label) != row.get("label_sha256"):
            raise RefilterBuildError(f"{sample_id}: frozen training label SHA mismatch")

        preview_sha = sha256_file(source_preview)
        preview_hashes.append(preview_sha)
        review_id = stable_review_id(sample_id)
        target = image_dir / f"{review_id}.jpg"
        shutil.copyfile(source_preview, target)
        if sha256_file(target) != preview_sha:
            raise RefilterBuildError(f"{sample_id}: copied preview SHA mismatch")

        public_items.append({"review_id": review_id, "image": f"images/{review_id}.jpg"})
        truth.append(
            {
                "review_id": review_id,
                "sample_id": sample_id,
                "symbol": row.get("symbol"),
                "split": row.get("split"),
                "owner_annotation_ids": annotation_ids,
                "owner_preview_source": str(source_preview.relative_to(PROJECT_ROOT)),
                "owner_preview_sha256": preview_sha,
                "owner_preview_size": list(EXPECTED_CANVAS),
                "training_image_path": row.get("image_path"),
                "training_image_sha256": row.get("image_sha256"),
                "training_label_path": row.get("label_path"),
                "training_label_sha256": row.get("label_sha256"),
                "source_owner_global": row.get("source_owner_global"),
                "source_owner_cut_time": row.get("source_owner_cut_time"),
                "future_visible_in_review_only_image": True,
                "review_image_used_as_model_input": False,
            }
        )

    if n_aliases != EXPECTED_DUPLICATE_ALIASES:
        raise RefilterBuildError(f"duplicate alias count changed: {n_aliases} != {EXPECTED_DUPLICATE_ALIASES}")
    if len(set(item["review_id"] for item in public_items)) != EXPECTED_POSITIVES:
        raise RefilterBuildError("stable review_id collision")

    positive_sha = sha256_file(positive_manifest)
    review_sheet_sha = sha256_file(review_sheet)
    storage_key = f"owner-positive-refilter::{positive_sha}::{review_sheet_sha}"
    page = _render_page(public_items, storage_key)
    (public_dir / "index.html").write_text(page, encoding="utf-8")
    write_json(public_dir / "manifest.json", {"pack_id": PACK_ID, "items": public_items})
    write_jsonl(admin_dir / "truth.jsonl", truth)
    prereg = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "population_contract": "actual 1,345 positives used by owner_short_gold_center_v1",
        "owner_short_review_rows_before_dedup": EXPECTED_OWNER_SHORT,
        "duplicate_aliases_merged": EXPECTED_DUPLICATE_ALIASES,
        "positive_training_rows": EXPECTED_POSITIVES,
        "decisions": sorted(ALLOWED_DECISIONS),
        "single_image_only": True,
        "green_box_is_owner_original_target": True,
        "future_visible_in_review_only_image": True,
        "review_images_physically_separate_from_training": True,
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
        "old_dataset_modified": False,
    }
    write_json(output_dir / "prereg.json", prereg)
    truth_sha = sha256_file(admin_dir / "truth.jsonl")
    public_manifest_sha = sha256_file(public_dir / "manifest.json")
    summary = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator_commit": generator_commit,
        "n_items": len(public_items),
        "n_owner_short_before_dedup": EXPECTED_OWNER_SHORT,
        "n_duplicate_aliases_merged": n_aliases,
        "split_counts_private": dict(Counter(str(row.get("split")) for row in positives)),
        "canvas": list(EXPECTED_CANVAS),
        "positive_manifest_sha256": positive_sha,
        "review_sheet_sha256": review_sheet_sha,
        "public_manifest_sha256": public_manifest_sha,
        "truth_sha256": truth_sha,
        "page_sha256": sha256_file(public_dir / "index.html"),
        "preview_set_sha256": hashlib.sha256("\n".join(sorted(preview_hashes)).encode()).hexdigest(),
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
    }
    write_json(output_dir / "build_summary.json", summary)
    (output_dir / "review_results" / "README.md").write_text(
        "# Owner review exports\n\nPlace exported JSON here. Exports never mutate the frozen dataset.\n",
        encoding="utf-8",
    )
    return summary


def verify_pack(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = json.loads((output_dir / "public" / "manifest.json").read_text(encoding="utf-8"))
    truth = read_jsonl(output_dir / "admin" / "truth.jsonl")
    items = manifest.get("items") or []
    if manifest.get("pack_id") != PACK_ID or len(items) != EXPECTED_POSITIVES:
        raise RefilterBuildError("public manifest population or pack_id mismatch")
    if len(truth) != EXPECTED_POSITIVES:
        raise RefilterBuildError("private truth population mismatch")
    truth_by_id = {str(row["review_id"]): row for row in truth}
    if len(truth_by_id) != EXPECTED_POSITIVES:
        raise RefilterBuildError("private truth review_id collision")
    for item in items:
        if set(item) != {"review_id", "image"}:
            raise RefilterBuildError(f"public item leaks private fields: {set(item)}")
        review_id = str(item["review_id"])
        row = truth_by_id.get(review_id)
        if row is None:
            raise RefilterBuildError(f"missing truth for {review_id}")
        image_path = output_dir / "public" / str(item["image"])
        if not image_path.is_file() or sha256_file(image_path) != row["owner_preview_sha256"]:
            raise RefilterBuildError(f"{review_id}: review image SHA mismatch")
        with Image.open(image_path) as image:
            if image.size != EXPECTED_CANVAS or image.format != "JPEG":
                raise RefilterBuildError(f"{review_id}: review image contract mismatch")
    page = (output_dir / "public" / "index.html").read_text(encoding="utf-8")
    forbidden = ["historical_image", "toggleReference", "R ·", "左右"]
    if any(token in page for token in forbidden):
        raise RefilterBuildError("page still contains the rejected two-image/reference UI")
    if page.count('<img id="chart"') != 1:
        raise RefilterBuildError("page must expose exactly one review image element")
    return {"ok": True, "n_items": len(items), "pack_id": PACK_ID}


def summarize_answers(answer_path: Path, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    payload = json.loads(answer_path.read_text(encoding="utf-8"))
    if payload.get("pack_id") != PACK_ID or not isinstance(payload.get("answers"), list):
        raise RefilterBuildError("answer export does not belong to this pack")
    truth_by_id = {
        str(row["review_id"]): row for row in read_jsonl(output_dir / "admin" / "truth.jsonl")
    }
    seen: set[str] = set()
    joined: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for answer in payload["answers"]:
        review_id = str(answer.get("review_id") or "")
        decision = str(answer.get("decision") or "")
        if review_id in seen or review_id not in truth_by_id:
            raise RefilterBuildError(f"unknown or duplicate review_id: {review_id}")
        if decision not in ALLOWED_DECISIONS:
            raise RefilterBuildError(f"{review_id}: invalid decision {decision}")
        seen.add(review_id)
        counts[decision] += 1
        joined.append(
            {
                **truth_by_id[review_id],
                "owner_refilter_decision": decision,
                "note": answer.get("note"),
                "decided_at": answer.get("decided_at"),
            }
        )
    return {
        "pack_id": PACK_ID,
        "n_total": EXPECTED_POSITIVES,
        "n_answered": len(joined),
        "complete": len(joined) == EXPECTED_POSITIVES,
        "counts": {key: counts.get(key, 0) for key in sorted(ALLOWED_DECISIONS)},
        "joined": joined,
        "training_eligible_changed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--positive-manifest", type=Path, default=DEFAULT_POSITIVE_MANIFEST)
    build.add_argument("--review-sheet", type=Path, default=DEFAULT_REVIEW_SHEET)
    build.add_argument("--review-root", type=Path, default=DEFAULT_REVIEW_ROOT)
    build.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument("--generator-commit", required=True)
    build.add_argument("--seed", type=int, default=SEED)
    verify = sub.add_parser("verify")
    verify.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    summarize = sub.add_parser("summarize")
    summarize.add_argument("answers", type=Path)
    summarize.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    summarize.add_argument("--joined-out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        result = build_pack(
            positive_manifest=args.positive_manifest,
            review_sheet=args.review_sheet,
            review_root=args.review_root,
            output_dir=args.output_dir,
            generator_commit=args.generator_commit,
            seed=args.seed,
        )
    elif args.command == "verify":
        result = verify_pack(args.output_dir)
    else:
        result = summarize_answers(args.answers, args.output_dir)
        if args.joined_out:
            write_jsonl(args.joined_out, result.pop("joined"))
        else:
            result = {key: value for key, value in result.items() if key != "joined"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
