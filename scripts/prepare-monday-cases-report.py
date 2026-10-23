#!/usr/bin/env python3
"""Refresh only reviewed data and generated candle assets in the authored report.

Reads frozen research results and preserves report identity; no backtest replay,
network access, source mutation or protected runtime modification is performed.
Run the canonical Data build/export commands after this preparation.
"""
import json
import shutil
from pathlib import Path

root=Path(__file__).resolve().parents[1]
out=root/'artifacts/monday-cases-report-20261023'
app=root/'reports/monday-1618-cases-20261023'
target=app/'src/data.json'
identity=json.loads(target.read_text())['id'] if target.exists() else 'report:b2b980f2-70d2-4866-8399-0d8c6aa94a7e'
snapshot=json.loads((out/'reviewed.json').read_text())
snapshot.update(id=identity,buildStatus='creating')
snapshot['queries']['rules']=json.loads((app/'src/content/report/rules-evidence.json').read_text())
for query in snapshot['queries'].values():
    for item in query.get('source',{}).get('metricDefinitions',[]):
        item['definition']=item['definition'].replace('GROUP1 and XAU gross only.','XAU and GROUP1 including gold gross only; case2 GROUP1 BTC/ETH net.')
    for item in query.get('source',{}).get('evidenceFlow',[]):
        item['detail']=item['detail'].replace('GROUP1 gross basis; other crypto net, gold gross.','GROUP1 with gold gross basis; case2 BTC/ETH GROUP1 net; other crypto net, gold gross.')
target.write_text(json.dumps(snapshot,ensure_ascii=False,separators=(',',':')))
assets=app/'src/content/assets/candles'
assets.mkdir(parents=True,exist_ok=True)
paths=sorted((out/'charts').glob('*.png'))
assert len(paths)==120, 'Require all source-based sample panels'
imports=[];pairs=[]
for i,path in enumerate(paths):
    shutil.copy2(path,assets/path.name)
    imports.append(f'import image{i} from "../assets/candles/{path.name}";')
    pairs.append(f'"{path.stem}":image{i}')
(app/'src/content/report/chart-images.js').write_text('\n'.join(imports)+'\nexport const chartImages={'+','.join(pairs)+'};\n')
print(json.dumps({'id':identity,'signals':len(snapshot['queries']['trades']['rows']),'charts':len(paths)}))
