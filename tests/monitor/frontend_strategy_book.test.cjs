'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ui=require('../../yoyo/monitor/static/reclaim-strategy-book.js');
const data=require('../../yoyo/monitor/static/strategy-booklet/research.json');
const paper=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
test('all eight accounts have separate selection and accessible booklet buttons',()=>{
 const html=ui.pickerHTML(paper.TRACKS);
 for(const t of paper.TRACKS){assert.ok(html.includes(`data-strategy-select="${t}"`));assert.ok(html.includes(`data-strategy-book="${t}"`));assert.ok(data.accounts[t]);assert.doesNotThrow(()=>ui.researchHTML(data,t));}
 assert.equal((html.match(/<button /g)||[]).length,24);
 for(const match of html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g))assert.doesNotMatch(match[1],/<button/);
});
test('local account ordering defaults to spike and preserves valid user order across new accounts',()=>{
 const defaults=ui.accountOrder(null,paper.TRACKS);assert.equal(defaults[0],'spike');assert.equal(defaults.length,8);
 const saved=['v3add20','60','60','deleted'];const result=ui.accountOrder(saved,paper.TRACKS);
 assert.deepEqual(result.slice(0,3),['v3add20','60','spike']);assert.equal(new Set(result).size,8);assert.deepEqual(saved,['v3add20','60','60','deleted']);
 assert.deepEqual(ui.accountOrder({bad:1},paper.TRACKS),defaults);
 const moved=ui.moveAccount(defaults,'v3add20',0);assert.equal(moved[0],'v3add20');assert.equal(defaults[0],'spike');
 assert.deepEqual(ui.moveAccount(defaults,'unknown',0),defaults);assert.equal(ui.moveAccount(defaults,'spike',99).at(-1),'spike');
});
test('picker reports complete realized P&L, preserves zero and missing states, and excludes refill cash',()=>{
 const data={equity:1150,start_equity:1000,closed:[{pnl:99999}],open:[{unrealized_bp:22222}]};
 assert.equal(ui.pnlView(paper.summary(data,'spike').pnl).text,'+150.00');
 assert.equal(ui.pnlView(paper.summary({...data,equity:980},'spike').pnl).text,'−20.00');
 assert.equal(ui.pnlView(paper.summary({...data,equity:1000},'spike').pnl).text,'0.00');
 const allin={...data,refills:[{equity_before:50,equity_after:1000}]};
 assert.equal(ui.pnlView(paper.summary(allin,'allin3').pnl).text,'−800.00');
 assert.equal(ui.pnlView(paper.summary(data,'allin3').pnl).text,'—');assert.equal(ui.pnlView(null).text,'—');
 assert.equal(ui.pnlView(-.001).text,'−<0.01');assert.match(ui.pnlView(1).title,/不含浮盈亏和补充本金/);
});
test('historical source projection preserves saved numbers and excludes broken slot sizing',()=>{
 const base=path.resolve(__dirname,'../..');
 for(const track of ['v3','v3lock']){
  const r=data.accounts[track],s=r.sources[0];assert.ok(s.selection.includes('|linear|L1'));assert.ok(!s.selection.includes('|slots|'));
  const original=JSON.parse(fs.readFileSync(path.join(base,s.path))),variant=track==='v3'?'v3_be17':'v3_be50';
  assert.equal(r.capital_rows[0][1],original.results.sizing[`${variant}|linear|L1`].y2020_2024.final_multiple);
  assert.equal(r.rows[0][3],original.results.per_trade[variant].y2020_2024.mean_bp);
 }
 assert.match(data.accounts['5'].note,/当前为盈利方向\+0.17%/);
 assert.match(data.accounts.v3add.note,/BE0/);assert.match(data.accounts.spike.note,/当前排列分用OKX/);
 assert.match(data.accounts.allin3.note,/不是未来达到目标的概率/);assert.match(data.integrity,/重复计算初始资金/);
});
test('research tables escape text and show missing values without fabricating zero',()=>{
 const html=ui.tableHTML(['<img>'],[['<script>',null,5]]);assert.match(html,/&lt;img&gt;/);assert.match(html,/&lt;script&gt;/);assert.match(html,/>—</);
 const options=ui.barOptions({labels:['A','B'],values:[null,1.5],unit:'倍'});assert.deepEqual(options.series[0].data,[null,1.5]);assert.equal(options.series[0].type,'bar');
});

test('v3add20 uses the corrected dynamic-margin study and labels historical BE0',()=>{
 const r=data.accounts.v3add20,s=r.sources[0];assert.match(s.path,/return-opt-20261027-v2/);
 const original=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../..',s.path))).results.settings;
 assert.equal(r.rows[0][1],original['v3add|frac|N20|L3'].y2020_2024.final_multiple);
 assert.equal(r.rows.at(-1)[1],original['v3add|frac|N20|L3'].from_2020.final_multiple);
 assert.equal(r.capital_rows[1][1],original['v3add|slots|N20|L3'].from_2020.final_multiple);
 assert.match(r.note,/BE0/);assert.match(r.note,/当前保护价.*0.17%/);assert.match(ui.researchHTML(data,'v3add20'),/历史资金曲线暂时无法读取/);
 assert.equal(r.charts.length,3);assert.equal(r.images,undefined);
});

test('every booklet has an account-matched continuous history with a real drawdown path',()=>{
 for(const t of paper.TRACKS){
  const h=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../yoyo/monitor/static/strategy-booklet',t+'-history.json')));
  assert.equal(ui.validateHistory(h,t),h);assert.throws(()=>ui.validateHistory(h,'wrong'));
  const html=ui.researchHTML(data,t,h);assert.match(html,/历史资金与回撤曲线/);assert.match(html,/月度/);assert.doesNotMatch(html,/历史资金曲线暂时无法读取/);
  const chart=ui.equityOptions(h,'equity');assert.equal(chart.series[0].step,'end');assert.equal(chart.series[0].smooth,false);assert.equal(chart.series[0].data.length,h.points.length);
  assert.equal(chart.series[1].data.length,h.points.length);assert.deepEqual(chart.dataZoom[0].xAxisIndex,[0,1]);
  assert.equal(chart.series[0].data.at(-1)[1],h.summary.end);
  const ret=ui.equityOptions(h,'returns');assert.equal(ret.series[0].data.at(-1)[1],h.refilled?h.summary.pnl:h.summary.return_pct);
 }
});
test('historical refills are distinct from returns and keep pre-refill lows',()=>{
 const h=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../yoyo/monitor/static/strategy-booklet/allin3-history.json')));
 assert.equal(h.refilled,true);assert.equal(h.summary.return_pct,null);assert.ok(h.summary.cash_added>0);
 assert.match(ui.historyHTML(h),/补充本金不计入交易收益/);assert.match(ui.heatmapHTML(h),/月度交易净盈亏/);
 assert.equal(ui.equityOptions(h).series[2].name,'累计净投入');assert.equal(ui.equityOptions(h,'returns').series.length,2);
 assert.ok(h.points.some((p,i)=>i&&p[0]===h.points[i-1][0]&&p[1]>h.points[i-1][1]));
});
