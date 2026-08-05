"""Build an offline gallery of the owner's original ⭐ benchmark images.

The source of truth is ``data/benchmark_exemplars.json``.  The script copies
the exact archived chart PNGs without cropping or re-rendering, then creates a
separate preview with the owner's normalized Label Studio boxes drawn on top.
Missing historical source files are reported honestly and are never replaced
with a processed training image.
"""
from __future__ import annotations

import argparse
import json
import shutil
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw


PROJECT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT / "data" / "benchmark_exemplars.json"
DEFAULT_OUT = PROJECT / "analysis" / "output" / "star_benchmark_originals"
DEFAULT_IMAGE_ROOTS = (
    PROJECT / "datasets" / "dense_owner_v14_pad200" / "images",
    PROJECT / "datasets" / "owner_eval_frozen" / "images",
    PROJECT / "analysis" / "output" / "v9_control_gold_pack" / "images",
)


def image_index(roots: list[Path]) -> dict[str, Path]:
    """Return the first readable exact-stem image from each priority root."""
    out: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            if not path.is_file() or path.stem in out:
                continue
            try:
                with Image.open(path) as image:
                    image.verify()
            except Exception:  # noqa: BLE001 - corrupt archives are skipped
                continue
            out[path.stem] = path
    return out


def draw_preview(source: Path, destination: Path, boxes: list[dict]) -> None:
    """Draw only the owner's original normalized boxes on the full source image."""
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    line_width = max(3, round(width / 320))
    for box in boxes:
        cx = float(box["cx"]) * width
        cy = float(box["cy"]) * height
        bw = float(box["w"]) * width
        bh = float(box["h"]) * height
        xy = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
        draw.rectangle(xy, outline=(255, 196, 0), width=line_width)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="JPEG", quality=94, optimize=True)


def gallery_html(items: list[dict], total_registry: int, missing: list[str]) -> str:
    """Return a fetch-free HTML document that also works from ``file://``."""
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    missing_text = escape("、".join(missing))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>⭐标杆金标 · 原始手标图库</title>
<style>
:root {{ color-scheme: dark; --bg:#0c1117; --panel:#151d27; --line:#293646;
  --text:#edf2f7; --muted:#91a0b2; --gold:#ffc400; --accent:#38bdf8; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,
  BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif; }}
header {{ position:sticky; top:0; z-index:10; display:flex; flex-wrap:wrap; gap:10px 16px;
  align-items:center; padding:11px 16px; background:rgba(21,29,39,.96);
  border-bottom:1px solid var(--line); backdrop-filter:blur(12px); }}
h1 {{ margin:0; font-size:16px; font-weight:650; }}
.count {{ color:var(--muted); font-size:13px; }} .count b {{ color:var(--gold); }}
.tools {{ margin-left:auto; display:flex; gap:8px; align-items:center; }}
input {{ min-width:210px; padding:8px 10px; color:var(--text); background:#0b121a;
  border:1px solid var(--line); border-radius:7px; }}
button,.raw-link {{ padding:8px 11px; color:var(--text); background:#202c3a;
  border:1px solid var(--line); border-radius:7px; cursor:pointer; text-decoration:none; }}
button:hover,.raw-link:hover {{ border-color:var(--accent); }}
main {{ width:min(1220px,100%); margin:0 auto; padding:16px; }}
.viewer {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
  overflow:hidden; }}
.viewer-top {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 16px;
  padding:10px 12px; border-bottom:1px solid var(--line); }}
.viewer-top code {{ color:var(--gold); }} .meta {{ color:var(--muted); font-size:12px; }}
.viewer-top .raw-link {{ margin-left:auto; }}
.stage {{ background:#000; display:flex; align-items:center; justify-content:center;
  min-height:360px; }}
.stage img {{ display:block; max-width:100%; max-height:72vh; object-fit:contain; }}
.nav {{ display:flex; justify-content:center; gap:10px; padding:10px; }}
.missing {{ margin:12px 0; color:var(--muted); font-size:12px; }}
.missing details {{ cursor:pointer; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr));
  gap:10px; margin-top:14px; }}
.tile {{ padding:0; overflow:hidden; text-align:left; background:var(--panel);
  border:1px solid var(--line); border-radius:8px; }}
.tile.active {{ border-color:var(--gold); box-shadow:0 0 0 1px var(--gold); }}
.tile img {{ width:100%; aspect-ratio:1280/742; display:block; object-fit:cover; background:#000; }}
.tile span {{ display:block; padding:7px 8px; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
.empty {{ color:var(--muted); padding:36px; text-align:center; }}
@media(max-width:700px) {{ .tools {{ width:100%; margin-left:0; }} input {{ flex:1; min-width:0; }}
  .stage {{ min-height:220px; }} .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
</head>
<body>
<header>
  <h1>⭐标杆金标 · 原始手标图库</h1>
  <div class="count">原始图 <b>{len(items)}</b> / 注册标杆 {total_registry} · 仅显示 ⭐ 子集</div>
  <div class="tools"><input id="search" type="search" placeholder="搜索币种或文件名"></div>
</header>
<main>
  <section class="viewer" id="viewer">
    <div class="viewer-top"><code id="stem"></code><span class="meta" id="meta"></span>
      <a class="raw-link" id="raw" href="#" target="_blank">打开未叠框原图</a></div>
    <div class="stage"><img id="main-image" alt="当前 ⭐标杆原图"></div>
    <div class="nav"><button id="prev" type="button">← 上一张</button><button id="next" type="button">下一张 →</button></div>
  </section>
  <div class="missing"><details><summary>{len(missing)} 张历史原图目前不在本机（未用训练裁剪图冒充）</summary>{missing_text}</details></div>
  <section class="grid" id="grid" aria-label="⭐标杆缩略图"></section>
</main>
<script>
const ITEMS={payload};
let filtered=ITEMS.slice(), current=0;
const $=id=>document.getElementById(id);
function esc(s){{return String(s).replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]));}}
function select(i){{
  if(!filtered.length)return;
  current=(i+filtered.length)%filtered.length;
  const item=filtered[current];
  $('stem').textContent=item.stem;
  $('meta').textContent=`${{current+1}} / ${{filtered.length}} · ${{item.boxes.length}} 个手画框 · ${{item.source_export}}`;
  $('main-image').src=item.preview;
  $('main-image').alt=item.stem+' ⭐标杆原图（黄色框为手标框）';
  $('raw').href=item.raw;
  document.querySelectorAll('.tile').forEach((el,j)=>el.classList.toggle('active',j===current));
}}
function renderGrid(){{
  const grid=$('grid');
  if(!filtered.length){{grid.innerHTML='<div class="empty">没有匹配图片</div>';return;}}
  grid.innerHTML=filtered.map((item,i)=>`<button type="button" class="tile${{i===current?' active':''}}" data-i="${{i}}"><img loading="lazy" src="${{esc(item.preview)}}" alt="${{esc(item.stem)}}"><span>${{esc(item.stem)}}</span></button>`).join('');
  grid.querySelectorAll('.tile').forEach(el=>el.onclick=()=>{{select(Number(el.dataset.i));scrollTo({{top:0,behavior:'smooth'}});}});
}}
$('prev').onclick=()=>select(current-1); $('next').onclick=()=>select(current+1);
$('search').oninput=e=>{{
  const q=e.target.value.trim().toLowerCase();
  filtered=ITEMS.filter(x=>x.stem.toLowerCase().includes(q)||x.source_export.toLowerCase().includes(q));
  current=0; renderGrid(); if(filtered.length)select(0);
}};
document.addEventListener('keydown',e=>{{if(e.key==='ArrowLeft')select(current-1);if(e.key==='ArrowRight')select(current+1);}});
renderGrid(); select(0);
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--extra-root", type=Path, action="append", default=[])
    args = parser.parse_args()

    registry_doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    exemplars = registry_doc["exemplars"]
    roots = [*args.extra_root, *DEFAULT_IMAGE_ROOTS]
    indexed = image_index(roots)

    raw_dir = args.out_dir / "raw"
    preview_dir = args.out_dir / "previews"
    raw_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict] = []
    missing: list[str] = []
    for stem, info in exemplars.items():
        source = indexed.get(stem)
        if source is None:
            missing.append(stem)
            continue
        raw_path = raw_dir / f"{stem}{source.suffix.lower()}"
        preview_path = preview_dir / f"{stem}.jpg"
        shutil.copy2(source, raw_path)
        draw_preview(source, preview_path, info.get("boxes", []))
        items.append(
            {
                "stem": stem,
                "boxes": info.get("boxes", []),
                "marked_at": info.get("marked_at", ""),
                "source_export": info.get("source_export", ""),
                "raw": raw_path.relative_to(args.out_dir).as_posix(),
                "preview": preview_path.relative_to(args.out_dir).as_posix(),
                "archive_source": str(source),
            }
        )

    items.sort(key=lambda item: item["stem"])
    missing.sort()
    manifest = {
        "description": registry_doc.get("description", ""),
        "registry_count": len(exemplars),
        "available_originals": len(items),
        "missing_originals": missing,
        "items": items,
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out_dir / "missing_originals.txt").write_text(
        "\n".join(missing) + ("\n" if missing else ""), encoding="utf-8"
    )
    (args.out_dir / "index.html").write_text(
        gallery_html(items, len(exemplars), missing), encoding="utf-8"
    )
    print(
        f"⭐ registry={len(exemplars)} available_originals={len(items)} "
        f"missing={len(missing)} out={args.out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
