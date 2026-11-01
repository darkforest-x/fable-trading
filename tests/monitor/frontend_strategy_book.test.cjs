'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ui=require('../../yoyo/monitor/static/reclaim-strategy-book.js');
const data=require('../../yoyo/monitor/static/strategy-booklet/research.json');
const paper=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
test('all thirteen accounts have separate selection and accessible booklet buttons',()=>{
 const html=ui.pickerHTML(paper.TRACKS);
 for(const t of paper.TRACKS){assert.ok(html.includes(`data-strategy-select="${t}"`));assert.ok(html.includes(`data-strategy-book="${t}"`));assert.ok(html.includes(`data-strategy-backtest="${t}"`));assert.ok(data.accounts[t]);assert.doesNotThrow(()=>ui.researchHTML(data,t));}
 assert.equal((html.match(/<button /g)||[]).length,52);
 for(const match of html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g))assert.doesNotMatch(match[1],/<button/);
});
test('local account ordering defaults to spike and preserves valid user order across new accounts',()=>{
 const defaults=ui.accountOrder(null,paper.TRACKS);assert.equal(defaults[0],'spike');assert.equal(defaults.length,13);
 const saved=['v3add20','60','60','deleted'];const result=ui.accountOrder(saved,paper.TRACKS);
 assert.deepEqual(result.slice(0,3),['v3add20','60','spike']);assert.equal(new Set(result).size,13);assert.deepEqual(saved,['v3add20','60','60','deleted']);
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

test('booklet supplements render as escaped collapsible tables after the controls',()=>{
 const r={status:'s',note:'n',headers:['h'],rows:[['a']],controls:{note:'c',headers:['h'],rows:[['b']]},
  sections:[{title:'<b>t</b>',note:'<i>x</i>',headers:['k'],rows:[['v']]}]};
 const html=ui.researchHTML({accounts:{x:r},period_note:''},'x');
 assert.match(html,/<summary>&lt;b&gt;t&lt;\/b&gt;<\/summary><p class="rs-muted">&lt;i&gt;x&lt;\/i&gt;<\/p>/);
 assert.ok(html.indexOf('同条件随机入场对照')<html.indexOf('&lt;b&gt;t'));
 for(const t of ['live-wick6x3','live-wick3x3','live-wick6x3hi','live-wick3x3far','live-allin3'])
  assert.match(ui.researchHTML(data,t),/倍投暂不采用|已研究，暂不采用/);
 assert.match(ui.researchHTML(data,'live-wick6x3'),/2026年10月补跑/);
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

test('month calendar reconciles every saved account/month without treating refills as profit',()=>{
 for(const track of paper.TRACKS){
  const h=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../yoyo/monitor/static/strategy-booklet',track+'-history.json')));
  let pnl=0;
  for(const [key,value] of h.monthly){
   const m=ui.monthCalendar(h,key);assert.ok(m);pnl+=m.pnl;
   assert.equal(m.returnPct,h.refilled?null:value);
   const calculated=h.refilled?m.pnl:(m.days.filter(d=>d.covered).reduce((v,d)=>v*(1+d.returnPct/100),1)-1)*100;
   assert.ok(Math.abs(calculated-value)<1e-6,`${track}/${key}: ${calculated} != ${value}`);
  }
  assert.ok(Math.abs(pnl-h.summary.pnl)<1e-6,track);
  assert.equal(ui.monthCalendar(h,'9999-12'),null);
  const markup=ui.heatmapHTML(h);
  assert.equal((markup.match(/data-history-month=/g)||[]).length,h.monthly.length);
 }
});

test('calendar uses UTC settlement dates, leap days, carried equity and explicit coverage',()=>{
 const t=s=>Date.parse(s),h={refilled:false,monthly:[['2024-02',10],['2024-03',0]],points:[
  [t('2024-02-10T10:00Z'),1000,0,0,1000],
  [t('2024-02-28T23:59Z'),1100,10,0,1000],
  [t('2024-02-29T00:00Z'),990,-1,-10,1000],
  [t('2024-02-29T00:00Z'),1100,10,0,1000],
  [t('2024-03-02T03:00Z'),1100,10,0,1000]]};
 const feb=ui.monthCalendar(h,'2024-02');assert.equal(feb.days.length,29);assert.equal(feb.offset,3);
 assert.equal(feb.previous,null);assert.equal(feb.next,'2024-03');assert.equal(feb.days[8].covered,false);
 assert.equal(feb.days[9].covered,true);assert.equal(feb.days[9].changed,false);
 assert.equal(feb.days[27].pnl,100);assert.ok(Math.abs(feb.days[27].returnPct-10)<1e-10);
 assert.equal(feb.days[28].pnl,0);assert.equal(feb.days[28].changed,true);assert.equal(feb.days[28].returnPct,0);
 const march=ui.monthCalendar(h,'2024-03');assert.equal(march.days[1].covered,true);assert.equal(march.days[2].covered,false);assert.equal(march.days[2].pnl,null);assert.equal(march.next,null);
 assert.match(ui.calendarHTML(march),/未覆盖/);assert.equal(ui.monthCalendar(h,'2024-13'),null);
});

test('same-time loss and refill remain a loss; pure cash additions never become daily profit',()=>{
 const start=Date.UTC(2024,1,1),h={refilled:true,monthly:[['2024-02',-900]],points:[
  [start,1000,0,0,1000],[start+86400000,100,-900,-90,1000],
  [start+86400000,1000,-900,0,1900],[start+172800000,1200,-900,0,2100]]};
 const m=ui.monthCalendar(h,'2024-02');assert.equal(m.pnl,-900);assert.equal(m.days[1].pnl,-900);
 assert.equal(m.days[2].pnl,0);assert.equal(m.days[2].changed,false);assert.equal(m.days[1].returnPct,null);
 const html=ui.calendarHTML(m);assert.match(html,/补充本金不计收益/);assert.doesNotMatch(html,/月收益率/);
 assert.match(html,/−900\.00/);assert.match(html,/2024-02-02 · −900\.00 USDT/);
});

const backtest=require('../../yoyo/monitor/static/strategy-backtest.js');
const flush=()=>new Promise(r=>setImmediate(r));
const dayRow=(id,pnl=1)=>({id,symbol:'AAAUSDT',side:1,entry_ms:Date.parse('2024-02-28T23:50:00Z'),exit_ms:Date.parse('2024-02-29T00:10:00Z'),pnl,return_pct:pnl});
const dayPage=(q,rows,total=rows.length,pnl=rows.reduce((v,r)=>v+r.pnl,0))=>({schema:1,...q,timezone:'UTC',source:'Binance',rows,total,pnl,has_more:q.offset+rows.length<total});
test('daily pages validate exact strategy/date, UTC boundaries and calendar amounts',()=>{
 const q={track:'spike',date:'2024-02-29',expectedPnl:1},p=dayPage({...q,offset:0},[dayRow(1)]);
 assert.equal(ui.validateDayPage(p,q,0,backtest.valid),p);
 for(const update of [{track:'60'},{date:'2024-02-28'},{timezone:'Asia/Shanghai'},{pnl:2},{rows:[dayRow(1),dayRow(1)],total:2},{rows:[{...dayRow(1),exit_ms:Date.parse('2024-03-01T00:00:00Z')}]},{rows:[],total:1,has_more:true}])assert.throws(()=>ui.validateDayPage({...p,...update},q,0,backtest.valid));
 const html=ui.dayTradeHTML({...dayRow(351),entry_rules:{score:.659222}});
 assert.match(html,/data-day-trade="351"/);assert.match(html,/2024-02-28 23:50/);assert.match(html,/2024-02-29 00:10/);assert.match(html,/0.6592/);assert.match(html,/历史K线/);
 assert.match(ui.dayTradesHTML({date:q.date,expectedPnl:0,data:{total:0},rows:[],busy:false,error:''}),/当天无结算交易/);
 assert.match(ui.dayTradesHTML({date:q.date,expectedPnl:0,data:null,rows:[],busy:false,error:'offline'}),/重试读取/);
});
test('late day reads cannot overwrite a different date or reopen a dismissed calendar',async()=>{
 const requests=[];const m=ui.createDayModel({validRow:backtest.valid,read:q=>new Promise(resolve=>requests.push({q,resolve}))});
 m.select('spike','2024-02-29',1);await flush();m.select('60','2024-03-01',0);await flush();
 requests[0].resolve(dayPage(requests[0].q,[dayRow(1)]));await flush();assert.equal(m.getState().data,null);
 requests[1].resolve(dayPage(requests[1].q,[]));await flush();assert.equal(m.getState().date,'2024-03-01');assert.equal(m.getState().data.total,0);
 m.select('spike','2024-02-29',1);await flush();m.pause();requests[2].resolve(dayPage(requests[2].q,[dayRow(1)]));await flush();assert.equal(m.getState().data,null);
});
test('day pagination retains rows on failure, retries the same offset and reconciles full total',async()=>{
 const req=[];const m=ui.createDayModel({validRow:backtest.valid,read:q=>new Promise((resolve,reject)=>req.push({q,resolve,reject}))});
 m.select('spike','2024-02-29',1);await flush();req[0].resolve(dayPage(req[0].q,[dayRow(1,2)],2,1));await flush();
 m.load();await flush();assert.equal(req[1].q.offset,1);req[1].reject(Error('offline'));await flush();assert.equal(m.getState().rows.length,1);
 m.load();await flush();assert.equal(req[2].q.offset,1);req[2].resolve(dayPage(req[2].q,[dayRow(2,-1)],2,1));await flush();assert.equal(m.getState().rows.length,2);assert.equal(m.getState().error,'');
 m.select('spike','2024-02-29',1);await flush();req[3].resolve(dayPage(req[3].q,[dayRow(1,2)],1,1));await flush();assert.match(m.getState().error,/合计不一致/);assert.equal(m.getState().rows.length,0);
});
