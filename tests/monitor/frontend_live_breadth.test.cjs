'use strict';
// live-breadth on the paper page (owner 2026-11-08, Stitch 2762455ffefe4f5a9784c2e2e0fd4db5).
const test=require('node:test'),assert=require('node:assert/strict');
const paper=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const DAY=86400000,now=Date.UTC(2026,10,12,9,34);
const leg=(symbol,taken,extra={})=>({id:9260000001+(symbol==='SOL-USDT-SWAP'?0:1),track:9260,symbol,side:1,entry:100,fill_ms:now-2*DAY,decision_ms:now+4*DAY,stage:'holding',taken,notional:1000,remaining_notional:null,note:taken?null:'comparison',unrealized_bp:241,exits:[],...extra});
test('breadth account is listed and labelled',()=>{
 assert.ok(paper.TRACKS.includes('live-breadth'));
 assert.equal(paper.trackLabel('live-breadth'),'live-breadth');
 assert.equal(paper.targetLabel(leg('SOL-USDT-SWAP',1),'live-breadth'),'6天到期');
});
test('holding stage counts down to the six-day exit and closes as the day-6 exit',()=>{
 assert.equal(paper.stage(leg('SOL-USDT-SWAP',1),now),'持有中 · 剩 4天0小时');
 assert.equal(paper.stage(leg('SOL-USDT-SWAP',1,{stage:'closed',reason:'time_6d'}),now),'第6天平仓');
 assert.equal(paper.allocation(leg('BTC-USDT-SWAP',0)),'影子 · 对照');
 assert.equal(paper.allocation(leg('SOL-USDT-SWAP',1)),'计入资金');
});
test('signal rows render status chips, never a touch score',()=>{
 const st={track:'live-breadth',tab:'events',q:'',scope:'funded',sort:'time_desc',trade:'',symbol:''};
 const html=paper.rowHTML({id:9260000001,breadth:true,symbol:'SOL-USDT-SWAP',candle_ms:now-5*DAY,decision_ms:now-5*DAY+35*60000,n:51,new_n:2,status:'followers'},st,now);
 assert.match(html,/51 个币首次碰周一下轨/);assert.match(html,/观察期有新币 · 取消 <b>\+2<\/b>/);assert.doesNotMatch(html,/评分/);
 const watching=paper.rowHTML({id:9260000002,breadth:true,symbol:'SOL-USDT-SWAP',candle_ms:now-5*60000,decision_ms:now+12*60000,n:33,new_n:null,status:'watching'},st,now);
 assert.match(watching,/观察中 · 剩 12分/);
});
test('position rows show funded/comparison tags, a six-day bar and no manual close',()=>{
 const st={track:'live-breadth',tab:'open',q:'',scope:'all',sort:'time_desc',trade:'',symbol:''};
 const sol=paper.rowHTML(leg('SOL-USDT-SWAP',1),st,now),btc=paper.rowHTML(leg('BTC-USDT-SWAP',0),st,now);
 assert.match(sol,/rp-breadth-tag is-funded">计入资金/);assert.match(btc,/影子 · 对照/);
 assert.match(sol,/持有 2天0小时 \/ 6天/);assert.match(sol,/--rp-hold:33\.3%/);
 assert.doesNotMatch(sol,/data-rp-close/);
});
test('recent breadth signals and closes are not limited to today',()=>{
 const data={now_ms:now,tracks:{'live-breadth':{open:[],closed:[leg('SOL-USDT-SWAP',1,{stage:'closed',exit_ms:now-9*DAY})]}},'live-breadth':{signals:[{id:1,breadth:true,symbol:'SOL-USDT-SWAP',candle_ms:now-9*DAY,status:'taken'}]},signals:[]};
 for(const tab of ['closed','events'])assert.equal(paper.rows(data,{track:'live-breadth',tab,q:'',scope:'all',sort:'time_desc'}).length,1,tab);
 assert.equal(paper.rows({...data,tracks:{spike:{open:[],closed:[{...leg('SOL-USDT-SWAP',1),track:3060,stage:'closed',exit_ms:now-9*DAY}]}}},{track:'spike',tab:'closed',q:'',scope:'all',sort:'time_desc'}).length,0,'other accounts keep the today filter');
});
test('rules describe the band, the approximation and SOL-only funding',()=>{
 const html=paper.rulesHTML({tracks:{'live-breadth':{start_equity:1000}},'live-breadth':{count_min:31,count_max:62,observe_minutes:30,hold_days:6,leverage:1,taker_bp:5}},'live-breadth');
 assert.match(html,/31–62 个 OKX 币/);assert.match(html,/近似/);assert.match(html,/BTC、ETH 以同样金额记作对照/);
});
