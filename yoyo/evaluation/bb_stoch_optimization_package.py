"""Hash final optimization artifacts; never open a market-data source."""
from __future__ import annotations

import subprocess
from pathlib import Path

from yoyo.evaluation.bb_stoch_optimization import EXP, ROOT, BUILDERS, require_committed
from yoyo.evaluation.eth_bb_stoch_study import save, sha


def main():
    head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    require_committed(Path(__file__),head)
    paths = set(ROOT/name for name in BUILDERS)
    paths.update(p for p in EXP.iterdir() if p.is_file() and p.name!='delivery_manifest.json')
    paths.update(ROOT/name for name in [
        'yoyo/evaluation/bb_stoch_optimization_package.py',
        'tests/evaluation/test_bb_stoch_optimization.py',
        'tests/evaluation/test_bb_stoch_parameter_replay.py',
        'docs/learnings/stop-optimization-needs-a-fixed-cash-denominator.md',
        'analysis/p1_eth_bb_stoch_optimization_20260916.md',
        'analysis/html/p1_eth_bb_stoch_optimization_20260916.html',
        'analysis/html/eth_bb_stoch_optimization_20260916_equity.png'])
    files = [dict(path=str(p.relative_to(ROOT)),sha256=sha(p),size_bytes=p.stat().st_size)
             for p in sorted(paths)]
    save(EXP/'delivery_manifest.json',dict(experiment_id=EXP.name,packaging_commit=head,
        holdout_consumed=False,training_eligible=False,production_eligible=False,files=files))
    print(f'Packaged {len(files)} files')


if __name__=='__main__':
    main()
