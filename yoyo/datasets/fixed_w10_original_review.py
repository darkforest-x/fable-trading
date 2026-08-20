"""Build a keep/remove review pack from the original visual evidence.

The frozen fixed-W10 snapshot is only the join key.  Review images come from
the source artifact that existed before the W10/Core4/Confirm1 migration:
Owner long-chart previews, easy-negative source renders, reviewed V3.2
images, the 8768 context/local pair, or the exact Owner semantic-review pair.
No OHLC is read and no training, holdout evaluation, label mutation, or
eligibility change is performed here.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from yoyo.datasets.fixed_w10_blind_audit import (
    AuditBuildError,
    read_jsonl,
    sha256_file,
    validate_dataset,
    write_json,
    write_jsonl,
)


SCHEMA_VERSION = 1
PACK_ID = "fixed_w10_core4_confirm1_v1_original_source_triage_v1"
DEFAULT_SEED = 20260821
VALID_DECISIONS = frozenset({"KEEP", "REMOVE", "UNCERTAIN"})


def _index(rows: Iterable[dict[str, Any]], key: str, *, source: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value:
            raise AuditBuildError(f"{source}: row missing {key}")
        if value in out:
            raise AuditBuildError(f"{source}: duplicate {key}={value}")
        out[value] = row
    return out


def _csv_index(path: Path, key: str) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return _index(csv.DictReader(handle), key, source=path)


def _path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _stable_id(seed: int, gold_id: str) -> str:
    payload = f"{seed}|original-source|{gold_id}".encode("utf-8")
    return f"os_{hashlib.sha256(payload).hexdigest()[:20]}"


def _stable_rank(seed: int, gold_id: str) -> str:
    return hashlib.sha256(f"{seed}|order|{gold_id}".encode("utf-8")).hexdigest()


def _verify_image(path: Path, expected_sha256: str | None, *, role: str) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise AuditBuildError(f"missing {role}: {path}")
    actual = sha256_file(path)
    if expected_sha256 and actual != expected_sha256:
        raise AuditBuildError(
            f"{role} sha256 drift: {path} actual={actual} expected={expected_sha256}"
        )
    return {
        "path": str(path),
        "sha256": actual,
        "size_bytes": path.stat().st_size,
    }


class OriginalSourceCatalog:
    """Resolve final Gold rows back to the image the Owner originally saw."""

    def __init__(self, project_root: Path, legacy_yoyo_root: Path):
        self.project_root = project_root.resolve()
        self.legacy_yoyo_root = legacy_yoyo_root.resolve()

        owner_root = self.project_root / "datasets" / "owner_short_gold_center_v1"
        self.owner_positive = _index(
            read_jsonl(owner_root / "positive_manifest.jsonl"),
            "sample_id",
            source=owner_root / "positive_manifest.jsonl",
        )
        self.owner_negative = _index(
            read_jsonl(owner_root / "negative_manifest.jsonl"),
            "sample_id",
            source=owner_root / "negative_manifest.jsonl",
        )
        self.owner_sheet_root = self.project_root / "analysis" / "output" / "owner_side_review"
        self.owner_sheet = _csv_index(self.owner_sheet_root / "review_sheet.csv", "box_id")

        v32_path = (
            self.legacy_yoyo_root
            / "datasets"
            / "dataset_v3_2_reviewed_core_v1"
            / "manifest.jsonl"
        )
        self.v32 = _index(read_jsonl(v32_path), "sample_id", source=v32_path)

        gold_path = self.legacy_yoyo_root / "datasets" / "gold_v1.jsonl"
        self.gold_8768 = _index(read_jsonl(gold_path), "gold_id", source=gold_path)

        semantic_path = (
            self.project_root
            / "analysis"
            / "output"
            / "local_signal_v2_positive_semantic_review200_v2"
            / "owner_review_joined.jsonl"
        )
        self.semantic = _index(read_jsonl(semantic_path), "review_id", source=semantic_path)

        hardneg_path = (
            self.project_root
            / "analysis"
            / "output"
            / "owner_short_train_hardneg_newblocks200_v3"
            / "owner_review_labeled_manifest.jsonl"
        )
        self.hardneg = _index(read_jsonl(hardneg_path), "review_id", source=hardneg_path)

    @staticmethod
    def _get(index: dict[str, dict[str, Any]], source_id: str, source: str) -> dict[str, Any]:
        try:
            return index[source_id]
        except KeyError as exc:
            raise AuditBuildError(f"{source}: source_record_id not found: {source_id}") from exc

    def resolve(self, event: dict[str, Any]) -> dict[str, Any]:
        """Return verified original primary/reference evidence for one Gold row."""

        source_dataset = str(event.get("source_dataset") or "")
        source_id = str(event.get("source_record_id") or "")
        if not source_dataset or not source_id:
            raise AuditBuildError(f"{event.get('gold_id')}: missing source lineage")

        primary: dict[str, Any]
        reference: dict[str, Any] | None = None
        source_kind: str
        source_record: dict[str, Any]

        if source_dataset.endswith("/owner_short_gold_center_v1/positive_manifest.jsonl"):
            source_kind = "owner_original_long_chart"
            source_record = self._get(self.owner_positive, source_id, source_kind)
            sheet = self._get(self.owner_sheet, source_id, "owner review sheet")
            primary = _verify_image(
                _path(self.owner_sheet_root, sheet["preview_path"]),
                None,
                role="Owner original long-chart preview",
            )
        elif source_dataset.endswith("/owner_short_gold_center_v1/negative_manifest.jsonl"):
            source_kind = "easy_negative_source_render"
            source_record = self._get(self.owner_negative, source_id, source_kind)
            primary = _verify_image(
                _path(self.project_root, source_record["image_path"]),
                source_record.get("image_sha256"),
                role="easy-negative source image",
            )
        elif "dataset_v3_2_reviewed_core_v1" in source_dataset:
            source_kind = "reviewed_v3_2_source_render"
            source_record = self._get(self.v32, source_id, source_kind)
            primary = _verify_image(
                _path(self.legacy_yoyo_root, source_record["image_path"]),
                source_record.get("image_sha256"),
                role="reviewed V3.2 source image",
            )
        elif source_dataset.endswith("/gold_v1.jsonl"):
            source_kind = "owner_8768_context_and_local"
            source_record = self._get(self.gold_8768, source_id, source_kind)
            render_root = self.legacy_yoyo_root / "datasets" / "gold_labelstudio_v1" / "images"
            primary = _verify_image(
                render_root / "context" / f"{source_id}.png",
                source_record.get("context_image_sha256"),
                role="8768 context image",
            )
            reference = _verify_image(
                render_root / "local" / f"{source_id}.png",
                source_record.get("local_image_sha256"),
                role="8768 local image",
            )
        elif "local_signal_v2_positive_semantic_review200_v2" in source_dataset:
            source_kind = "owner_semantic_review_pair"
            source_record = self._get(self.semantic, source_id, source_kind)
            primary = _verify_image(
                _path(self.project_root, source_record["image_path"]),
                source_record.get("image_sha256"),
                role="Owner semantic causal-review image",
            )
            reference = _verify_image(
                _path(self.project_root, source_record["future_review_path"]),
                source_record.get("future_review_sha256"),
                role="Owner semantic future-review image",
            )
        elif "owner_short_train_hardneg_newblocks200_v3" in source_dataset:
            source_kind = "owner_hardneg_review_pair"
            source_record = self._get(self.hardneg, source_id, source_kind)
            primary = _verify_image(
                _path(self.project_root, source_record["causal_review_path"]),
                source_record.get("causal_review_sha256"),
                role="Owner hard-negative causal-review image",
            )
            reference = _verify_image(
                _path(self.project_root, source_record["future_review_path"]),
                source_record.get("future_review_sha256"),
                role="Owner hard-negative future-review image",
            )
        else:
            raise AuditBuildError(
                f"{event.get('gold_id')}: unsupported source_dataset {source_dataset}"
            )

        return {
            "gold_id": str(event["gold_id"]),
            "source_kind": source_kind,
            "source_dataset": source_dataset,
            "source_record_id": source_id,
            "primary": primary,
            "reference": reference,
            "shape_label": event.get("shape_label"),
            "migration_status": event.get("migration_status"),
            "source_annotation_type": event.get("source_annotation_type"),
            "split": event.get("split"),
            "decision_time": event.get("decision_time"),
            "holdout_read": False,
        }


def _copy_image(source: Path, target: Path, expected_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and sha256_file(target) == expected_sha256:
        return
    shutil.copy2(source, target)
    if sha256_file(target) != expected_sha256:
        raise AuditBuildError(f"review copy hash mismatch: {target}")


def _page_html(items: list[dict[str, Any]], *, gold_sha256: str) -> str:
    """Return a self-contained file:// compatible high-speed triage page."""

    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    storage_key = json.dumps(f"original-source-triage::{PACK_ID}::{gold_sha256}")
    export_name = json.dumps(f"{PACK_ID}_answers.json")
    template = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>2,649 张原始来源图全量筛选</title>
<style>
:root{--bg:#111412;--panel:#1a1e1b;--ink:#f5f5ef;--muted:#a9b0aa;--line:#394039;--keep:#157f55;--remove:#a33b32;--maybe:#8b6b1f;--accent:#8ed1b0}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow-x:hidden}
header{position:sticky;top:0;z-index:5;background:rgba(17,20,18,.97);border-bottom:1px solid var(--line);padding:9px 14px}
.topline,.stats,.controls,.actions,.nav{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.topline{justify-content:space-between}.stats{font-size:13px;color:var(--muted)}
h1{font-size:17px;margin:0}.hint{font-size:12px;color:var(--muted);margin-top:5px}.progress{height:5px;background:#303630;border-radius:8px;overflow:hidden;margin-top:7px}.bar{height:100%;background:var(--accent);width:0}
main{width:min(1500px,100%);margin:auto;padding:10px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.viewer{height:calc(100vh - 245px);min-height:360px;background:#fff;border-radius:7px;overflow:auto;display:flex;align-items:center;justify-content:center;gap:8px}
.viewer.split img{max-width:49%}.viewer img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain}.viewer.zoom img{max-width:none;max-height:none}
.reference{display:none}.reference.show{display:block}.position{font-size:14px}.controls{justify-content:space-between;margin-bottom:8px}.controls input[type=number]{width:88px}
button,select,input{font:inherit}button,select,input[type=number]{color:var(--ink);background:#242a25;border:1px solid var(--line);border-radius:7px;padding:7px 10px}button{cursor:pointer}button:hover{border-color:var(--accent)}
.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:9px}.actions button{font-size:17px;font-weight:700;padding:12px}.keep{background:var(--keep)}.remove{background:var(--remove)}.maybe{background:var(--maybe)}.actions button.active{outline:3px solid #fff;outline-offset:-4px}
.nav{justify-content:space-between;margin-top:8px}.nav>div{display:flex;gap:7px;flex-wrap:wrap}.primary{background:#d8f2e4;color:#102319;border-color:#d8f2e4}.note{width:min(520px,100%);padding:7px 10px;color:var(--ink);background:#242a25;border:1px solid var(--line);border-radius:7px}
kbd{border:1px solid #626b63;border-bottom-width:2px;border-radius:4px;padding:1px 5px;color:#e7ece7;background:#242a25}.hidden{display:none!important}.error{color:#ff9e94;font-size:13px}
@media(max-width:760px){header{padding:8px}main{padding:6px}.viewer{height:calc(100vh - 300px);min-height:300px}.actions{grid-template-columns:1fr}.viewer.split{display:block;height:auto}.viewer.split img{max-width:100%;margin:auto}}
</style>
</head>
<body>
<header>
  <div class="topline"><h1>2,649 张原始来源图全量筛选</h1><div class="stats"><span id="done"></span><span id="counts"></span></div></div>
  <div class="hint"><kbd>K</kbd>/<kbd>1</kbd> 保留　<kbd>X</kbd>/<kbd>2</kbd> 去掉　<kbd>?</kbd>/<kbd>3</kbd> 待定　<kbd>J</kbd>/<kbd>←</kbd> 上一张　<kbd>L</kbd>/<kbd>→</kbd>/<kbd>空格</kbd> 下一张　<kbd>U</kbd> 撤销　<kbd>R</kbd> 参考图　<kbd>Z</kbd> 缩放</div>
  <div class="progress"><div class="bar" id="bar"></div></div>
</header>
<main><section class="panel">
  <div class="controls">
    <div class="position"><span id="position"></span> <span id="decision"></span></div>
    <div>
      <select id="filter"><option value="ALL">全部</option><option value="UNREVIEWED" selected>只看未审核</option><option value="KEEP">只看保留</option><option value="REMOVE">只看去掉</option><option value="UNCERTAIN">只看待定</option></select>
      <label>跳到 <input id="jump" type="number" min="1" step="1"></label>
      <label><input id="autoNext" type="checkbox" checked> 选择后自动下一张</label>
    </div>
  </div>
  <div class="viewer" id="viewer"><img id="primary" alt="原始来源图"><img id="reference" class="reference" alt="当时附加参考图"></div>
  <div class="error" id="imageError"></div>
  <div class="actions">
    <button class="keep" data-decision="KEEP">K / 1 · 保留</button>
    <button class="remove" data-decision="REMOVE">X / 2 · 去掉</button>
    <button class="maybe" data-decision="UNCERTAIN">? / 3 · 待定</button>
  </div>
  <div class="nav">
    <div><button id="prev">J / ← 上一张</button><button id="next">L / → 下一张</button><button id="undo">U · 撤销上次</button><button id="toggleReference">R · 显示参考图</button><button id="zoom">Z · 原尺寸</button></div>
    <div><input id="note" class="note" placeholder="备注（可空）"><button id="import">导入进度</button><input id="importFile" class="hidden" type="file" accept="application/json"><button id="export" class="primary">导出 JSON</button></div>
  </div>
</section></main>
<script>
const items=__ITEMS__; const packId=__PACK_ID__; const key=__STORAGE_KEY__; const exportName=__EXPORT_NAME__;
const allowed=new Set(['KEEP','REMOVE','UNCERTAIN']);
let index=Number(localStorage.getItem(key+'::index')||0); if(index<0||index>=items.length)index=0;
let answers={}; try{answers=JSON.parse(localStorage.getItem(key+'::answers')||'{}')}catch(_){answers={}}
let undoStack=[]; let referenceShown=false;
const $=id=>document.getElementById(id), primary=$('primary'),reference=$('reference'),viewer=$('viewer'),note=$('note'),filter=$('filter');
function save(){localStorage.setItem(key+'::answers',JSON.stringify(answers));localStorage.setItem(key+'::index',String(index))}
function answered(id){return answers[id]&&allowed.has(answers[id].decision)}
function matches(i){const mode=filter.value,a=answers[items[i].review_id];if(mode==='ALL')return true;if(mode==='UNREVIEWED')return !answered(items[i].review_id);return a&&a.decision===mode}
function findStep(direction){for(let n=1;n<=items.length;n++){const i=(index+direction*n+items.length)%items.length;if(matches(i))return i}return index}
function step(direction){index=findStep(direction);referenceShown=false;save();render()}
function decide(value){const id=items[index].review_id;const old=answers[id]?{...answers[id]}:null;undoStack.push({index,old});answers[id]={review_id:id,decision:value,note:note.value||'',decided_at:new Date().toISOString()};save();if($('autoNext').checked)step(1);else render()}
function undo(){const last=undoStack.pop();if(!last)return;index=last.index;const id=items[index].review_id;if(last.old)answers[id]=last.old;else delete answers[id];save();render()}
function stats(){const counts={KEEP:0,REMOVE:0,UNCERTAIN:0};Object.values(answers).forEach(a=>{if(a&&allowed.has(a.decision))counts[a.decision]++});const n=counts.KEEP+counts.REMOVE+counts.UNCERTAIN;$('done').textContent=`已审 ${n} / ${items.length}`;$('counts').textContent=`保留 ${counts.KEEP} · 去掉 ${counts.REMOVE} · 待定 ${counts.UNCERTAIN}`;$('bar').style.width=`${100*n/items.length}%`;return n}
function render(){const item=items[index],a=answers[item.review_id]||{};$('position').textContent=`${index+1} / ${items.length} · ${item.review_id}`;$('decision').textContent=a.decision?`· 当前：${{KEEP:'保留',REMOVE:'去掉',UNCERTAIN:'待定'}[a.decision]}`:'· 未审核';primary.src=item.image;reference.src=item.reference_image||'';reference.classList.toggle('show',Boolean(item.reference_image&&referenceShown));viewer.classList.toggle('split',Boolean(item.reference_image&&referenceShown));$('toggleReference').classList.toggle('hidden',!item.reference_image);$('toggleReference').textContent=referenceShown?'R · 隐藏参考图':'R · 显示参考图';note.value=a.note||'';$('jump').value=String(index+1);document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===a.decision));$('imageError').textContent='';stats()}
primary.onerror=()=>{$('imageError').textContent='原图加载失败，请停止审核并报告此编号。'};reference.onerror=()=>{$('imageError').textContent='附加参考图加载失败，请停止审核并报告此编号。'};
document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>decide(b.dataset.decision));
$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('undo').onclick=undo;
$('toggleReference').onclick=()=>{referenceShown=!referenceShown;render()};$('zoom').onclick=()=>viewer.classList.toggle('zoom');
filter.onchange=()=>{if(!matches(index))index=findStep(1);save();render()};$('jump').onchange=()=>{const n=Number($('jump').value);if(Number.isInteger(n)&&n>=1&&n<=items.length){index=n-1;referenceShown=false;save();render()}};
note.onchange=()=>{const id=items[index].review_id;if(answers[id]){answers[id].note=note.value||'';save()}};
$('export').onclick=()=>{const rows=items.map(x=>answers[x.review_id]).filter(a=>a&&allowed.has(a.decision));const out={schema_version:1,pack_id:packId,exported_at:new Date().toISOString(),complete:rows.length===items.length,n_total:items.length,n_answered:rows.length,answers:rows};const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=exportName;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)};
$('import').onclick=()=>$('importFile').click();$('importFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{const out=JSON.parse(await file.text());if(out.pack_id!==packId||!Array.isArray(out.answers))throw new Error('不是本审核包导出的 JSON');const known=new Set(items.map(x=>x.review_id));for(const a of out.answers){if(known.has(a.review_id)&&allowed.has(a.decision))answers[a.review_id]=a}save();render();alert(`已导入 ${out.answers.length} 条`)}catch(err){alert(`导入失败：${err.message}`)}e.target.value=''};
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;const k=e.key.toLowerCase();if(k==='k'||e.key==='1')decide('KEEP');else if(k==='x'||e.key==='2')decide('REMOVE');else if(e.key==='?'||e.key==='3')decide('UNCERTAIN');else if(k==='j'||e.key==='ArrowLeft'){e.preventDefault();step(-1)}else if(k==='l'||e.key==='ArrowRight'||e.key===' '){e.preventDefault();step(1)}else if(k==='u')undo();else if(k==='r'&&!$('toggleReference').classList.contains('hidden'))$('toggleReference').click();else if(k==='z')$('zoom').click()});
render();
</script></body></html>'''
    return (
        template.replace("__ITEMS__", payload)
        .replace("__PACK_ID__", json.dumps(PACK_ID))
        .replace("__STORAGE_KEY__", storage_key)
        .replace("__EXPORT_NAME__", export_name)
    )


def build_pack_from_resolved(
    resolved: Sequence[dict[str, Any]],
    pack_root: Path,
    *,
    gold_sha256: str,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Copy verified evidence and build the public/private review package."""

    pack_root = pack_root.resolve()
    public = pack_root / "public"
    admin = pack_root / "admin"
    image_dir = public / "images"
    reference_dir = public / "reference"
    public.mkdir(parents=True, exist_ok=True)
    admin.mkdir(parents=True, exist_ok=True)

    ordered = sorted(resolved, key=lambda row: _stable_rank(seed, str(row["gold_id"])))
    if len({str(row["gold_id"]) for row in ordered}) != len(ordered):
        raise AuditBuildError("resolved original evidence has duplicate gold_id")

    public_items: list[dict[str, Any]] = []
    truth: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    total_bytes = 0
    reference_count = 0
    for row in ordered:
        gold_id = str(row["gold_id"])
        review_id = _stable_id(seed, gold_id)
        primary = row["primary"]
        primary_source = Path(primary["path"])
        primary_suffix = primary_source.suffix.lower() or ".png"
        primary_name = f"{review_id}{primary_suffix}"
        primary_target = image_dir / primary_name
        _copy_image(primary_source, primary_target, str(primary["sha256"]))
        total_bytes += primary_target.stat().st_size

        reference_rel: str | None = None
        reference = row.get("reference")
        if reference:
            reference_source = Path(reference["path"])
            reference_suffix = reference_source.suffix.lower() or ".png"
            reference_name = f"{review_id}{reference_suffix}"
            reference_target = reference_dir / reference_name
            _copy_image(reference_source, reference_target, str(reference["sha256"]))
            total_bytes += reference_target.stat().st_size
            reference_count += 1
            reference_rel = f"reference/{reference_name}"

        public_items.append(
            {
                "review_id": review_id,
                "image": f"images/{primary_name}",
                "reference_image": reference_rel,
            }
        )
        truth.append(
            {
                "review_id": review_id,
                "gold_id": gold_id,
                "source_kind": row["source_kind"],
                "source_dataset": row["source_dataset"],
                "source_record_id": row["source_record_id"],
                "source_annotation_type": row.get("source_annotation_type"),
                "shape_label": row.get("shape_label"),
                "migration_status": row.get("migration_status"),
                "split": row.get("split"),
                "decision_time": row.get("decision_time"),
                "original_primary_path": primary["path"],
                "original_primary_sha256": primary["sha256"],
                "original_reference_path": reference["path"] if reference else None,
                "original_reference_sha256": reference["sha256"] if reference else None,
                "public_image": f"images/{primary_name}",
                "public_reference_image": reference_rel,
                "holdout_read": False,
            }
        )
        source_counts[str(row["source_kind"])] += 1
        label_counts[str(row.get("shape_label"))] += 1

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "purpose": "owner keep/remove triage over original source evidence",
        "gold_events_sha256": gold_sha256,
        "n_items": len(public_items),
        "items": public_items,
        "truth_fields_exposed": False,
        "w10_images_used": False,
        "holdout_read": False,
    }
    write_json(public / "manifest.json", manifest)
    write_jsonl(admin / "truth.jsonl", truth)
    (public / "index.html").write_text(
        _page_html(public_items, gold_sha256=gold_sha256), encoding="utf-8"
    )
    prereg = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "gold_events_sha256": gold_sha256,
        "population_n": len(public_items),
        "decisions": sorted(VALID_DECISIONS),
        "decision_meaning": {
            "KEEP": "retain this row as a candidate for the next dataset version",
            "REMOVE": "exclude this row from the next dataset version",
            "UNCERTAIN": "do not train; send to a later adjudication queue",
        },
        "completion_required_before_dataset_rebuild": True,
        "current_dataset_is_not_modified": True,
        "training_eligible_changed": False,
        "original_images_may_include_post_decision_human_context": True,
        "original_images_for_review_only": True,
        "holdout_read": False,
    }
    write_json(pack_root / "prereg.json", prereg)
    results_dir = pack_root / "review_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "README.md").write_text(
        "# Original-source triage results\n\n"
        "完成全部 2,649 张后，在页面点击“导出 JSON”。不要直接覆盖当前数据集；"
        "导出答案必须先按 admin/truth.jsonl 回连，生成新版本 manifest。\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_items": len(public_items),
        "n_reference_images": reference_count,
        "source_counts": dict(sorted(source_counts.items())),
        "label_counts_private": dict(sorted(label_counts.items())),
        "gold_events_sha256": gold_sha256,
        "public_manifest_sha256": sha256_file(public / "manifest.json"),
        "truth_sha256": sha256_file(admin / "truth.jsonl"),
        "page_sha256": sha256_file(public / "index.html"),
        "copied_image_bytes": total_bytes,
        "missing_original_images": 0,
        "w10_images_used": False,
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
    }
    write_json(pack_root / "build_summary.json", summary)
    return summary


def build_original_review(
    project_root: Path,
    legacy_yoyo_root: Path,
    dataset_root: Path,
    pack_root: Path,
    *,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Resolve and package all 2,649 original images without reading OHLC."""

    validated = validate_dataset(dataset_root)
    events = validated["events"]
    catalog = OriginalSourceCatalog(project_root, legacy_yoyo_root)
    resolved = [catalog.resolve(event) for event in events]
    if len(resolved) != 2649:
        raise AuditBuildError(f"expected 2,649 original rows, got {len(resolved)}")
    summary = build_pack_from_resolved(
        resolved,
        pack_root,
        gold_sha256=str(validated["gold_sha256"]),
        seed=seed,
    )
    summary.update(
        {
            "dataset_root": str(Path(dataset_root).resolve()),
            "project_root": str(Path(project_root).resolve()),
            "legacy_yoyo_root": str(Path(legacy_yoyo_root).resolve()),
        }
    )
    write_json(Path(pack_root) / "build_summary.json", summary)
    return summary


def summarize_export(pack_root: Path, answers_path: Path) -> dict[str, Any]:
    """Validate an exported keep/remove file and join it to private lineage."""

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
        joined.append({**truth_by_id[review_id], "owner_triage_decision": decision, "note": answer.get("note")})
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
