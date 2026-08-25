#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the offline Owner category-and-geometry review page for 9,000 candidates.

Inputs are a hash-pinned PENDING candidate manifest, its already-rendered
48-bar review PNGs, the Local Signal V2 protocol and the review
preregistration.  The script rehashes every review PNG and embeds only identity
metadata plus relative image paths in one HTML page.  It does not read OHLCV,
holdout data, models, labels, forward state or order state, and it cannot write
Owner answers, training images, negatives, YOLO labels or a training job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from yoyo.datasets.candidate_boundary_review import validate_source_rows


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREREG = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-boundary-review9000-v1"
    / "preregistration.json"
)
MODULE_PATH = ROOT / "yoyo/datasets/candidate_boundary_review.py"


def sha256_file(path: Path) -> str:
    """Hash one file without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_path(value: str | Path) -> Path:
    """Resolve a repository-relative preregistration path without allowing escape."""

    path = (ROOT / Path(value)).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError(f"path escapes repository: {value}")
    return path


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_builder_committed(paths: Sequence[Path]) -> str:
    """Fail before artifact generation unless all behavior/config is on main."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("boundary review builder must run on main")
    relatives = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = git_output("status", "--short", "--", *relatives)
    if dirty:
        raise RuntimeError(f"boundary review builder inputs are not committed:\n{dirty}")
    commits = [git_output("log", "-1", "--format=%H", "--", relative) for relative in relatives]
    if any(len(commit) != 40 for commit in commits):
        raise RuntimeError("could not resolve builder/config commits")
    return git_output("rev-parse", "HEAD")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _json_for_script(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).replace("</", "<\\/")


def build_items(
    rows: Sequence[Mapping[str, Any]],
    *,
    final_html: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rehash review images and build the minimal browser identity ledger."""

    items: list[dict[str, Any]] = []
    bytes_total = 0
    for number, row in enumerate(rows, 1):
        image = repo_path(str(row["review_path"]))
        if not image.is_file():
            raise FileNotFoundError(image)
        digest = sha256_file(image)
        if digest != str(row["review_sha256"]):
            raise ValueError(f"review image hash drifted: {image}")
        bytes_total += image.stat().st_size
        items.append(
            {
                "event_id": str(row["event_id"]),
                "symbol": str(row["symbol"]),
                "direction": str(row["direction"]),
                "rank": int(row["rank"]),
                "anchor_time": str(row["anchor_time"]),
                "review_sha256": digest,
                "image": Path(os.path.relpath(image, final_html.parent)).as_posix(),
                "source_order": number,
            }
        )
    return items, {
        "files_rehashed": len(items),
        "bytes_rehashed": bytes_total,
        "hash_mismatches": 0,
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>15m 候选逐样本边界审核</title>
  <style>
    :root{--ink:#16222d;--muted:#627283;--line:#d4dde5;--bg:#eef2f5;--card:#fff;--blue:#1776b6;--orange:#dc7a16;--green:#16865a;--red:#c8444d;--amber:#b77a12}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
    header{position:sticky;top:0;z-index:20;background:#fffffffa;border-bottom:1px solid var(--line);box-shadow:0 3px 14px #15253512}
    .top{max-width:1500px;margin:auto;padding:13px 18px 11px}.titleline{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}h1{margin:0;font-size:24px}.sub{color:var(--muted);font-size:14px}
    .progress{height:7px;background:#e5ebef;border-radius:999px;overflow:hidden;margin:9px 0}.progress span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--green),#42b889)}
    .toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.toolbar select,.toolbar button,.toolbar input{border:1px solid #b8c5cf;background:#fff;border-radius:8px;padding:7px 10px;font:inherit}.toolbar button{cursor:pointer}.toolbar .primary{background:#176fa4;color:#fff;border-color:#176fa4;font-weight:700}.stats{margin-left:auto;font-weight:750;font-variant-numeric:tabular-nums}
    main{max-width:1500px;margin:auto;padding:17px 18px 65px}.notice{border:1px solid #efcb7b;background:#fff7df;border-radius:10px;padding:10px 13px;line-height:1.55;margin-bottom:12px}.notice.long{border-color:#d6b4e3;background:#fbf2ff}.notice strong{color:#8d5200}
    .card{background:var(--card);border-radius:13px;box-shadow:0 2px 13px #1b304018;overflow:hidden}.cardhead{display:flex;align-items:center;gap:10px;padding:11px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}.badge{padding:4px 9px;border-radius:999px;font-size:13px;font-weight:800}.badge.SHORT{background:#ffe4e7;color:#a32734}.badge.LONG{background:#dff1ff;color:#0e6594}.identity{font-weight:800}.meta{color:var(--muted);font-size:13px}.current{margin-left:auto;font-weight:800}
    .stage{position:relative;background:#fff;aspect-ratio:1280/770;overflow:hidden}.stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block}.band{position:absolute;pointer-events:none;display:none;top:9.0909%;height:88.3117%}.inputband{border:3px solid #1776b6;background:#1776b611}.coreband{border:4px solid #dc7a16;background:#dc7a1624}.bandlabel{position:absolute;top:2px;left:3px;background:#ffffffdc;border-radius:4px;padding:2px 5px;font-size:11px;font-weight:800;white-space:nowrap}.coreband .bandlabel{top:29px}.imageerror{position:absolute;inset:45% 10% auto;text-align:center;color:var(--red);font-weight:800}
    .below{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(330px,.75fr);gap:0;border-top:1px solid var(--line)}.geometry{padding:13px 14px;border-right:1px solid var(--line)}.group{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:0 0 10px}.group b{width:150px}.choice{border:1px solid #b9c5ce;background:#fff;border-radius:8px;min-width:43px;padding:8px 10px;cursor:pointer;font-weight:700}.choice.active{background:#173b50;color:#fff;border-color:#173b50}.geomsummary{background:#f1f6f9;border-radius:8px;padding:9px 11px;color:#334a5c;font-variant-numeric:tabular-nums}.geomsummary.invalid{background:#fff0f0;color:#a2303a}
    .decision{padding:13px 14px}.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.actions button{border:0;border-radius:9px;padding:12px 5px;color:#fff;font-size:16px;font-weight:800;cursor:pointer}.keep{background:var(--green)}.drop{background:var(--red)}.uncertain{background:var(--amber)}.actions button.active{outline:4px solid #192a3880;outline-offset:2px}.note{width:100%;margin-top:11px;border:1px solid #bbc7d0;border-radius:8px;padding:9px 10px;font:inherit}.nav{display:flex;gap:8px;margin-top:11px;flex-wrap:wrap}.nav button{border:1px solid #b8c5cf;background:#fff;border-radius:8px;padding:8px 11px;cursor:pointer}.nav .danger{color:#a72c37}.keys{margin-top:9px;color:var(--muted);font-size:12px;line-height:1.5}
    footer{max-width:1500px;margin:13px auto 0;padding:0 18px;color:var(--muted);font-size:13px;line-height:1.55}
    .hidden{display:none!important}@media(max-width:900px){header{position:static}.below{grid-template-columns:1fr}.geometry{border-right:0;border-bottom:1px solid var(--line)}.stats{width:100%;margin-left:0}.group b{width:100%}}
  </style>
</head>
<body>
<header><div class="top">
  <div class="titleline"><h1>15m 六均线启动候选：逐样本类别 + 边界</h1><span class="sub" id="position">载入中…</span></div>
  <div class="progress"><span id="bar"></span></div>
  <div class="toolbar">
    <select id="sideFilter" aria-label="方向筛选"><option value="SHORT" selected>先审 SHORT</option><option value="LONG">只审 LONG</option><option value="ALL">全部方向</option></select>
    <select id="answerFilter" aria-label="状态筛选"><option value="ALL">全部状态</option><option value="PENDING" selected>只看未审</option><option value="KEEP">只看 KEEP</option><option value="DROP">只看 DROP</option><option value="UNCERTAIN">只看 UNCERTAIN</option></select>
    <label>跳到全局 <input id="jump" type="number" min="1" max="__TOTAL__" step="1" style="width:92px"></label>
    <label><input id="autoNext" type="checkbox" checked> 裁决后下一张</label>
    <button id="import">导入进度</button><input id="importFile" class="hidden" type="file" accept="application/json">
    <button id="export" class="primary">导出审核 JSON</button>
    <span class="stats" id="stats"></span>
  </div>
</div></header>
<main>
  <div class="notice"><strong>这是审核页，不是自动标注器。</strong>蓝线 t-3 只是用户要求的视觉参考；每张 KEEP 必须另选 W14–22、核心 4–7 根和确认 3–5 根。图中 t 之后的走势只供人工判断，不会进入训练输入。DROP 也不会自动变成负例。</div>
  <div class="notice long hidden" id="longNotice"><strong>LONG 仍是 mirror_unconfirmed。</strong>可以记录逐样本观察与边界，但在另行确认多头镜像协议前，它既不能进正例，也不能进负例。</div>
  <section class="card">
    <div class="cardhead"><span class="badge" id="side"></span><span class="identity" id="identity"></span><span class="meta" id="meta"></span><span class="current" id="current"></span></div>
    <div class="stage"><img id="chart" alt="候选审核图"><div class="band inputband" id="inputBand"><span class="bandlabel" id="inputLabel"></span></div><div class="band coreband" id="coreBand"><span class="bandlabel" id="coreLabel"></span></div><div class="imageerror" id="imageError"></div></div>
    <div class="below">
      <div class="geometry">
        <div class="group"><b>确认根数（t 后沿）</b><button class="choice" data-field="confirmation_bars" data-value="3">3</button><button class="choice" data-field="confirmation_bars" data-value="4">4</button><button class="choice" data-field="confirmation_bars" data-value="5">5</button></div>
        <div class="group"><b>核心宽度</b><button class="choice" data-field="core_width_bars" data-value="4">4</button><button class="choice" data-field="core_width_bars" data-value="5">5</button><button class="choice" data-field="core_width_bars" data-value="6">6</button><button class="choice" data-field="core_width_bars" data-value="7">7</button></div>
        <div class="group"><b>完整输入窗</b><button class="choice" data-field="input_window_bars" data-value="14">14</button><button class="choice" data-field="input_window_bars" data-value="15">15</button><button class="choice" data-field="input_window_bars" data-value="16">16</button><button class="choice" data-field="input_window_bars" data-value="17">17</button><button class="choice" data-field="input_window_bars" data-value="18">18</button><button class="choice" data-field="input_window_bars" data-value="19">19</button><button class="choice" data-field="input_window_bars" data-value="20">20</button><button class="choice" data-field="input_window_bars" data-value="21">21</button><button class="choice" data-field="input_window_bars" data-value="22">22</button></div>
        <div class="geomsummary invalid" id="geomSummary">KEEP 前必须逐项选择；本页不预填统一框。</div>
      </div>
      <div class="decision">
        <div class="actions"><button class="keep" data-decision="KEEP">K · KEEP</button><button class="drop" data-decision="DROP">X · DROP</button><button class="uncertain" data-decision="UNCERTAIN">C · 待定</button></div>
        <input id="note" class="note" maxlength="2000" placeholder="备注（可空）">
        <div class="nav"><button id="prev">J / ← 上一张</button><button id="next">L / → 下一张</button><button id="undo">U · 撤销</button><button id="clearCurrent" class="danger">清除本张</button></div>
        <div class="keys">快捷键：3/4/5=确认；Q/W/E/R=核心4/5/6/7；[ / ]=缩短/加长输入窗；K/X/C=裁决；J/L=前后；U=撤销。</div>
      </div>
    </div>
  </section>
</main>
<footer>进度只保存在当前浏览器 localStorage；点击“导出审核 JSON”才得到可审计回执。页面没有服务端写盘、没有批量认可、没有训练按钮。完整导出仍须经仓库 summarizer 校验，所有行继续 training_eligible=false / production_eligible=false。</footer>
<script>
const ITEMS=__ITEMS__;
const CONFIG=__CONFIG__;
const BY_ID=new Map(ITEMS.map(x=>[x.event_id,x]));
const STORAGE_KEY=`boundary-review::${CONFIG.pack_id}::${CONFIG.source_manifest_sha256}`;
const ALLOWED=new Set(['KEEP','DROP','UNCERTAIN']);
let state={answers:{},drafts:{},cursor_id:null,side_filter:'SHORT',answer_filter:'PENDING'};
try{const raw=JSON.parse(localStorage.getItem(STORAGE_KEY)||'null');if(raw&&typeof raw==='object')state={...state,...raw}}catch(_){ }
let undoStack=[];
function $(id){return document.getElementById(id)}
function save(){localStorage.setItem(STORAGE_KEY,JSON.stringify(state))}
function currentItem(){return BY_ID.get(state.cursor_id)||filtered()[0]||ITEMS[0]}
function decisionOf(item){return state.answers[item.event_id]?.decision||'PENDING'}
function filtered(){return ITEMS.filter(item=>(state.side_filter==='ALL'||item.direction===state.side_filter)&&(state.answer_filter==='ALL'||decisionOf(item)===state.answer_filter))}
function identityBase(item){return {event_id:item.event_id,symbol:item.symbol,direction:item.direction,anchor_time:item.anchor_time,review_sha256:item.review_sha256}}
function geometry(draft){
  const W=Number(draft.input_window_bars),width=Number(draft.core_width_bars),confirm=Number(draft.confirmation_bars);
  if(!(W>=14&&W<=22&&width>=4&&width<=7&&confirm>=3&&confirm<=5))return null;
  const input_end_review_i=30,input_start_review_i=31-W,core_end_review_i=30-confirm,core_start_review_i=core_end_review_i-width+1;
  const box_center_ratio=Number(((((core_start_review_i+core_end_review_i)/2)-input_start_review_i)/(input_end_review_i-input_start_review_i)).toFixed(6));
  return {input_start_review_i,input_end_review_i,input_window_bars:W,core_start_review_i,core_end_review_i,core_width_bars:width,confirmation_bars:confirm,box_center_ratio};
}
function nullGeometry(){return {input_start_review_i:null,input_end_review_i:null,input_window_bars:null,core_start_review_i:null,core_end_review_i:null,core_width_bars:null,confirmation_bars:null,box_center_ratio:null}}
function draftFor(item){const answer=state.answers[item.event_id]||{},draft=state.drafts[item.event_id]||{};return {...draft,input_window_bars:answer.input_window_bars??draft.input_window_bars,core_width_bars:answer.core_width_bars??draft.core_width_bars,confirmation_bars:answer.confirmation_bars??draft.confirmation_bars,note:answer.note??draft.note??''}}
function xAt(i){return 12+(i/47)*1256}
function paintBand(el,a,b){const half=(1256/47)/2,left=Math.max(12,xAt(a)-half),right=Math.min(1268,xAt(b)+half);el.style.left=`${100*left/1280}%`;el.style.width=`${100*(right-left)/1280}%`;el.style.display='block'}
function setDraft(field,value){const item=currentItem(),id=item.event_id,draft=draftFor(item);draft[field]=value;state.drafts[id]=draft;const old=state.answers[id];if(old?.decision==='KEEP'){const g=geometry(draft);if(g)state.answers[id]={...identityBase(item),decision:'KEEP',reviewed_at:new Date().toISOString(),note:draft.note||null,...g}}save();render()}
function stats(){const counts={KEEP:0,DROP:0,UNCERTAIN:0,PENDING:0};ITEMS.forEach(i=>counts[decisionOf(i)]++);const done=ITEMS.length-counts.PENDING;$('stats').textContent=`已审 ${done}/${ITEMS.length} · K ${counts.KEEP} · X ${counts.DROP} · ? ${counts.UNCERTAIN}`;$('bar').style.width=`${100*done/ITEMS.length}%`;return counts}
function render(){
  let list=filtered();if(!list.length){state.answer_filter='ALL';$('answerFilter').value='ALL';list=filtered()}
  let item=currentItem();if(!list.some(x=>x.event_id===item.event_id)){item=list[0]||ITEMS[0];state.cursor_id=item.event_id}
  const answer=state.answers[item.event_id]||{},draft=draftFor(item),g=geometry(draft),pos=list.findIndex(x=>x.event_id===item.event_id);
  $('position').textContent=`筛选内 ${pos+1}/${list.length} · 全局 ${item.source_order}/${ITEMS.length}`;$('side').textContent=item.direction;$('side').className=`badge ${item.direction}`;$('identity').textContent=`${item.symbol} · #${item.rank}`;$('meta').textContent=`${item.anchor_time} · ${item.event_id}`;$('current').textContent=answer.decision?`当前 ${answer.decision}`:'未审核';
  $('chart').src=item.image;$('chart').onerror=()=>$('imageError').textContent='图片加载失败：请保持 review_charts 本地目录完整';$('chart').onload=()=>$('imageError').textContent='';
  $('longNotice').classList.toggle('hidden',item.direction!=='LONG');$('note').value=draft.note||'';
  document.querySelectorAll('.choice').forEach(b=>b.classList.toggle('active',Number(draft[b.dataset.field])===Number(b.dataset.value)));
  document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===answer.decision));
  if(g){paintBand($('inputBand'),g.input_start_review_i,g.input_end_review_i);paintBand($('coreBand'),g.core_start_review_i,g.core_end_review_i);$('inputLabel').textContent=`训练输入 W${g.input_window_bars}: i${g.input_start_review_i}–${g.input_end_review_i}`;$('coreLabel').textContent=`核心 ${g.core_width_bars}根: i${g.core_start_review_i}–${g.core_end_review_i}`;$('geomSummary').classList.remove('invalid');$('geomSummary').textContent=`确认 ${g.confirmation_bars} 根 · 核心 ${g.core_width_bars} 根 · 完整窗 W${g.input_window_bars} · 框中心 ${(100*g.box_center_ratio).toFixed(1)}%`}
  else{$('inputBand').style.display='none';$('coreBand').style.display='none';$('geomSummary').classList.add('invalid');$('geomSummary').textContent='KEEP 前必须逐项选择；本页不预填统一框。'}
  stats();save();prefetch(list,pos)
}
function snapshot(){undoStack.push(JSON.stringify(state));if(undoStack.length>50)undoStack.shift()}
function decide(value){const item=currentItem(),draft=draftFor(item),g=geometry(draft);if(value==='KEEP'&&!g){alert('KEEP 需要先明确选择确认根数、核心宽度和完整输入窗。');return}snapshot();state.answers[item.event_id]={...identityBase(item),decision:value,reviewed_at:new Date().toISOString(),note:draft.note||null,...(value==='KEEP'?g:nullGeometry())};save();if($('autoNext').checked)step(1);else render()}
function step(delta){const list=filtered();if(!list.length)return;const item=currentItem();let i=list.findIndex(x=>x.event_id===item.event_id);if(i<0)i=0;state.cursor_id=list[Math.max(0,Math.min(list.length-1,i+delta))].event_id;save();render()}
function prefetch(list,pos){for(let k=1;k<=2;k++){const item=list[pos+k];if(item){const img=new Image();img.src=item.image}}}
function exportPayload(){const answers=ITEMS.map(i=>state.answers[i.event_id]).filter(a=>a&&ALLOWED.has(a.decision));return {schema_version:1,pack_id:CONFIG.pack_id,source_manifest_sha256:CONFIG.source_manifest_sha256,protocol_sha256:CONFIG.protocol_sha256,exported_at:new Date().toISOString(),complete:answers.length===ITEMS.length,n_total:ITEMS.length,n_answered:answers.length,answers}}
document.querySelectorAll('.choice').forEach(b=>b.onclick=()=>setDraft(b.dataset.field,Number(b.dataset.value)));
document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>decide(b.dataset.decision));
$('note').oninput=e=>{const item=currentItem(),id=item.event_id,draft=draftFor(item);draft.note=e.target.value;state.drafts[id]=draft;if(state.answers[id]){state.answers[id].note=e.target.value||null;state.answers[id].reviewed_at=new Date().toISOString()}save()};
$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('undo').onclick=()=>{if(!undoStack.length)return;state=JSON.parse(undoStack.pop());save();render()};$('clearCurrent').onclick=()=>{const item=currentItem();if(!confirm('清除本张裁决和几何草稿？'))return;snapshot();delete state.answers[item.event_id];delete state.drafts[item.event_id];save();render()};
$('sideFilter').value=state.side_filter;$('answerFilter').value=state.answer_filter;$('sideFilter').onchange=e=>{state.side_filter=e.target.value;state.cursor_id=null;save();render()};$('answerFilter').onchange=e=>{state.answer_filter=e.target.value;state.cursor_id=null;save();render()};
$('jump').onchange=e=>{const n=Math.max(1,Math.min(ITEMS.length,Number(e.target.value)||1)),item=ITEMS[n-1];state.side_filter='ALL';state.answer_filter='ALL';state.cursor_id=item.event_id;$('sideFilter').value='ALL';$('answerFilter').value='ALL';save();render()};
$('export').onclick=()=>{const out=exportPayload(),blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${CONFIG.pack_id}_answers_${out.n_answered}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)};
$('import').onclick=()=>$('importFile').click();$('importFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{const out=JSON.parse(await file.text());if(out.pack_id!==CONFIG.pack_id||out.source_manifest_sha256!==CONFIG.source_manifest_sha256||out.protocol_sha256!==CONFIG.protocol_sha256||!Array.isArray(out.answers))throw new Error('不是本审核包的导出文件');snapshot();for(const a of out.answers){const item=BY_ID.get(a.event_id);if(!item||!ALLOWED.has(a.decision))continue;if(item.symbol!==a.symbol||item.direction!==a.direction||item.anchor_time!==a.anchor_time||item.review_sha256!==a.review_sha256)throw new Error(`身份不匹配 ${a.event_id}`);state.answers[a.event_id]=a;if(a.decision==='KEEP')state.drafts[a.event_id]={input_window_bars:a.input_window_bars,core_width_bars:a.core_width_bars,confirmation_bars:a.confirmation_bars,note:a.note||''};else state.drafts[a.event_id]={note:a.note||''}}save();render();alert(`已导入 ${out.answers.length} 条；最终仍须仓库校验。`)}catch(err){alert(`导入失败：${err.message}`)}e.target.value=''};
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;const k=e.key.toLowerCase();if(['3','4','5'].includes(e.key))setDraft('confirmation_bars',Number(e.key));else if({q:4,w:5,e:6,r:7}[k])setDraft('core_width_bars',{q:4,w:5,e:6,r:7}[k]);else if(e.key==='['||e.key===']'){const d=draftFor(currentItem()),cur=Number(d.input_window_bars)||18;setDraft('input_window_bars',Math.max(14,Math.min(22,cur+(e.key==='['?-1:1))))}else if(k==='k')decide('KEEP');else if(k==='x')decide('DROP');else if(k==='c'||e.key==='?')decide('UNCERTAIN');else if(k==='j'||e.key==='ArrowLeft'){e.preventDefault();step(-1)}else if(k==='l'||e.key==='ArrowRight'||e.key===' '){e.preventDefault();step(1)}else if(k==='u')$('undo').click()});
if(!state.cursor_id)state.cursor_id=filtered()[0]?.event_id||ITEMS[0].event_id;render();
</script>
</body></html>
"""


def render_html(items: Sequence[Mapping[str, Any]], prereg: Mapping[str, Any]) -> str:
    config = {
        "pack_id": prereg["experiment_id"],
        "source_manifest_sha256": prereg["source"]["candidate_manifest_sha256"],
        "protocol_sha256": prereg["protocol"]["sha256"],
    }
    return (
        HTML_TEMPLATE.replace("__TOTAL__", str(len(items)))
        .replace("__ITEMS__", _json_for_script(items))
        .replace("__CONFIG__", _json_for_script(config))
    )


def build(prereg_path: Path, output_dir: Path | None = None) -> dict[str, Any]:
    prereg_path = prereg_path.resolve()
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    if prereg["experiment_id"] != "exp-15m-ma-launch-boundary-review9000-v1":
        raise ValueError("unexpected boundary review experiment_id")
    builder_commit = verify_builder_committed(
        [Path(__file__).resolve(), MODULE_PATH, prereg_path]
    )

    source_manifest = repo_path(prereg["source"]["candidate_manifest_path"])
    source_prereg = repo_path(prereg["source"]["candidate_preregistration_path"])
    protocol_path = repo_path(prereg["protocol"]["path"])
    gate_path = repo_path(prereg["source"]["training_gate_receipt_path"])
    pinned = {
        source_manifest: prereg["source"]["candidate_manifest_sha256"],
        source_prereg: prereg["source"]["candidate_preregistration_sha256"],
        protocol_path: prereg["protocol"]["sha256"],
        gate_path: prereg["source"]["training_gate_receipt_sha256"],
    }
    for path, expected in pinned.items():
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"hash-pinned input drifted: {path} expected={expected} actual={actual}")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if gate["status"] != "blocked_before_dataset_materialization_and_training":
        raise ValueError("source training gate is no longer fail-closed")
    if gate["training"]["started"] is not False:
        raise ValueError("source training gate unexpectedly reports a training start")

    rows = load_jsonl(source_manifest)
    validate_source_rows(rows)
    expected_rows = int(prereg["source"]["candidate_rows"])
    if len(rows) != expected_rows:
        raise ValueError(f"expected {expected_rows} source rows, got {len(rows)}")
    sides = Counter(str(row["direction"]) for row in rows)
    expected_sides = Counter(
        {key: int(value) for key, value in prereg["source"]["per_side"].items()}
    )
    if sides != expected_sides:
        raise ValueError(f"source side counts drifted: {sides}")

    final_dir = output_dir.resolve() if output_dir else prereg_path.parent / "results"
    building_dir = final_dir.with_name(f"{final_dir.name}.building")
    if final_dir.exists() or building_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite review output: final={final_dir} building={building_dir}"
        )
    public_dir = building_dir / "public"
    public_dir.mkdir(parents=True)
    final_html = final_dir / "public" / "index.html"
    items, image_audit = build_items(rows, final_html=final_html)
    html_text = render_html(items, prereg)
    html_path = public_dir / "index.html"
    html_path.write_text(html_text, encoding="utf-8")

    readme = f"""# 15m 候选逐样本边界审核包

本页覆盖 {len(items):,} 个新候选（SHORT {sides['SHORT']:,} / LONG {sides['LONG']:,}）。
它只在浏览器 localStorage 保存进度并导出 JSON，不会写训练标签、负例或启动训练。

直接打开：`public/index.html`

若浏览器限制本地文件，可在仓库根目录运行：

```bash
python3 -m http.server 8769 --directory {final_dir.relative_to(ROOT)}
```

然后打开 `http://127.0.0.1:8769/public/index.html`。

每张 KEEP 必须明确选择：完整输入 W14–22、核心 4–7 根、确认 3–5 根。
蓝色 t-3 竖线不等于答案。LONG 仍为 `mirror_unconfirmed`。完整导出返回后，运行
`scripts/summarize_15m_candidate_boundary_review.py` 做 fail-closed 校验；校验结果仍不自动授权训练。
"""
    (building_dir / "README.md").write_text(readme, encoding="utf-8")
    receipt = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": prereg["experiment_id"],
        "status": "review_surface_ready_pending_owner_answers",
        "builder_commit": builder_commit,
        "inputs": {
            "preregistration_path": str(prereg_path.relative_to(ROOT)),
            "preregistration_sha256": sha256_file(prereg_path),
            "source_manifest_path": str(source_manifest.relative_to(ROOT)),
            "source_manifest_sha256": sha256_file(source_manifest),
            "protocol_path": str(protocol_path.relative_to(ROOT)),
            "protocol_sha256": sha256_file(protocol_path),
            "training_gate_receipt_sha256": sha256_file(gate_path),
        },
        "counts": {
            "source_rows": len(rows),
            "unique_event_ids": len({row["event_id"] for row in rows}),
            "side_counts": dict(sides),
            "answers_preselected": 0,
            "training_images_generated": 0,
            "yolo_labels_generated": 0,
            "negatives_generated": 0,
            "models_trained": 0,
        },
        "image_audit": image_audit,
        "review_surface": {
            "html_path": str(final_html.relative_to(ROOT)),
            "html_sha256": sha256_file(html_path),
            "html_size_bytes": html_path.stat().st_size,
            "bulk_accept": False,
            "json_import_export": True,
            "keep_requires_explicit_geometry": True,
            "writes_repository_answers": False,
        },
        "eligibility": {
            "training_eligible": False,
            "production_eligible": False,
            "long_direction_status": "mirror_unconfirmed",
        },
        "holdout": {
            "read": False,
            "ohlcv_rows_materialized": 0,
            "source_ohlcv_files_opened": 0,
        },
        "remote": {"writes": 0, "training_started": False},
        "errors": [],
    }
    (building_dir / "build_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(building_dir, final_dir)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    receipt = build(args.prereg, args.out)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
