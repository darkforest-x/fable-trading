'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

function loadRoute(search='?book=live-cloud-v17&chapter=history'){
  const context={
    window:{},
    location:{href:'https://trade.example/'+search,pathname:'/',search,hash:''},
    history:{state:null,replaceState(){}},
    URL,URLSearchParams
  };
  vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/cloud-book-route.js','utf8'),context);
  return context.window.SpikeCloudBookRoute;
}

test('cloud booklet deep links normalize legacy trade keys for shared replay',()=>{
  const route=loadRoute('?book=live-cloud-v17&chapter=history&trade=cloud-v17-001082&query=BEAT&side=-1&outcome=win&from=2026-01-01&until=2026-02-01&order=loss');
  const parsed=route.read('?book=live-cloud-v17&chapter=history&trade=cloud-v17-001082&query=BEAT&side=-1&outcome=win&from=2026-01-01&until=2026-02-01&order=loss');
  assert.equal(parsed.chapter,'history');
  assert.deepEqual({...route.backtestOptions(parsed)},{trade:'1082',query:'BEAT',side:'short',outcome:'win',from:'2026-01-01',until:'2026-02-01',order:'loss'});
  assert.equal(route.tradeId('000643'),'643');
  assert.equal(route.tradeId('not-a-trade'),'');
});
