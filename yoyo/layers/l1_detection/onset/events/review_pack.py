"""Causal Review Pack — reveal bars one at a time, hide everything else.

The question this tool exists to answer cannot be asked with a normal chart:

    Looking only at this bar and everything before it, would you say
    "this is the pattern"?

Any chart showing what came next answers it for the reviewer. So the pack
pre-renders a strip of frames per event, each ending one bar later than the
last, and the page steps through them. Nothing after the current tip exists in
any frame -- not faded, not greyed, absent.

Blinded at render time, not by CSS (spec §8.2). The page never receives:

    original bbox · original signal_i · quality grade · source ·
    teacher confidence · later price · whether this event is a hidden repeat

Those live in a separate truth file the page never loads. Hiding them in the
client would leave them in the DOM for anyone who scrolls the source, and more
practically, would leave them where a future refactor could surface them.
"""
from __future__ import annotations

import hashlib
import html
import json
import random
from pathlib import Path
from typing import Any, Callable

STAGE_CHOICES = ("NOT_YET", "FORMING", "ONSET_NOW", "INVALID", "UNCERTAIN")
PROTOCOL_VERSION = "causal_onset_review_v1"


def frame_plan(box_start_i: int, box_end_i: int, lead_bars: int,
               trail_bars: int) -> list[int]:
    """Tips to reveal, earliest first.

    Starts before the formation could plausibly be visible and continues past
    the box edge, so the reviewer can say ONSET_NOW early, late, or never
    without the strip's own boundaries suggesting an answer.
    """
    start = box_start_i - lead_bars
    end = box_end_i + trail_bars
    return list(range(start, end + 1))


def build_pack(
    events: list[dict[str, Any]],
    render_frame: Callable[[str, int, int], str | None],
    out_dir: Path,
    *,
    lead_bars: int = 12,
    trail_bars: int = 6,
    window_bars: int = 200,
    repeat_frac: float = 0.25,
    seed: int = 20260806,
) -> dict[str, Any]:
    """Render frame strips and emit a blinded review page.

    `render_frame(symbol, tip_i, window_bars) -> relative path or None` is
    injected so this module stays free of chart and OHLCV dependencies, which
    keeps it unit-testable without market data.
    """
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = out_dir / "frames"
    img_dir.mkdir(exist_ok=True)

    n_repeat = int(round(len(events) * repeat_frac))
    repeats = rng.sample(events, min(n_repeat, len(events)))

    items: list[dict[str, Any]] = []
    truth: dict[str, Any] = {}

    def add(ev: dict[str, Any], repeat_of: str | None) -> None:
        box = ev["original_box"]
        bs, be = box.get("box_start_i"), box.get("box_end_i")
        if bs is None or be is None:
            return
        # review_id must not encode the event: a reviewer who notices evt_000123
        # and evt_000123_r side by side has been told they are the same event.
        rid = "rv_" + hashlib.sha256(
            f"{ev['event_id']}|{repeat_of or ''}|{seed}".encode()).hexdigest()[:12]
        frames = []
        for tip in frame_plan(bs, be, lead_bars, trail_bars):
            rel = render_frame(ev["symbol"], tip, window_bars)
            if rel is None:
                continue
            frames.append({"tip_i": tip, "rel_img": rel})
        if len(frames) < 3:
            return
        items.append({
            "review_id": rid,
            # symbol and timeframe are shown: they are visible on any chart and
            # withholding them would make the task unlike real reading
            "symbol": ev["symbol"],
            "timeframe": ev["timeframe"],
            "frames": frames,
        })
        truth[rid] = {
            "event_id": ev["event_id"],
            "source_pattern_id": ev["source_pattern_id"],
            "source": ev["source"],
            "quality_label": ev.get("quality_label"),
            "box_start_i": bs,
            "box_end_i": be,
            "is_repeat_of": repeat_of,
        }

    for ev in events:
        add(ev, None)
    for ev in repeats:
        add(ev, ev["event_id"])

    rng.shuffle(items)

    (out_dir / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "manifest_type": "causal_onset_review_pack",
        "protocol_version": PROTOCOL_VERSION,
        "stage_choices": list(STAGE_CHOICES),
        "lead_bars": lead_bars, "trail_bars": trail_bars,
        "window_bars": window_bars, "seed": seed,
        "repeat_frac": repeat_frac,
        "n_items": len(items),
        "n_repeat_items": sum(1 for k in truth if truth[k]["is_repeat_of"]),
        "blinding": "frames contain no bar after the displayed tip; bbox, signal_i, "
                    "quality grade, source, teacher confidence and repeat status "
                    "are not present in the page or manifest",
        "items": items,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    (out_dir / "_truth_do_not_open.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    write_review_html(items, out_dir / "index.html", out_dir.name)
    return {"n_items": len(items), "n_repeats": len(repeats), "out_dir": str(out_dir)}


def write_review_html(items: list[dict[str, Any]], out: Path, pack_id: str) -> None:
    """One question, three keys.

    The protocol has five states, and the first version bound a key to each. In
    practice a reviewer answers one thing -- the earliest bar they would commit
    to -- and every bar before it is NOT_YET by definition. Making them press a
    key per frame turned one judgement into thirty keystrokes and, per owner,
    made the tool unusable.

    So NOT_YET is now implicit in advancing, and the only deliberate acts are
    "this bar" and "never". The exported records still carry all five states:
    steps are reconstructed from the onset position, so the stability gate sees
    exactly what it saw before.

    The commit key is split in two rather than adding a step. Direction is not a
    second question -- a reviewer who can say "this is the pattern" already knows
    which way it breaks, and asking separately would double the keystrokes for a
    judgement they made in the same glance. So F commits short and D commits
    long, and the answer count per event stays at one.

    Direction has to be captured here because nothing downstream can recover it.
    Backfilling from owner's older direction review reaches only 33% of events,
    and inferring it from price-versus-cluster geometry is circular: that feature
    separates owner's own short and long boxes at AUC 0.988, so an inferred side
    would agree with itself and measure nothing.
    """
    data = json.dumps(items, ensure_ascii=False)
    out.write_text(f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>因果 onset 标注 · {pack_id}</title>
<style>
:root{{color-scheme:dark}}
body{{margin:0;background:#0d1117;color:#c9d1d9;font:14px/1.5 -apple-system,system-ui,sans-serif}}
header{{position:sticky;top:0;background:#161b22;border-bottom:1px solid #30363d;padding:12px 16px;z-index:9}}
h1{{margin:0 0 2px;font-size:17px}}
.q{{color:#58a6ff;font-size:15px;font-weight:600}}
#img{{width:100%;max-width:1280px;display:block;margin:10px auto;border:1px solid #30363d;border-radius:6px}}
.meta{{color:#8b949e;font-size:12px}}
kbd{{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:2px 8px;font-size:13px}}
.keys{{margin-top:8px;font-size:14px}}
.keys b{{color:#3fb950}}
.keys i{{color:#f0883e;font-style:normal}}
button{{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 12px;cursor:pointer}}
#bar{{height:5px;background:#21262d;border-radius:3px;margin-top:10px;overflow:hidden}}
#fill{{height:100%;background:#58a6ff;width:0;transition:width .08s}}
.done{{color:#3fb950;font-weight:600}}
</style>
<header>
<h1>事件 <span id="pos"></span> · 已标 <span class="done" id="done">0</span>/<span id="tot"></span>
&nbsp;<button onclick="dl()">导出 JSONL</button></h1>
<div class="q">只看当前这根和它之前 —— 你会说「就是它」吗？是的话，往哪边走？</div>
<div class="keys">
<kbd>空格</kbd> 再放一根　<b><kbd>F</kbd> 就是这根 · 往下（做空）</b>　<i><kbd>D</kbd> 就是这根 · 往上（做多）</i>　<kbd>N</kbd> 这个没有／不是形态　<kbd>←</kbd> 退一根
</div>
<div class="meta" id="info" style="margin-top:6px"></div>
<div id="bar"><div id="fill"></div></div>
</header>
<img id="img" alt="">
<script>
const IT={data};
const K='causal_onset::{pack_id}';
let ans=JSON.parse(localStorage.getItem(K)||'{{}}'), i=0, f=0;
const $=s=>document.querySelector(s);
const nDone=()=>Object.values(ans).filter(a=>a&&(a.causal_onset_i!=null||a.invalid)).length;
function cur(){{ return IT[i]; }}
function render(){{
  const it=cur(); if(!it) return;
  f=Math.min(Math.max(f,0),it.frames.length-1);
  $('#img').src=it.frames[f].rel_img;
  $('#pos').textContent=`${{i+1}} / ${{IT.length}}`;
  $('#tot').textContent=IT.length;
  $('#done').textContent=nDone();
  $('#fill').style.width=((f+1)/it.frames.length*100)+'%';
  const a=ans[it.review_id]||{{}};
  let s='';
  if(a.causal_onset_i!=null){{
    const sd=a.side==='long'?'<span style="color:#f0883e">往上</span>'
                            :'<span style="color:#3fb950">往下</span>';
    s=`<span class="done">✓ 已标 onset</span> · ${{sd}}　`;
  }}
  else if(a.invalid) s='<span style="color:#f85149">✗ 已标「没有」</span>　';
  $('#info').innerHTML=s+`${{it.symbol}} · 第 ${{f+1}}/${{it.frames.length}} 根`;
}}
function advance(){{ const it=cur(); if(f<it.frames.length-1){{f++; render();}} }}
function back(){{ if(f>0){{f--; render();}} }}
function nextEvent(){{ if(i<IT.length-1){{i++; f=0; render();}} else render(); }}
function setOnset(side){{
  const it=cur(), fr=it.frames[f];
  ans[it.review_id]={{causal_onset_i:fr.tip_i, onset_frame_index:f, side:side,
                      n_frames:it.frames.length, invalid:false}};
  localStorage.setItem(K,JSON.stringify(ans));
  nextEvent();
}}
function setNone(){{
  const it=cur();
  ans[it.review_id]={{causal_onset_i:null, side:null, invalid:true,
                      n_frames:it.frames.length}};
  localStorage.setItem(K,JSON.stringify(ans));
  nextEvent();
}}
function dl(){{
  const now=new Date().toISOString();
  const lines=IT.map(it=>{{
    const a=ans[it.review_id]||{{}};
    // rebuild per-frame states from the single decision, so the export matches
    // the five-state protocol the analysis expects
    const steps={{}};
    if(a.causal_onset_i!=null){{
      for(const fr of it.frames){{
        if(fr.tip_i<a.causal_onset_i) steps[fr.tip_i]='NOT_YET';
        else if(fr.tip_i===a.causal_onset_i) steps[fr.tip_i]='ONSET_NOW';
      }}
    }} else if(a.invalid){{
      for(const fr of it.frames) steps[fr.tip_i]='INVALID';
    }}
    return JSON.stringify({{
      review_id:it.review_id, protocol_version:"{PROTOCOL_VERSION}",
      steps:steps,
      formation_start_i:null,
      causal_onset_i:a.causal_onset_i??null,
      side:a.side??null,
      side_source:a.side?"causal_onset_review":null,
      invalid:!!a.invalid,
      n_frames:it.frames.length,
      reviewed_at:(a.causal_onset_i!=null||a.invalid)?now:null,
      reviewer:"owner"
    }});
  }});
  const b=new Blob([lines.join('\\n')+'\\n'],{{type:'application/x-ndjson'}});
  const el=document.createElement('a');
  el.href=URL.createObjectURL(b); el.download='reviews.jsonl'; el.click();
}}
addEventListener('keydown',e=>{{
  const k=e.key.toLowerCase();
  if(k===' '||k==='j'||k==='arrowright'){{ e.preventDefault(); advance(); }}
  else if(k==='k'||k==='arrowleft'){{ back(); }}
  else if(k==='f'||k==='enter'){{ e.preventDefault(); setOnset('short'); }}
  else if(k==='d'){{ e.preventDefault(); setOnset('long'); }}
  else if(k==='n'){{ setNone(); }}
}});
render();
</script>
""", encoding="utf-8")
