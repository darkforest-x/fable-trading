'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const paper=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const close=require('../../yoyo/monitor/static/reclaim-close.js');
const book=require('../../yoyo/monitor/static/reclaim-strategy-book.js');
const backtest=require('../../yoyo/monitor/static/strategy-backtest.js');
const history=require('../../yoyo/monitor/static/strategy-booklet/live-allin3-history.json');

test('live-allin3 route and touch list never fall back to retrospective scores',()=>{
 const state=paper.route({track:'live-allin3',tab:'events'});
 assert.equal(state.track,'live-allin3');
 const original={id:1,symbol:'ABC-USDT-SWAP',candle_ms:1000,score:.9};
 const data={now_ms:10000,tracks:{},signals:[original],live_signals:[{...original,score:.61}]};
 assert.equal(paper.rows(data,state)[0].score,.61);
 assert.equal(paper.rows({...data,live_signals:[]},state).length,0);
 assert.equal(paper.rows(data,paper.route({track:'allin3',tab:'events'}))[0].score,.9);
 assert.equal(paper.targetLabel({track:7060},'live-allin3'),'对侧 1.618');
});
test('live-allin3 monetary displays remove replenishments and charge original slippage once',()=>{
 assert.equal(paper.summary({equity:1000,start_equity:1000,refills:[{equity_before:95,equity_after:1000}]},'live-allin3').pnl,-905);
 const p={taken:1,stage:'pending_confirm',remaining_notional:3000,entry:100,mark:100,side:1,track:7060,margin_leverage:3};
 const value=close.estimate(p,50);assert.ok(Math.abs(value.pnl+2.55)<1e-10);
 assert.equal(value.entryFee,.3);assert.equal(value.exitFee,.75);
 assert.equal(close.estimate({...p,mark:1},50).pnl,-500);
});
test('booklet is the 422-trade pre-touch path and labels terminal historical settlement',()=>{
 assert.equal(book.validateHistory(history,'live-allin3'),history);
 assert.equal(history.summary.trades,422);assert.ok(Math.abs(history.summary.pnl-406801.41165444884)<1e-8);
 assert.ok(Math.abs(history.yearly.find(r=>r[0]==='2026')[1]-417032.2228192504)<1e-8);
 assert.match(book.historyHTML(history),/127506.23/);
 const row={id:1,symbol:'ONUSDT',side:-1,entry_ms:1000,exit_ms:2000,pnl:10,return_pct:1,exit_reason:0,
   entry_rules:{status:'saved',score:.7,minimum:.6,kind:'first_touch',score_basis:'pre_touch'}};
 const html=backtest.rowHTML(row);assert.match(html,/触价前评分/);assert.match(html,/期末结算/);
});
