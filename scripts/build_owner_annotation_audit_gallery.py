"""Build a local, read-only viewer of the 72 inspected diagnostic cards.

This presents explicit analyst suggestions separately from owner submissions.
It does not open source market data or connect to Label Studio.
"""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/offline_tasks/owner_annotation_audit_20260908'


def main():
    own = str(Path(__file__).relative_to(ROOT))
    if Path(__file__).read_bytes() != subprocess.check_output(['git','show','HEAD:'+own], cwd=ROOT):
        raise ValueError('Commit gallery builder first')
    rows = json.loads((OUT/'rows.json').read_text())
    notes = json.loads((OUT/'visual_findings.json').read_text())
    assert [x['task_id'] for x in rows] == [x['task_id'] for x in notes] == list(range(37277,37349))
    data = [{**n, 'status':r['status'],'direction':r['direction'],
             'owner_bars':r['geometry']['owner_bar_centers'],
             'future150_pct':round(r['future_close_change_pct_from_input_end']['150'],3)}
            for r,n in zip(rows,notes)]
    template = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>72 份标注逐图分析 · 只读</title><style>
body{font:16px/1.6 system-ui,sans-serif;margin:0;background:#f3f5f7;color:#17202b}main{max-width:1600px;margin:auto;padding:18px 24px}h1{font-size:24px;margin:0 0 8px}p{margin:8px 0}.muted{color:#5b6470}nav{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0}button,select,input{font:inherit;padding:7px 12px;border:1px solid #bdc7d0;border-radius:6px;background:white}button{cursor:pointer}section{background:white;border-radius:10px;padding:16px;margin:14px 0}.scroll{overflow:auto}img{display:block;width:100%;min-width:1080px}.zoom img{width:2560px;max-width:none}strong{color:#174886}#status{font-weight:600}a{color:#174886}label{white-space:nowrap}
</style><main><h1>72 份标注：主图、提交框与后续 150 根对照</h1>
<p>灰框＝原始弱标签；蓝框＝提交时保存的框。蓝框仍出现不代表用户保留了它，最终选择见下方状态。</p>
<p class="muted">左图下方为 K 线编号。右图 +1 从主图末根之后计数；形态后的 3–5 根确认通常已在左图中。全部结论是复核建议，尚未修改你的答案。</p>
<nav><button id="prev">上一张</button><select id="task" aria-label="选择任务"></select><button id="next">下一张</button><label>直接找任务 <input id="find" type="number" min="37277" max="37348" placeholder="37315" style="width:100px"></label><button id="go">查看</button><label><input id="zoom" type="checkbox">原尺寸放大</label><span id="count"></span></nav>
<section><div id="status"></div><p id="finding"></p><p><strong>边界建议：</strong><span id="span"></span></p><p><strong>150 根观察：</strong><span id="future"></span></p><p id="pct" class="muted"></p></section>
<div id="canvas" class="scroll"><img id="card" alt="编号 K 线、原框、提交框与后续150根对照"></div>
<p class="muted">这里是分析报告，不是新的标注项目；不会提交答案。左右方向键切换任务。几像素误差与圈错市场阶段不同；找不到清楚平台时不要为了保留正例硬凑框。</p>
</main><script>const data=__DATA__; const el=id=>document.getElementById(id);let index=0;
const states={owner_boxes:'提交：保留框',owner_no_target_with_inherited_proposal:'提交：无目标（图中框只是继承预框）',conflict_needs_review:'提交冲突：无目标 + 改动过的框，需核对最终意思'};
data.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.task_id+' · '+(r.direction==='LONG'?'多头':'空头');el('task').append(o)});
function show(i){index=Math.max(0,Math.min(data.length-1,i));const r=data[index];el('task').value=index;el('count').textContent=(index+1)+' / '+data.length;el('status').textContent=r.task_id+' · '+states[r.status];el('finding').textContent=r.finding;el('span').textContent=r.candidate_span||'不强行给框，先判断有没有标准目标';el('future').textContent=r.future_observation;el('pct').textContent='主图末根收盘 → 其后第150根收盘：'+(r.future150_pct>0?'+':'')+r.future150_pct+'%。仅描述价格路径，不是交易收益，不决定形态标签。';el('card').src='cards/'+r.task_id+'.png';el('prev').disabled=index===0;el('next').disabled=index===data.length-1;location.hash=String(r.task_id)}
el('prev').onclick=()=>show(index-1);el('next').onclick=()=>show(index+1);el('task').onchange=()=>show(Number(el('task').value));el('go').onclick=()=>{let i=data.findIndex(r=>r.task_id===Number(el('find').value));if(i>=0)show(i)};el('zoom').onchange=()=>el('canvas').classList.toggle('zoom',el('zoom').checked);document.addEventListener('keydown',e=>{if(['INPUT','SELECT'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')show(index-1);if(e.key==='ArrowRight')show(index+1)});let initial=data.findIndex(r=>String(r.task_id)===location.hash.slice(1));show(initial<0?0:initial);
</script></html>'''
    (OUT/'gallery.html').write_text(template.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    files=['rows.json','summary.json','visual_findings.json','gallery.html']+[f'cards/{r["task_id"]}.png' for r in rows]
    receipt={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip(),
             'files_sha256':{f:hashlib.sha256((OUT/f).read_bytes()).hexdigest() for f in files},
             'tasks':len(rows),'label_studio_writes':0,'training_eligible':False,'new_gold':False}
    (OUT/'gallery_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(OUT/'gallery.html')


if __name__ == '__main__':
    main()
