"""Receipt and table semantics must survive the visual-design adapter."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_receipt_actions_fee_currency_and_table_missing_values():
    script = r"""
import fs from 'node:fs';
import assert from 'node:assert/strict';
const load=async name=>import('data:text/javascript,'+encodeURIComponent(fs.readFileSync('yoyo/monitor/static/pro/'+name+'.js','utf8')));
const {exchangeReceipt,paperReceipt}=await load('account-records');
const closed=exchangeReceipt({instId:'X-USDT-SWAP',side:'buy',posSide:'short',pnl:'3',fee:'-0.1',feeCcy:'USDT',avgPx:'5',accFillSz:'4',state:'filled'});
assert.equal(closed.action,'close');assert.equal(closed.receipt_net_usdt,2.9);
assert.equal(exchangeReceipt({side:'sell',posSide:'short'}).action,'open');
assert.equal(exchangeReceipt({side:'sell',posSide:'net'}).action,'');
assert.equal(exchangeReceipt({pnl:'3',fee:'-0.1',feeCcy:'BTC'}).receipt_net_usdt,null);
assert.equal(paperReceipt({gross_pnl:'3',fee:'0.1'}).receipt_net_usdt,2.9);
const {correlation,numericColumns}=await load('evidence');
const t={columns:['x','y','constant','missing','label'],rows:[{x:1,y:3,constant:1,missing:1,label:'A'},{x:2,y:2,constant:1,missing:'',label:'B'},{x:3,y:1,constant:1,missing:3,label:'C'},{x:4,y:0,constant:1,missing:4,label:'D'}]};
assert.equal(correlation(t,['x','y']).matrix[0][1],-1);
assert.equal(correlation(t,['x','constant']).matrix[0][1],null);
assert.equal(correlation(t,['x','missing']).count,3);
assert.equal(numericColumns(t).includes('label'),false);
assert.equal(correlation({rows:[{x:1,y:2},{x:2,y:3}]},['x','y']).matrix[0][1],null);
"""
    subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, check=True)
