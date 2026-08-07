#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime, timezone
PROJECT=Path(__file__).resolve().parents[1]
st=PROJECT/"analysis/output/w20_shadow_status.json"
out=PROJECT/"analysis/output/w20_shadow_status.html"
if not st.exists():
    out.write_text("<html><body><p>no status yet — run pulse</p></body></html>")
    raise SystemExit(0)
s=json.loads(st.read_text())
b=s.get("book",{}); g=s.get("gate",{})
html=f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<meta http-equiv=refresh content=60>
<title>w20 shadow forward</title>
<style>
body{{font-family:system-ui;background:#0e1116;color:#e6edf3;margin:2rem;max-width:48rem}}
h1{{font-size:1.4rem}} .ok{{color:#3fb950}} .warn{{color:#d29922}}
table{{border-collapse:collapse;margin:1rem 0}} td,th{{border:1px solid #30363d;padding:.4rem .7rem}}
code{{background:#21262d;padding:.1rem .35rem;border-radius:4px}}
</style></head><body>
<h1>w20 midbox hardneg · Shadow 前向</h1>
<p class=warn>研究 shadow · <b>execution_eligible=false</b> · 不写 mainline forward_log · 不切 ACTIVE/owner_best</p>
<table>
<tr><th>closed / 100</th><td class=ok><b>{g.get('closed',0)}</b> / 100（剩余 {g.get('remaining',100)}）</td></tr>
<tr><th>open</th><td>{b.get('n_open')}</td></tr>
<tr><th>rows</th><td>{b.get('n_rows')}</td></tr>
<tr><th>mean net</th><td>{b.get('mean_net_bp')} bp</td></tr>
<tr><th>win rate</th><td>{b.get('win_rate')}</td></tr>
<tr><th>PF</th><td>{b.get('profit_factor')}</td></tr>
<tr><th>outcomes</th><td><code>{b.get('outcomes')}</code></td></tr>
<tr><th>conf / W</th><td>{s.get('conf')} / {s.get('window')}</td></tr>
<tr><th>weights</th><td><code>{s.get('weights','')}</code></td></tr>
<tr><th>log</th><td><code>{s.get('log_path','')}</code></td></tr>
<tr><th>updated</th><td>{s.get('updated_at')}</td></tr>
</table>
<p>刷新间隔 60s · 本地生成 {datetime.now(timezone.utc).isoformat()}</p>
<p>脉冲：<code>bash scripts/forward_pulse_w20_shadow.sh</code></p>
</body></html>"""
out.write_text(html)
print("wrote", out)
