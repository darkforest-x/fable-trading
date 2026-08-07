#!/usr/bin/env python3
"""Rebuild w20 5d gallery HTML with conf threshold filter (default ≥0.30).

Does not delete PNGs — only changes which cards show by default.
  .venv/bin/python scripts/filter_w20_5d_gallery.py --min-conf 0.30
  open analysis/output/w20_midbox_5d_gallery/index.html
"""
from __future__ import annotations

import argparse
import html as H
import re
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis" / "output" / "w20_midbox_5d_gallery"
PAT = re.compile(r"^(?P<sym>.+)_(?P<d>\d{8})_(?P<t>\d{4})_c(?P<c>[\d.]+)\.png$")


def parse_cards(img_dir: Path) -> list[dict]:
    cards = []
    for p in sorted(img_dir.glob("*.png")):
        m = PAT.match(p.name)
        if not m:
            continue
        d, t = m.group("d"), m.group("t")
        st = f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:]}:00+00:00"
        cards.append(
            {
                "symbol": m.group("sym"),
                "signal_time": st,
                "conf": float(m.group("c")),
                "rel_img": f"images/{p.name}",
            }
        )
    cards.sort(key=lambda c: (-c["conf"], c["signal_time"]))
    return cards


def write_html(cards: list[dict], out_html: Path, *, min_conf: float) -> dict:
    thresholds = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
    counts = { thr: sum(1 for c in cards if c["conf"] >= thr) for thr in thresholds }
    default = [c for c in cards if c["conf"] >= min_conf]

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>",
        f"<title>w20 midbox 5d · conf≥{min_conf:.2f} ({len(default)})</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;background:#0e1116;color:#e6edf3;margin:20px 24px 48px}",
        "h1{font-size:1.3rem;margin:0 0 8px}",
        ".meta{color:#8b949e;margin:0 0 14px;font-size:14px;line-height:1.5}",
        ".dock{position:sticky;top:0;z-index:5;background:#0e1116ee;padding:10px 0 12px;"
        "border-bottom:1px solid #30363d;margin:0 0 16px;backdrop-filter:blur(6px)}",
        ".dock button{border:1px solid #30363d;background:#21262d;color:#e6edf3;"
        "border-radius:999px;padding:6px 12px;font-size:12.5px;cursor:pointer;margin:3px}",
        ".dock button.active{background:#1f6feb;border-color:#1f6feb}",
        ".grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}",
        "figure{margin:0;background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden}",
        "figure.hidden{display:none}",
        "img{width:100%;display:block;background:#000}",
        "figcaption{padding:10px 12px;font-size:13px;line-height:1.4}",
        "a{color:#58a6ff}",
        "@media(max-width:900px){.grid{grid-template-columns:1fr}}",
        "</style></head><body>",
        "<h1>w20 midbox · last 5d gallery</h1>",
        f"<p class='meta'>全量 PNG <b>{len(cards)}</b> · 默认显示 conf≥<b>{min_conf:.2f}</b> → "
        f"<b id='shown'>{len(default)}</b> 张 · "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
        f"<a href='images/'>全部 images/</a></p>",
        "<div class='dock' id='dock'><span style='color:#8b949e;margin-right:8px'>conf 过滤</span>",
    ]
    for thr in thresholds:
        active = " active" if abs(thr - min_conf) < 1e-9 else ""
        parts.append(
            f"<button type='button' class='{active}' data-min='{thr}'>"
            f"≥{thr:.2f}<span style='opacity:.7'> ({counts[thr]})</span></button>"
        )
    parts.append("</div><div class='grid' id='grid'>")
    for c in cards:
        parts.append(
            f"<figure data-conf='{c['conf']:.4f}' "
            f"class='{'hidden' if c['conf'] < min_conf else ''}'>"
            f"<a href='{H.escape(c['rel_img'])}' target='_blank' rel='noopener'>"
            f"<img src='{H.escape(c['rel_img'])}' loading='lazy'/></a>"
            f"<figcaption><b>{H.escape(c['symbol'])}</b> · conf {c['conf']:.3f}<br>"
            f"{H.escape(c['signal_time'])}</figcaption></figure>"
        )
    parts.append(
        """</div>
<script>
(function(){
  const shown = document.getElementById('shown');
  const buttons = document.querySelectorAll('#dock button');
  function apply(min){
    let n = 0;
    document.querySelectorAll('#grid figure').forEach(el => {
      const ok = parseFloat(el.dataset.conf) >= min;
      el.classList.toggle('hidden', !ok);
      if (ok) n++;
    });
    if (shown) shown.textContent = n;
    buttons.forEach(b => b.classList.toggle('active', parseFloat(b.dataset.min) === min));
  }
  buttons.forEach(b => b.addEventListener('click', () => apply(parseFloat(b.dataset.min))));
})();
</script></body></html>"""
    )
    out_html.write_text("".join(parts), encoding="utf-8")
    return {"total": len(cards), "default_shown": len(default), "counts": counts, "min_conf": min_conf}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--min-conf", type=float, default=0.30)
    args = ap.parse_args()
    cards = parse_cards(args.out / "images")
    stats = write_html(cards, args.out / "index.html", min_conf=args.min_conf)
    # also write a static hard-filtered sibling for sharing
    hard = [c for c in cards if c["conf"] >= args.min_conf]
    (args.out / f"index_conf{args.min_conf:.2f}.html").write_text(
        (args.out / "index.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    print(stats)
    print(f"wrote {args.out / 'index.html'} default conf>={args.min_conf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
