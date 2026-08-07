#!/usr/bin/env python3
"""Wait until w20 hardneg@0.30 5d gallery finishes, then push summary + samples to TG.

Owner request 2026-08-07: after hardneg@0.30 rescan completes, notify Telegram.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
_YOYO = Path.home() / "yoyo-trading"
if _YOYO.is_dir():
    sys.path.insert(0, str(_YOYO))
os.environ.setdefault("YOYO_DATA_ROOT", str(PROJECT))

from src.notify import send, send_photo  # noqa: E402

OUT = PROJECT / "analysis" / "output" / "w20_midbox_5d_gallery_hardneg030"
LOG = PROJECT / "logs" / "w20_hn030_gallery.log"
POLL_SEC = 120
MAX_PHOTOS = 8  # top conf samples, avoid TG spam


def scan_running() -> bool:
    try:
        out = subprocess.check_output(
            ["ps", "-ax", "-o", "command="], text=True, stderr=subprocess.DEVNULL
        )
    except Exception:
        return False
    for line in out.splitlines():
        if "scan_w20_midbox_5d_gallery.py" in line and "hardneg030" in line:
            return True
    return False


def progress() -> str:
    if not LOG.exists():
        return "no log"
    text = LOG.read_text(errors="replace")
    ms = list(re.finditer(r"\[(\d+)/(\d+)\].*total_cards=(\d+).*elapsed=(\d+)", text))
    if not ms:
        return f"log_lines={len(text.splitlines())}"
    i, n, c, e = map(int, ms[-1].groups())
    return f"{i}/{n} cards={c} elapsed={e//60}m"


def finished() -> bool:
    man = OUT / "manifest.json"
    html = OUT / "index.html"
    if man.exists() and html.exists() and not scan_running():
        return True
    # also accept: log has final summary and process gone
    if LOG.exists() and not scan_running():
        t = LOG.read_text(errors="replace")
        if "gallery →" in t or '"n_cards"' in t:
            return True
    return False


def build_summary() -> tuple[str, list[Path]]:
    imgs = sorted((OUT / "images").glob("*.png"))
    man = {}
    if (OUT / "manifest.json").exists():
        man = json.loads((OUT / "manifest.json").read_text())
    meta = man.get("meta", man) if isinstance(man, dict) else {}
    cards = man.get("cards", [])
    # parse conf from filenames if no cards
    pat = re.compile(r"^(?P<sym>.+)_(?P<d>\d{8})_(?P<t>\d{4})_c(?P<c>[\d.]+)\.png$")
    parsed = []
    for p in imgs:
        m = pat.match(p.name)
        if not m:
            continue
        d, t = m.group("d"), m.group("t")
        st = f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:]}:00"
        parsed.append(
            {
                "path": p,
                "symbol": m.group("sym"),
                "conf": float(m.group("c")),
                "signal_time": st,
            }
        )
    parsed.sort(key=lambda x: -x["conf"])
    n = len(parsed)
    confs = [x["conf"] for x in parsed]
    p50 = confs[len(confs) // 2] if confs else 0
    top = parsed[:MAX_PHOTOS]
    n_sym = len({x["symbol"] for x in parsed})
    lines = [
        "<b>w20 hardneg @0.30 · 5d tip 画廊完成</b>",
        "",
        f"模型: <code>hardneg_c1 best.pt</code>",
        f"conf 门槛: <b>0.30</b> · 窗 W=24 tip",
        f"数据: 最近 5 天 (至 ~2026-08-05 UTC)",
        f"信号图: <b>{n}</b> 张 · 涉及币种 <b>{n_sym}</b>",
    ]
    if confs:
        lines.append(
            f"conf 分布: min {min(confs):.2f} · p50 {p50:.2f} · max {max(confs):.2f}"
        )
    lines.append("")
    lines.append("对比: 旧 cold@0.15 未扫完已 1000+ 张；本档明显更稀。")
    lines.append("")
    lines.append("路径:")
    lines.append(f"<code>{OUT / 'index.html'}</code>")
    if top:
        lines.append("")
        lines.append("Top conf 样本（附图）:")
        for x in top[:5]:
            lines.append(
                f"· {x['symbol']} conf={x['conf']:.3f} @ {x['signal_time']} UTC"
            )
    if meta:
        lines.append("")
        lines.append(f"manifest n_cards={meta.get('n_cards', n)}")
    text = "\n".join(lines)
    return text, [x["path"] for x in top]


def main() -> int:
    print(f"watching {OUT}", flush=True)
    while not finished():
        print(f"  wait… {progress()} running={scan_running()}", flush=True)
        time.sleep(POLL_SEC)
    print("done — sending TG", flush=True)
    text, photos = build_summary()
    ok = send(text)
    print(f"send text ok={ok}", flush=True)
    sent = 0
    for p in photos:
        if send_photo(p, caption=p.name[:100]):
            sent += 1
        time.sleep(0.4)
    print(f"send photos {sent}/{len(photos)}", flush=True)
    # final short ack
    send(f"画廊 TG 推送完毕：文案 ok={ok} · 附图 {sent}/{len(photos)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
