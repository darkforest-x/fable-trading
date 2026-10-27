"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync("yoyo/monitor/static/range-chart.js","utf8")+fs.readFileSync("yoyo/monitor/static/market-sync.js","utf8");
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
test("optional metadata failure preserves stale feed status and backfill evidence",async()=>{
 const root={innerHTML:"",querySelectorAll:()=>[]},window={};
 const end=Date.UTC(2026,9,10);
 const snapshot={events:[{minutes:60,kind:"sync",config:"z3.0_v3.0_b0.75",side:1,open_ms:end,close_ms:end+3600000,out_of_sample:true,outcomes:{}}],latest:{60:[{open_ms:end,close_ms:end+3600000}]}};
 vm.runInNewContext(source,{window,document:{hidden:false,getElementById:id=>id==="market-sync-workspace"?root:null},fetch:async url=>({ok:!url.endsWith("iterations"),status:500,json:async()=>url.includes("history")?{rows:[],exits:["time_12h"],instruments:["eth","btc","alts"]}: {snapshot,status:{state:"error",error:"binance_http_451"},freshness:{60:{stale:true}}}}),Intl,Date,Number,URL,console});
 window.SpikeMarketSync.setActive(true);await flush();
 assert.match(root.innerHTML,/HTTP 451/);assert.match(root.innerHTML,/缓存已过期/);
 assert.match(root.innerHTML,/研究登记暂不可用/);assert.match(root.innerHTML,/上线后及时记录：0 次/);
 assert.doesNotMatch(root.innerHTML,/样本外新增/);
});

async function mountReadableMarket({history: historyOverride, health, latestRow, context, sourceId, audio=false} = {}) {
 const handlers=new Map(),details=new Map(),window={SpikeChartTools:require('../../yoyo/vision_research/static/chart-tools.js')};
 const resonanceNodes=new Map(),periodButtons=['30','60'].map(tf=>({dataset:{resonanceTf:tf},attributes:{},setAttribute(k,v){this.attributes[k]=v;},addEventListener(_,fn){this.click=fn;}}));
 const resonance={innerHTML:'',dataset:{},querySelector(selector){
  if(!this.innerHTML)return null;
  if(!resonanceNodes.has(selector))resonanceNodes.set(selector,{textContent:'',className:''});
  return resonanceNodes.get(selector);
 },querySelectorAll(){return periodButtons;}};
 let beeps=0;const prefs=new Map(audio?[['spike-market-sound','true']]:[]),session=new Map();
 window.AudioContext=class {constructor(){this.currentTime=0;this.destination={};}resume(){return Promise.resolve();}createOscillator(){return {connect(){},frequency:{value:0},start(){beeps++;},stop(){}};}createGain(){return {connect(){},gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}}};}};
 const root={_html:"",get innerHTML(){return this._html;},set innerHTML(v){this._html=v;details.clear();for(const [,id] of v.matchAll(/<details[^>]*id="([^"]+)"/g))details.set(id,{id,open:false});},querySelectorAll(selector){
  if(selector==="details[id][open]")return [...details.values()].filter(d=>d.open);
  if(selector!=="[data-ms]")return [];
  return [...this.innerHTML.matchAll(/data-ms="([^"]+)" data-value="([^"]+)"/g)].map(([,ms,value])=>({dataset:{ms,value},addEventListener:(_,fn)=>handlers.set(`${ms}:${value}`,fn)}));
 }};
 const end=Date.UTC(2026,9,10),config="z3.0_v3.0_b0.75";
 const event=(side,first)=>({minutes:60,kind:"sync",config,side,open_ms:end,close_ms:end+3600000,btc_ret:side*.02,eth_ret:side*.03,btc_z:side*4,eth_z:null,btc_vr:6,eth_vr:5,breadth:.94,out_of_sample:true,...(first?{first_observed_ms:end+3600000}:{}),outcomes:{"12h":{started:true,done:true,eth:-191,btc:100,alts:200},"4h":{started:true,done:true,eth:53.8,btc:0,alts:null},"24h":{started:true,done:false,eth:-1,btc:-2,alts:-3}}});
 const live={snapshot:{events:[event(1,false),event(-1,true)],latest:{}},status:{state:"ok"},freshness:{60:health || {stale:false,ready:true}}};
 if(latestRow)live.snapshot.latest['60']=[latestRow];
 if(sourceId)live.source={id:sourceId,label:'OKX USDT 永续'};
 const history=historyOverride || {exits:["time_12h","time_4h"],instruments:["eth","btc","alts"],rows:[[end,1,4,5,.9,[53.8,100,200,0,-191,null],[3.8,0,0,0,0,0]],[end,-1,-4,-5,.9,[-100,50,60,70,80,90],[0,0,0,0,0,0]]]};
 vm.runInNewContext(source,{window,localStorage:{getItem:k=>prefs.get(k)},sessionStorage:{getItem:k=>session.get(k),setItem:(k,v)=>session.set(k,v)},document:{hidden:false,getElementById:id=>id==="market-sync-workspace"?root:id==='market-resonance-overview'?resonance:details.get(id)||null},fetch:async url=>({ok:true,json:async()=>url.includes("context")?(context||{}):url.includes("history")?history:url.includes("iterations")?{items:[{result:"净收益 +75bp，另一组 -191.0bp",status:"done"}]}:live}),Intl,Date,Number,URL,console});
 window.SpikeMarketSync.setActive(true);await flush();
 return {root,details,live,window,resonance,periodButtons,beeps:()=>beeps,async select(name,value){handlers.get(`${name}:${value}`)();await flush();}};
}

test('resonance strip preserves zero, labels breadth direction and switches closed-bar periods',async()=>{
 const row={close_ms:Date.now(),btc_ret:0,eth_ret:null,btc_z:0,eth_z:null,btc_vr:0,eth_vr:null,breadth_up:.8,breadth_down:.2};
 const page=await mountReadableMarket({latestRow:row}),text=key=>page.resonance.querySelector(`[data-resonance-${key}]`).textContent;
 assert.equal(text('value="btc_ret"'),'+0.00%');assert.equal(text('value="btc_z"'),'0.00×');
 assert.equal(text('value="btc_vr"'),'0.00×');assert.equal(text('value="eth_ret"'),'—');
 assert.equal(text('breadth'),'—');assert.equal(text('direction'),'同向山寨 · 方向待定');
 page.live.freshness['30']={ready:true,stale:false,valid_alts:50,expected_alts:50};
 page.live.snapshot.latest['30']=[{...row,btc_ret:-.01,eth_ret:.02,btc_z:-3,eth_z:2}];
 page.periodButtons[0].click();await flush();
 assert.equal(text('value="btc_ret"'),'-1.00%');assert.equal(text('value="btc_z"'),'3.00×');
 assert.equal(text('breadth'),'20%');assert.equal(text('direction'),'山寨随 BTC 下跌');
 assert.equal(page.periodButtons[0].attributes['aria-pressed'],'true');
 assert.equal(page.periodButtons[1].attributes['aria-pressed'],'false');
});

test('stale or incomplete resonance snapshots cannot appear as current observations',async()=>{
 for(const health of [{ready:true,stale:true},{ready:false,stale:false},{ready:true}]){
  const page=await mountReadableMarket({health,latestRow:triggerRow()});
  assert.equal(page.resonance.dataset.state,'unavailable');
  assert.equal(page.resonance.querySelector('[data-resonance-value="btc_ret"]').textContent,'—');
  assert.equal(page.resonance.querySelector('[data-resonance-breadth]').textContent,'—');
  assert.equal(page.resonance.querySelector('[data-resonance-close]').textContent,'收盘 —');
 }
});

test("basis point API values render as percentages without altering return or control meaning",async()=>{
 const page=await mountReadableMarket();
 assert.ok(page.root.innerHTML.includes("+0.538%"));
 assert.ok(page.root.innerHTML.includes("+0.038%"));
 assert.ok(page.root.innerHTML.includes("-1.91%"));
 assert.ok(page.root.innerHTML.includes("多 0.500 个百分点"));
 assert.ok(page.root.innerHTML.includes("+0.75%"));
 assert.ok(!/[+-]?\d+(?:\.\d+)?bp/.test(page.root.innerHTML));
 assert.ok(page.root.innerHTML.includes("波动为平常的 4.00 / — 倍"));
 assert.ok(page.root.innerHTML.includes("上线后及时记录：0 次"));
 assert.ok(page.root.innerHTML.includes("事后补算"));
 assert.ok(page.root.innerHTML.includes("持有中 · 暂时收益"));
});

test("direction, instrument and horizon controls update the explanation and selected outcome",async()=>{
 const page=await mountReadableMarket();
 page.details.get("ms-filters").open=true;
 await page.select("side","-1");
 assert.ok(page.root.innerHTML.includes("BTC、ETH 都明显下跌"));
 assert.ok(page.root.innerHTML.includes("下一根开盘做空 ETH"));
 assert.ok(page.root.innerHTML.includes("上线后及时记录：1 次"));
 assert.ok(page.details.get("ms-filters").open);
 await page.select("inst","btc");await page.select("hold","4h");
 assert.ok(page.root.innerHTML.includes("下一根开盘做空 BTC → 持有 4 小时"));
 assert.ok(page.root.innerHTML.includes("BTC · 4 小时净收益"));
 assert.ok(page.root.innerHTML.includes('<strong class="ms-event-return"><span class="">+0.00%</span>'));
 await page.select("side","all");
 assert.ok(page.root.innerHTML.includes("上涨后做多、下跌后做空 BTC"));
 await page.select("kind","cluster");
 assert.ok(page.root.innerHTML.includes("前两根里至少一根也同方向满足"));
});

test("unsupported high-volume history stays unavailable rather than showing unfiltered returns",async()=>{
 const page=await mountReadableMarket();await page.select("vol","high");
 assert.ok(page.root.innerHTML.includes("历史文件缺少逐条量比，无法统计这个筛选结果"));
 assert.ok(!page.root.innerHTML.includes("每次平均净收益"));
 assert.ok(!page.root.innerHTML.includes("随机入场的平均净收益"));
});

test("hold comparisons use identical events in both periods and exclude incomplete controls",async()=>{
 const row=(year,net,ctrl)=>[Date.UTC(year,3,1),1,4,5,.9,net,ctrl];
 const page=await mountReadableMarket({history:{exits:["time_1h","time_4h","time_12h","time_24h"],instruments:["eth"],rows:[
  row(2024,[1,2,3,4],[0,0,0,0]),row(2025,[11,12,13,14],[1,2,3,4]),
  row(2025,[1000,1000,1000,1000],[0,0,0,null])
 ]}});
 assert.match(page.root.innerHTML,/2023–2024 · 1 次/);
 assert.match(page.root.innerHTML,/2025 起 · 1 次/);
 assert.match(page.root.innerHTML,/完整收益及对照的 2 次事件/);
 assert.match(page.root.innerHTML,/\+0.13%/);
 assert.match(page.root.innerHTML,/差 \+0.10 个百分点/);
 assert.match(page.root.innerHTML,/入场仍待验证/);
 assert.match(page.root.innerHTML,/退出对比数据暂不可用/);
 await page.select("vol","high");
 assert.doesNotMatch(page.root.innerHTML,/完整收益及对照的 2 次事件/);
});

test("exit comparison labels a different signal set instead of claiming default strategy evidence",async()=>{
 const rows=["select","check"].flatMap(period=>["hold_12h","trail3_48h"].map(exit=>({period,exit,minutes:60,side:1,instrument:"eth",events:100,
  mean_net_bp:exit==="hold_12h"?10:20,control_net_bp:2,vs_base_bp:exit==="hold_12h"?null:10,p_vs_base:exit==="hold_12h"?null:.3})));
 const page=await mountReadableMarket({history:{exits:["time_12h"],instruments:["eth"],rows:[],exit_study:{available:true,event_set:{kind:"sync",z_min:2,v_min:2,b_min:.6},rows,
  exits:["hold_12h","trail3_48h"],baseline:"hold_12h",cost_bp:20,experiment_id:"v5",source_commit:"abc123"}}});
 assert.match(page.root.innerHTML,/不对应当前筛选，不能直接用于当前信号/);
 assert.match(page.root.innerHTML,/本组没有方案达到原研究的改善条件/);
 assert.match(page.root.innerHTML,/较 12h \+0.10 个百分点 · p=0.3000/);
 await page.select("side","all");
 assert.match(page.root.innerHTML,/研究没有把两个方向合并比较/);
 assert.doesNotMatch(page.root.innerHTML,/较 12h \+0.10 个百分点 · p=0.3000/);
});

test("fresh timestamp with missing features does not claim feed readiness",async()=>{
 const page=await mountReadableMarket({health:{stale:false,ready:false,valid_alts:8,expected_alts:50}});
 assert.match(page.root.innerHTML,/行情不完整 · 暂不能判断触发/);
 assert.match(page.root.innerHTML,/山寨覆盖 8 \/ 50/);
 assert.doesNotMatch(page.root.innerHTML,/行情已更新 · 观察最新收盘数据/);
});

const triggerRow=()=>({close_ms:Date.now(),btc_ret:-.04,eth_ret:-.05,btc_z:-4,eth_z:-5,btc_vr:4,eth_vr:5,breadth_up:.1,breadth_down:.9,decisions:{'sync|z3.0_v3.0_b0.75':'event'}});
test('fresh OKX market shock is prominent and sounds only once per closed-bar event',async()=>{
 const row=triggerRow(),page=await mountReadableMarket({latestRow:row,sourceId:'okx-usdt-swap',audio:true});
 assert.match(page.root.innerHTML,/共振异动 · 集体下跌/);assert.match(page.root.innerHTML,/OKX USDT 永续/);
 assert.equal(page.beeps(),1);
 await page.window.SpikeMarketSync.refresh();await flush();assert.equal(page.beeps(),1);
 row.close_ms+=3600000;await page.window.SpikeMarketSync.refresh();await flush();assert.equal(page.beeps(),2);
 page.live.freshness['60'].stale=true;row.close_ms+=3600000;
 await page.window.SpikeMarketSync.refresh();await flush();assert.equal(page.beeps(),2);
 assert.match(page.root.innerHTML,/数据异常 · 暂停当前行情判断/);assert.doesNotMatch(page.root.innerHTML,/共振异动 · 集体下跌/);
});

test('BTC candle image and transparent bias meter disappear as current evidence when stale',async()=>{
 const context={timezone:'America/New_York',bar:'15m',last_close_ms:Date.now(),stale:false,candles:[{time:Date.now()-900000,open:100,high:104,low:98,close:103,volume:5}],levels:[{kind:'range',price:105,label:'周一高'},{kind:'profile',price:101,label:'昨日 POC'}],monday:{complete:true},structure:{label:'上行排列'},bias:{score:67,label:'强多倾向',components:[{name:'市场广度',score:80}]}};
 const page=await mountReadableMarket({context,latestRow:triggerRow(),sourceId:'okx-usdt-swap'});
 assert.equal(page.resonance.querySelector('[data-resonance-score]').textContent,'+67');
 assert.equal(page.resonance.querySelector('[data-resonance-label]').textContent,'↗ 强多倾向');
 assert.match(page.resonance.querySelector('[data-resonance-bias]').className,/r-positive/);
 assert.match(page.root.innerHTML,/aria-valuenow="67"/);assert.match(page.root.innerHTML,/周一高/);assert.match(page.root.innerHTML,/昨日 POC/);
 assert.match(page.root.innerHTML,/<svg[^>]+role="img"/);assert.match(page.root.innerHTML,/不是胜率/);
 context.stale=true;await page.window.SpikeMarketSync.refresh();await flush();
 assert.equal(page.resonance.querySelector('[data-resonance-score]').textContent,'—');
 assert.equal(page.resonance.querySelector('[data-resonance-label]').textContent,'等待行情');
 assert.doesNotMatch(page.resonance.querySelector('[data-resonance-bias]').className,/r-positive|r-negative/);
 assert.doesNotMatch(page.root.innerHTML,/aria-valuenow="67"/);assert.match(page.root.innerHTML,/等待新鲜行情/);
});
test('terminal renders before optional context or history and does not fetch unopened research',async()=>{
 const calls=[],root={innerHTML:'',querySelectorAll:()=>[]},window={SpikeMarketTerminal:{setActive(){},update(){}}};
 vm.runInNewContext(source,{window,document:{hidden:false,getElementById:id=>id==='market-sync-workspace'?root:null},fetch:async url=>{calls.push(url);if(url.includes('/context'))return new Promise(()=>{});return {ok:true,json:async()=>({snapshot:{events:[],latest:{}},freshness:{}})};},Intl,Date,Number,URL,console});
 window.SpikeMarketSync.setActive(true);await flush();
 assert.match(root.innerHTML,/ms-terminal-top/);assert.match(root.innerHTML,/ms-recent-events/);
 assert.ok(!calls.some(url=>url.includes('/history')||url.includes('/iterations')));assert.ok(!root.innerHTML.includes('ms-history'));
});
