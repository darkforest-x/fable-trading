"""Merge reviewed N5 evidence into the original Stitch report without replay.

Owner2026-10-24 explicitly authorized reuse of the first Stitch template.
Requires the existing baseline and monday_report_update snapshot, plus the
completed monday_alt_n5_report_review output. No source trades are mutated.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'reports/monday-1618-cases-20261023'
ART = ROOT / 'artifacts/monday-alt-n5-report-20261024'


def main():
    path = APP / 'src/data.json'
    data = json.loads(path.read_text())
    assert data['id'] == 'report:b2b980f2-70d2-4866-8399-0d8c6aa94a7e'
    assert len(data['queries']['trades']['rows']) == 79430
    assert len(data['queries']['samples']['rows']) == 60
    assert len(data['queries']['latest-parameters']['rows']) == 243
    reviewed = json.loads((ART / 'reviewed-queries.json').read_text())
    assert len(reviewed['alt-n5-ledger']['rows']) == 9065
    assert sum(r['closed'] for r in reviewed['alt-n5-ledger']['rows']) == 9041
    data['queries'].update(reviewed)
    for query_id, metadata in json.loads((ART / 'report-source-metadata.json').read_text()).items():
        data['queries'][query_id].update(metadata)
    # The available evidence is fully rendered; the additional pullback test is pending owner rules.
    data['buildStatus'] = 'paused'
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    print('Preserved baseline and app identity; merged 8 N5 queries. Pullback test remains pending.')


if __name__ == '__main__':
    main()
