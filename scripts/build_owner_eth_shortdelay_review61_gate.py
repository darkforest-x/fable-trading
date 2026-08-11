#!/usr/bin/env python3
"""Build an offline Owner confirmation page for 61 rebox proposals.

The page is a review surface, not a training-label writer.  Decisions are kept
in browser localStorage and exported as JSON text for the Owner to paste back
into Codex.  No decision is preselected, and opening the page cannot mutate the
dataset, model, production configuration, or confirmation manifest.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
YOYO_REPO = Path.home() / "yoyo-trading"
for module_path in (ROOT, YOYO_REPO):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_local_signal_v2_stageb import HOLDOUT_START, sha256_file  # noqa: E402
from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: E402
    _utc,
    load_preholdout_prefix,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402


DEFAULT_SOURCE = (
    ROOT / "analysis/output/owner_eth_shortdelay_review200_rebox_v1/proposal_manifest.jsonl"
)
DEFAULT_OUT = (
    ROOT / "analysis/html/p1_owner_eth_shortdelay_review61_owner_gate_20260811.html"
)
PROTOCOL = "owner_eth_shortdelay_review61_owner_gate_v1_20260811"
DEFAULT_FUTURE_OUT = (
    ROOT / "analysis/output/owner_eth_shortdelay_review200_rebox_v1/review_future_only"
)
FUTURE_BARS = 48
ORANGE = (20, 145, 225)
FUTURE_TINT = (244, 238, 255)
BOUNDARY = (180, 90, 120)


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows.sort(key=lambda row: int(str(row["calibration_id"])[1:4]))
    if len(rows) != 61:
        raise ValueError(f"expected 61 proposals, got {len(rows)}")
    if len({row["calibration_id"] for row in rows}) != 61:
        raise ValueError("proposal calibration_id is not unique")
    if any(row.get("sample_owner_confirmed") for row in rows):
        raise ValueError("review page source unexpectedly contains confirmed samples")
    return rows


def _relative_image(row: dict[str, Any], output_path: Path) -> str:
    absolute = ROOT / str(row["proposal_image_path"])
    if not absolute.is_file():
        raise FileNotFoundError(absolute)
    return Path(os.path.relpath(absolute, output_path.parent)).as_posix()


def _relative_future_image(row: dict[str, Any], output_path: Path) -> str:
    absolute = ROOT / str(row["future_review_image_path"])
    if not absolute.is_file():
        raise FileNotFoundError(absolute)
    return Path(os.path.relpath(absolute, output_path.parent)).as_posix()


def _box_rect(image: np.ndarray, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    xc, yc, box_w, box_h = box
    return (
        int(round((xc - box_w / 2) * width)),
        int(round((yc - box_h / 2) * height)),
        int(round((xc + box_w / 2) * width)),
        int(round((yc + box_h / 2) * height)),
    )


def render_future_review_image(
    row: dict[str, Any],
    output_dir: Path,
    *,
    future_bars: int = FUTURE_BARS,
) -> dict[str, Any]:
    """Render review-only future context without mutating the training crop."""
    if future_bars <= 0:
        raise ValueError("future_bars must be positive")
    train_image = ROOT / str(row["proposal_image_path"])
    if not train_image.is_file() or sha256_file(train_image) != row["proposal_image_sha256"]:
        raise ValueError(f"training proposal image changed: {row['calibration_id']}")
    train_sha_before = sha256_file(train_image)

    review_start = int(row["proposal_win_start"])
    train_end = int(row["proposal_win_end"])
    review_end = train_end + future_bars
    frame, read_audit = load_preholdout_prefix(ROOT / str(row["source_csv"]), review_end)
    enriched = add_mas(frame)
    window = enriched.iloc[review_start : review_end + 1].reset_index(drop=True)
    expected_len = int(row["proposal_win_len"]) + future_bars
    if len(window) != expected_len:
        raise ValueError(f"short future review window: {row['calibration_id']}")
    if _utc(window.iloc[-1]["open_time"]) >= HOLDOUT_START:
        raise ValueError(f"future review touches holdout: {row['calibration_id']}")

    image, transform = render_chart(window, out_path=None)
    core_start, core_end = map(int, row["proposal_core_local"])
    core_box = yolo_box_from_bars(transform, window, core_start, core_end)
    if core_box is None:
        raise ValueError(f"empty future review core: {row['calibration_id']}")
    train_end_local = int(row["proposal_win_len"]) - 1
    first_future_local = train_end_local + 1
    boundary_x = (transform.x_at(train_end_local) + transform.x_at(first_future_local)) // 2

    tint = image.copy()
    cv2.rectangle(tint, (boundary_x, 42), (image.shape[1] - 1, image.shape[0] - 1), FUTURE_TINT, -1)
    image[42:] = cv2.addWeighted(image[42:], 0.68, tint[42:], 0.32, 0)
    rect = _box_rect(image, core_box)
    cv2.rectangle(image, (rect[0], rect[1]), (rect[2], rect[3]), ORANGE, 4, cv2.LINE_AA)
    cv2.line(image, (boundary_x, 42), (boundary_x, image.shape[0] - 1), BOUNDARY, 4, cv2.LINE_AA)
    cv2.rectangle(image, (0, 0), (image.shape[1], 42), (250, 250, 250), -1)
    cv2.putText(
        image,
        f"{row['calibration_id']} | LEFT=TRAIN INPUT | RIGHT=FUTURE {future_bars} BARS / {future_bars*15//60}H REVIEW ONLY",
        (10, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.57,
        (22, 32, 39),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        "FUTURE REGION IS FOR OWNER REVIEW ONLY - NEVER ENTERS TRAINING IMAGE OR LABEL",
        (max(boundary_x + 10, 10), image.shape[0] - 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        BOUNDARY,
        2,
        cv2.LINE_AA,
    )
    path = output_dir / f"{row['calibration_id']}_future{future_bars}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"failed to write {path}")
    if sha256_file(train_image) != train_sha_before:
        raise RuntimeError(f"training proposal mutated while rendering future: {row['calibration_id']}")

    output = dict(row)
    output.update(
        {
            "future_review_only": True,
            "future_bars": future_bars,
            "future_hours": future_bars * 15 / 60,
            "future_review_start_global": review_start,
            "future_review_train_end_global": train_end,
            "future_review_end_global": review_end,
            "future_review_start_time": _utc(window.iloc[0]["open_time"]).isoformat(),
            "future_review_train_end_time": _utc(window.iloc[train_end_local]["open_time"]).isoformat(),
            "future_review_end_time": _utc(window.iloc[-1]["open_time"]).isoformat(),
            "future_review_image_path": str(path.relative_to(ROOT)),
            "future_review_image_sha256": sha256_file(path),
            "future_review_read_audit": read_audit,
            "training_image_path_unchanged": str(row["proposal_image_path"]),
            "training_image_sha256_unchanged": train_sha_before,
            "future_data_in_training_image": False,
            "future_data_in_training_label": False,
            "sample_owner_confirmed": False,
            "training_eligible": False,
            "production_eligible": False,
        }
    )
    return output


def _card(row: dict[str, Any], output_path: Path) -> str:
    sample_id = html.escape(str(row["calibration_id"]), quote=True)
    symbol = html.escape(str(row["symbol"]), quote=True)
    image_src = html.escape(_relative_image(row, output_path), quote=True)
    future_src = html.escape(_relative_future_image(row, output_path), quote=True)
    old_start, old_end = map(int, row["legacy_core_local_in_frozen_window"])
    new_start, new_end = map(int, row["proposal_selected_local_in_frozen_window"])
    return f"""
      <article class="sample-card" id="card-{sample_id}" data-sample="{sample_id}" data-decision="pending">
        <div class="card-head">
          <div><span class="sample-id">{sample_id}</span><span class="symbol">{symbol}</span></div>
          <span class="decision-chip">未确认</span>
        </div>
        <div class="image-pair">
          <div><div class="panel-label">训练输入：只用这张短窗</div>
            <button class="image-button" type="button" onclick="openZoom('{sample_id}','train')" aria-label="放大 {sample_id} 训练输入">
              <img src="{image_src}" alt="{sample_id} 训练输入新旧框对照" loading="lazy" data-role="train" data-full-src="{image_src}">
            </button>
          </div>
          <div><div class="panel-label future-label">人工审核：额外未来48根 / 12小时</div>
            <button class="image-button" type="button" onclick="openZoom('{sample_id}','future')" aria-label="放大 {sample_id} 未来走势">
              <img src="{future_src}" alt="{sample_id} 未来48根审核对照" loading="lazy" data-role="future" data-full-src="{future_src}">
            </button>
          </div>
        </div>
        <div class="geometry">
          <span>新框 <b>{new_start}–{new_end}</b>（{int(row['proposal_core_bars'])}根）</span>
          <span>旧框 {old_start}–{old_end}</span>
          <span>确认 {int(row['post_bars'])}根</span>
          <span>完整窗 W{int(row['proposal_win_len'])}</span>
        </div>
        <div class="decision-buttons" role="group" aria-label="{sample_id} 裁决">
          <button type="button" data-choice="accept" onclick="setDecision('{sample_id}','accept')">✓ 认可新框</button>
          <button type="button" data-choice="adjust" onclick="setDecision('{sample_id}','adjust')">↔ 还要改</button>
          <button type="button" data-choice="reject" onclick="setDecision('{sample_id}','reject')">✕ 剔除</button>
        </div>
      </article>"""


def render_html(rows: list[dict[str, Any]], source: Path, output_path: Path) -> str:
    widths = Counter(int(row["proposal_core_bars"]) for row in rows)
    posts = Counter(int(row["post_bars"]) for row in rows)
    sample_ids = [str(row["calibration_id"]) for row in rows]
    cards = "\n".join(_card(row, output_path) for row in rows)
    source_hash = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    js_ids = json.dumps(sample_ids, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Owner确认：61张空头核心改框</title>
  <style>
    :root {{ --ink:#17212b; --muted:#607080; --line:#d9e0e6; --bg:#f4f7f9; --card:#fff;
      --orange:#d98700; --green:#198754; --red:#dc3545; --blue:#1769aa; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .top {{ position:sticky; top:0; z-index:20; background:rgba(255,255,255,.97); border-bottom:1px solid var(--line); box-shadow:0 3px 16px rgba(18,35,52,.08); }}
    .top-inner {{ max-width:1480px; margin:auto; padding:16px 22px 14px; }}
    h1 {{ margin:0 0 8px; font-size:25px; }}
    .explain {{ margin:0; color:#354758; line-height:1.55; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:8px 18px; margin:10px 0; font-size:14px; }}
    .legend b.orange {{ color:var(--orange); }} .legend b.red {{ color:var(--red); }}
    .toolbar {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:10px; }}
    button, select {{ font:inherit; }}
    .toolbar button, .toolbar select {{ border:1px solid #bac6d0; background:#fff; color:var(--ink); border-radius:8px; padding:8px 12px; cursor:pointer; }}
    .toolbar button.primary {{ background:var(--green); color:#fff; border-color:var(--green); font-weight:700; }}
    .toolbar button.copy {{ background:var(--blue); color:#fff; border-color:var(--blue); font-weight:700; }}
    .stats {{ margin-left:auto; font-weight:700; font-variant-numeric:tabular-nums; }}
    main {{ max-width:1480px; margin:0 auto; padding:20px 18px 70px; }}
    .how {{ background:#fff8e7; border:1px solid #f0d08c; border-radius:10px; padding:12px 15px; margin-bottom:16px; line-height:1.55; }}
    .source-note {{ background:#eaf4ff; border:1px solid #a9cdeb; border-radius:10px; padding:12px 15px; margin-bottom:12px; line-height:1.55; }}
    .grid {{ display:grid; grid-template-columns:1fr; gap:18px; }}
    .sample-card {{ background:var(--card); border:3px solid transparent; border-radius:12px; overflow:hidden; box-shadow:0 2px 11px rgba(30,50,70,.09); scroll-margin-top:210px; }}
    .sample-card[data-decision="accept"] {{ border-color:var(--green); }}
    .sample-card[data-decision="adjust"] {{ border-color:var(--orange); }}
    .sample-card[data-decision="reject"] {{ border-color:var(--red); }}
    .card-head {{ display:flex; justify-content:space-between; align-items:center; gap:10px; padding:10px 12px; border-bottom:1px solid var(--line); }}
    .sample-id {{ font-weight:800; margin-right:10px; }} .symbol {{ color:var(--muted); font-size:14px; }}
    .decision-chip {{ background:#eef2f5; color:#586a79; border-radius:999px; padding:4px 9px; font-size:13px; font-weight:700; }}
    .image-button {{ display:block; width:100%; padding:0; margin:0; border:0; background:#fff; cursor:zoom-in; }}
    .image-button img {{ display:block; width:100%; height:auto; aspect-ratio:1280/742; object-fit:contain; }}
    .image-pair {{ display:grid; grid-template-columns:1fr 1fr; gap:1px; background:var(--line); }}
    .image-pair > div {{ background:#fff; }}
    .panel-label {{ padding:7px 10px; font-size:13px; font-weight:800; color:#304557; background:#eef4f7; }}
    .future-label {{ color:#873a77; background:#f8edf7; }}
    .geometry {{ display:flex; flex-wrap:wrap; gap:6px 14px; padding:9px 12px; color:#465867; font-size:13px; border-top:1px solid #eef1f3; }}
    .geometry b {{ color:var(--orange); }}
    .decision-buttons {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; padding:0 12px 12px; }}
    .decision-buttons button {{ border:1px solid #c4ced6; background:#fff; border-radius:8px; padding:9px 5px; cursor:pointer; }}
    .decision-buttons button[data-choice="accept"].active {{ background:var(--green); color:white; border-color:var(--green); }}
    .decision-buttons button[data-choice="adjust"].active {{ background:var(--orange); color:white; border-color:var(--orange); }}
    .decision-buttons button[data-choice="reject"].active {{ background:var(--red); color:white; border-color:var(--red); }}
    textarea {{ width:100%; min-height:145px; margin-top:18px; border:1px solid #b9c5ce; border-radius:10px; padding:10px; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; background:#fff; }}
    dialog {{ width:min(96vw,1500px); max-height:96vh; border:0; border-radius:12px; padding:10px; box-shadow:0 18px 70px rgba(0,0,0,.35); }}
    dialog::backdrop {{ background:rgba(0,0,0,.72); }} dialog img {{ display:block; width:100%; height:auto; }}
    .zoom-close {{ float:right; margin-bottom:8px; border:0; border-radius:7px; padding:7px 11px; cursor:pointer; }}
    .hidden {{ display:none !important; }}
    @media (max-width:900px) {{ .image-pair {{ grid-template-columns:1fr; }} .stats {{ width:100%; margin:4px 0 0; }} .top {{ position:static; }} .sample-card {{ scroll-margin-top:10px; }} }}
  </style>
</head>
<body>
  <header class="top"><div class="top-inner">
    <h1>Owner确认：61张空头核心改框</h1>
    <p class="explain">这里才是确认页面。每个样本左边是<strong>真正训练输入短窗</strong>，右边额外展示<strong>未来48根K线（12小时）</strong>帮助你人工判断。未来走势只用于审核，绝不进入训练图片或标签。</p>
    <div class="legend">
      <span><b class="orange">橙框</b>＝拟采用的新核心</span><span><b class="red">红虚框</b>＝被替换的旧核心</span>
      <span>右图紫色区域＝训练窗之后的未来</span>
      <span>61张仍未写入训练标签</span><span>当前不会训练或读取holdout</span>
    </div>
    <div class="toolbar">
      <button class="primary" type="button" onclick="setAllAccepted()">✓ 浏览后全部认可</button>
      <button type="button" onclick="clearAll()">清空选择</button>
      <select id="filter" onchange="applyFilter()" aria-label="筛选裁决状态">
        <option value="all">显示全部</option><option value="pending">只看未确认</option>
        <option value="accept">只看已认可</option><option value="adjust">只看还要改</option><option value="reject">只看剔除</option>
      </select>
      <button class="copy" type="button" onclick="copyResults()">复制确认结果</button>
      <span class="stats" id="stats">未确认 61 / 61</span>
    </div>
  </div></header>
  <main>
    <div class="source-note"><strong>数据来源与数量：</strong>本页61张属于当前200张语义校准包；200张是从Stage-A的2,020个train正事件中、满足旧框后3–5根条件的316个候选里抽出的，不是最终训练集。Stage-A正事件总池为2,378个（train 2,020 / val 358），本机15分钟原始缓存最长约430天。这里“3–5”是3–5根15分钟K线（45–75分钟），不是3–5天。</div>
    <div class="how"><strong>最简单的确认方法：</strong>先从上到下浏览；如果整体都对，点击“浏览后全部认可”，再点“复制确认结果”，把下面生成的JSON直接粘贴回对话。如果只有少数不对，就只给那些样本点“还要改”或“剔除”。页面会在本机保存选择。</div>
    <section class="grid" id="grid">{cards}</section>
    <textarea id="export" readonly aria-label="确认结果JSON" placeholder="点击“复制确认结果”后，这里会出现可粘贴回Codex的JSON。"></textarea>
  </main>
  <dialog id="zoom"><button class="zoom-close" type="button" onclick="closeZoom()">关闭</button><img id="zoom-image" alt="放大查看"></dialog>
  <script>
    const SAMPLE_IDS = {js_ids};
    const STORAGE_KEY = "{PROTOCOL}:{source_hash}";
    const LABELS = {{pending:"未确认",accept:"已认可",adjust:"还要改",reject:"已剔除"}};
    let decisions = {{}};
    try {{ decisions = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}") || {{}}; }} catch (_) {{ decisions = {{}}; }}
    function save() {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions)); }}
    function setDecision(id, value) {{
      decisions[id] = value; save(); paintCard(id); updateStats(); applyFilter();
    }}
    function paintCard(id) {{
      const card = document.getElementById("card-" + id); const value = decisions[id] || "pending";
      card.dataset.decision = value; card.querySelector(".decision-chip").textContent = LABELS[value];
      card.querySelectorAll("[data-choice]").forEach(b => b.classList.toggle("active", b.dataset.choice === value));
    }}
    function counts() {{
      const c = {{pending:0,accept:0,adjust:0,reject:0}};
      SAMPLE_IDS.forEach(id => c[decisions[id] || "pending"]++); return c;
    }}
    function updateStats() {{ const c=counts(); document.getElementById("stats").textContent=`认可 ${{c.accept}} · 改 ${{c.adjust}} · 剔除 ${{c.reject}} · 未确认 ${{c.pending}}`; }}
    function setAllAccepted() {{
      if (!confirm("确认把当前61张全部标为“认可新框”？仍需复制结果并发回Codex才会进入下一步。")) return;
      SAMPLE_IDS.forEach(id => decisions[id]="accept"); save(); SAMPLE_IDS.forEach(paintCard); updateStats(); applyFilter();
    }}
    function clearAll() {{ if (!confirm("清空本页全部选择？")) return; decisions={{}}; save(); SAMPLE_IDS.forEach(paintCard); updateStats(); applyFilter(); document.getElementById("export").value=""; }}
    function applyFilter() {{
      const wanted=document.getElementById("filter").value;
      document.querySelectorAll(".sample-card").forEach(card => card.classList.toggle("hidden", wanted!=="all" && card.dataset.decision!==wanted));
    }}
    function payload() {{
      const c=counts(); return {{protocol:"{PROTOCOL}",source_sha256:"{source_hash}",total:61,counts:c,decisions:Object.fromEntries(SAMPLE_IDS.map(id=>[id,decisions[id]||"pending"]))}};
    }}
    async function copyResults() {{
      const text=JSON.stringify(payload(),null,2); const box=document.getElementById("export"); box.value=text; box.focus(); box.select();
      try {{ await navigator.clipboard.writeText(text); }} catch (_) {{ try {{ document.execCommand("copy"); }} catch (_) {{}} }}
      document.getElementById("stats").textContent="确认结果已生成；请粘贴回Codex";
    }}
    function openZoom(id, role) {{ const img=document.querySelector(`#card-${{CSS.escape(id)}} img[data-role="${{role}}"]`); document.getElementById("zoom-image").src=img.dataset.fullSrc; document.getElementById("zoom").showModal(); }}
    function closeZoom() {{ document.getElementById("zoom").close(); }}
    SAMPLE_IDS.forEach(paintCard); updateStats();
  </script>
</body>
</html>
"""


def run(
    source: Path,
    output_path: Path,
    future_output_dir: Path = DEFAULT_FUTURE_OUT,
) -> dict[str, Any]:
    rows = load_rows(source)
    training_hashes_before = {
        row["calibration_id"]: sha256_file(ROOT / str(row["proposal_image_path"]))
        for row in rows
    }
    future_rows = [render_future_review_image(row, future_output_dir) for row in rows]
    training_hashes_after = {
        row["calibration_id"]: sha256_file(ROOT / str(row["proposal_image_path"]))
        for row in rows
    }
    if training_hashes_before != training_hashes_after:
        raise RuntimeError("training proposal images changed during future review rendering")
    rendered = render_html(future_rows, source, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered)
    manifest_path = future_output_dir / "future_review_manifest.jsonl"
    with manifest_path.open("w") as handle:
        for row in future_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "protocol": PROTOCOL,
        "output": str(output_path),
        "source": str(source),
        "source_sha256": sha256_file(source),
        "rows": len(future_rows),
        "future_bars": FUTURE_BARS,
        "future_hours": FUTURE_BARS * 15 / 60,
        "future_manifest": str(manifest_path),
        "widths": dict(sorted(Counter(int(row["proposal_core_bars"]) for row in future_rows).items())),
        "posts": dict(sorted(Counter(int(row["post_bars"]) for row in future_rows).items())),
        "owner_decisions_preselected": 0,
        "training_mutation": False,
        "future_data_in_training_image": False,
        "future_data_in_training_label": False,
        "future_review_max_time": max(row["future_review_end_time"] for row in future_rows),
        "holdout_read": False,
        "quality_gates": {
            "exactly_61": len(future_rows) == 61,
            "future_exactly_48_bars": all(row["future_bars"] == FUTURE_BARS for row in future_rows),
            "future_review_files_exist": all((ROOT / row["future_review_image_path"]).is_file() for row in future_rows),
            "future_rows_preholdout": all(_utc(row["future_review_end_time"]) < HOLDOUT_START for row in future_rows),
            "holdout_rows_materialized_zero": all(row["future_review_read_audit"]["holdout_rows_materialized"] == 0 for row in future_rows),
            "training_images_byte_unchanged": training_hashes_before == training_hashes_after,
            "future_never_marked_for_training": all(not row["future_data_in_training_image"] and not row["future_data_in_training_label"] and not row["training_eligible"] for row in future_rows),
            "no_label_directory_in_future_output": not (future_output_dir / "labels").exists(),
        },
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(f"future review quality gate failed: {summary['quality_gates']}")
    (future_output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--future-out", type=Path, default=DEFAULT_FUTURE_OUT)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.out, args.future_out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
