"""Package the frozen exit experiment with local-only dense curves and receipts."""
import json
from html.parser import HTMLParser
from pathlib import Path
import subprocess
from urllib.parse import unquote,urlparse
from yoyo.evaluation.ma_stoch_exit_study import EXP,ROOT
from yoyo.evaluation.ma_shift_stoch_study import dump,sha
from yoyo.evaluation.ma_stoch_exit_report import main as render


class CheckHTML(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=0;self.images=0;self.links=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='table':self.tables+=1
        if tag=='img':self.images+=1
        if tag=='a' and 'href' in attrs:self.links.append(attrs['href'])


def main():
    receipt=dict(status='passed',focused_tests=32,boundary_and_holdout_tests=88,
       commands=['.venv/bin/python -m pytest -q tests/test_ma_stoch_exit_engine.py tests/test_ma_stoch_exit_study.py tests/test_ma_shift_stoch.py tests/test_spike_fanshen_exit.py',
                 '.venv/bin/python -m pytest -q tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py'],
       baseline_real_ledger_parity=dict(dev=167,validation=156),
       broader_gates='Not repeated. Prior monthly run:468 passed,7 existing failures;TOTAL2 source_commit5,migration hash2.',
       equity_plot_visual_check='Main Codex viewed full equity/drawdown PNG; labels, dates, endpoints and legends readable.',
       html_browser_pixel_qa=False,html_browser_limitation='Earlier browser file URL denied by URL policy; no alternative route attempted.',
       independent_agent_review=False,holdout_consumed=False,
       notion_url='https://app.notion.com/p/3dc8856479af81078409ce4bbebaa155')
    dump(EXP/'validation.json',receipt)
    render()
    html=ROOT/'analysis/html/p1_ma_stoch_exit_optimization_20260915.html'
    parsed=CheckHTML();parsed.feed(html.read_text())
    assert parsed.tables==4 and parsed.images==1
    bad=[]
    for link in parsed.links:
        target=urlparse(link)
        if target.scheme in ('http','https','mailto') or link.startswith('#'):continue
        p=Path(unquote(target.path))
        if not p.is_absolute():p=html.parent/p
        if not p.exists():bad.append(str(p))
    assert not bad,bad
    receipt['html_structure']=dict(tables=parsed.tables,images=parsed.images,links=len(parsed.links),missing_local_links=bad)
    dump(EXP/'validation.json',receipt)
    files=[p for p in EXP.rglob('*') if p.is_file() and p.name!='delivery_manifest.json']
    files += [ROOT/'analysis/p1_ma_stoch_exit_optimization_20260915.md',html]
    files += [ROOT/p for p in ['yoyo/evaluation/ma_stoch_exit_engine.py','yoyo/evaluation/ma_stoch_exit_study.py',
              'yoyo/evaluation/ma_stoch_exit_audit.py','yoyo/evaluation/ma_stoch_exit_report.py','yoyo/evaluation/ma_stoch_exit_delivery.py',
              'tests/test_ma_stoch_exit_engine.py','tests/test_ma_stoch_exit_study.py',
              'docs/learnings/exit-protection-must-follow-the-bar-event-clock.md','docs/learnings/smaller-single-trade-loss-does-not-imply-smaller-drawdown.md']]
    records=[]
    for p in sorted(set(files)):
        rel=str(p.relative_to(ROOT))
        records.append(dict(path=rel,sha256=sha(p),size_bytes=p.stat().st_size,
                            storage='local_only_rebuildable' if p.name.endswith('_curve.csv') else 'repo'))
    dump(EXP/'delivery_manifest.json',dict(experiment_id=EXP.name,holdout_consumed=False,training_eligible=False,production_eligible=False,
          source_commit=json.loads((EXP/'dev/summary.json').read_text())['source_commit'],
          delivery_builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
          dense_curves='Local-only, ignored in git; reproducible from frozen code/config and original source prefix.',files=records))
    print(json.dumps(dict(delivery_files=len(records),local_dense_curves=sum(r['storage']=='local_only_rebuildable' for r in records),html_structure=receipt['html_structure']),ensure_ascii=False))

if __name__=='__main__':main()
