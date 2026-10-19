'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../yoyo/monitor/static/paper-desk-market.js'),'utf8');
const sandbox={window:{},module:{exports:{}},Date};vm.runInNewContext(source,sandbox);const {config,tvSymbol,ticker}=sandbox.module.exports;
test('TradingView receives the exact OKX perpetual symbol, never spot or another exchange',()=>{
  assert.equal(tvSymbol('BTC-USDT-SWAP'),'OKX:BTCUSDT.P');assert.equal(tvSymbol('ETH-USDT-SWAP'),'OKX:ETHUSDT.P');
  assert.equal(tvSymbol('1000PEPE-USDT-SWAP'),'OKX:1000PEPEUSDT.P');
  assert.equal(tvSymbol('W-USDT-SWAP'),'OKX:WUSDT.P');
  for(const invalid of ['BTCUSDT','ETH-USDC-SWAP','BTC-USDT','-USDT-SWAP','<script>','BTC-USDT-SWAP?symbol=BTCUSD'])assert.equal(tvSymbol(invalid),null);
});
test('official widget locks symbol selection to the trading form and retains native indicators and volume',()=>{
  const c=config('ETH-USDT-SWAP','240','dark');assert.equal(c.symbol,'OKX:ETHUSDT.P');assert.equal(c.locale,'zh_CN');
  assert.equal(c.allow_symbol_change,false);assert.equal(c.hide_top_toolbar,false);assert.equal(c.hide_volume,false);
  assert.equal(c.theme,'dark');assert.equal(c.timezone,'Asia/Shanghai');assert.equal(c.interval,'240');
});
test('last trade and 24h change display independently of executable bid ask',()=>{
  const html=ticker({last:'0.00012345',bid:'.00012',ask:'.00013',change_24h_percent:'-2.5',source_ms:Date.now(),expires_ms:Date.now()+15000});
  assert.match(html,/0\.00012345/);assert.match(html,/-2\.50%/);assert.match(html,/desk-short/);assert.match(html,/OKX ·/);
});
test('stale display price is labelled stale; missing prices and change never turn into zero',()=>{
  assert.match(ticker({last:'100',expires_ms:Date.now()-1}),/行情过期/);
  const missing=ticker(null,'offline');assert.match(missing,/行情暂不可用/);assert.doesNotMatch(missing,/0\.00%/);assert.match(missing,/—/);
});
test('24h zero is a real flat change, distinct from missing',()=>{
  assert.match(ticker({last:'100',change_24h_percent:'0',source_ms:Date.now(),expires_ms:Date.now()+15000}),/0\.00%/);
});

test('panel layout preference survives a new mount and invalid values use the compact default',()=>{
  const values=new Map();sandbox.localStorage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
  const {readLayout,applyLayout}=sandbox.module.exports;
  const root={dataset:{}};
  assert.equal(readLayout(),'chart-left');
  for(const layout of ['ledger-left','chart-left','chart-right','chart-top']){
    assert.equal(applyLayout(root,layout),layout);assert.equal(root.dataset.panelLayout,layout);assert.equal(readLayout(),layout);
  }
  values.set('spike-paper-panel-layout-v2','invalid');assert.equal(readLayout(),'chart-left');
  assert.equal(applyLayout(root,'invalid'),'chart-left');
});
test('layout switching works without storage and does not touch chart DOM',()=>{
  sandbox.localStorage={getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}};
  const {readLayout,applyLayout}=sandbox.module.exports;
  const root={dataset:{},replaceChildren(){throw new Error('chart detached');},set innerHTML(_){throw new Error('chart rebuilt');}};
  assert.equal(readLayout(),'chart-left');assert.equal(applyLayout(root,'ledger-left'),'ledger-left');assert.equal(root.dataset.panelLayout,'ledger-left');
});

function marketHarness(initialTab){
  const handlers={},queries=new Map(),requests=[];let interval,paint=0;
  const node=()=>({hidden:false,dataset:{},children:[],attrs:{},innerHTML:'',textContent:'',setAttribute(k,v){this.attrs[k]=v;},replaceChildren(...items){this.children=items;},querySelector:()=>null,querySelectorAll:()=>[],appendChild(){},remove(){},focus(){}});
  const widget=node();Object.defineProperty(widget,'innerHTML',{get:()=>'',set:()=>{paint++;widget.firstElementChild={isConnected:true,appendChild(){}};}});
  for(const key of ['[data-market-help]','[data-market-ticker]','[data-market-tools]','[data-market-balances]','[data-market-selector]','[data-market-external]','[data-market-range]','[data-range-content]','[data-range-title]','[data-range-bars]','[data-range-zone]'])queries.set(key,node());
  queries.set('[data-market-widget]',widget);
  const tabs=['tradingview','range'].map(value=>({...node(),dataset:{chartTab:value}}));
  const host={...node(),querySelector:selector=>queries.get(selector),querySelectorAll:selector=>selector==='[data-chart-tab]'?tabs:[],addEventListener:(event,fn)=>handlers[event]=fn};
  const body=node(),rootNodes=new Map([['[data-desk-market]',host],['[data-desk-body]',body],['[data-desk-toolbar-slot]',node()],['[data-desk-ticket-slot]',node()],['[data-desk-ledger-slot]',node()],['[data-desk-notices]',node()]]);
  const root={...node(),querySelector:selector=>rootNodes.get(selector)};
  const window={SpikeRangeChart:{mount:(host,c)=>{host.innerHTML=c?`CHART:${c.symbol}`:'';},unmount:()=>{},clock:()=>''}},prefs=new Map(initialTab?[['spike-paper-chart-tab-v1',initialTab]]:[]);
  const context={window,document:{hidden:false,documentElement:{dataset:{theme:'light'}},createElement:()=>node()},localStorage:{getItem:k=>prefs.get(k),setItem:(k,v)=>prefs.set(k,v)},
    Date,AbortController,setTimeout:()=>1,clearTimeout(){},setInterval:fn=>{interval=fn;},clearInterval(){},MutationObserver:class{observe(){}disconnect(){}},
    fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve:data=>resolve({ok:true,json:async()=>data})}))};
  vm.runInNewContext(source,context);
  return {api:window.SpikePaperMarket,root,queries,requests,paint:()=>paint,tick:()=>interval(),
    tab:value=>handlers.click({target:{closest:()=>({dataset:{chartTab:value},hasAttribute:()=>false})}})};
}
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
test('empty initial mount works and tab changes or ledger refresh never rebuild a same-symbol iframe',()=>{
 const c=marketHarness();
 c.api.render(c.root,'',{selector:''});assert.equal(c.paint(),0);
 c.api.render(c.root,'',{symbol:'BTC-USDT-SWAP',selector:''});assert.equal(c.paint(),1);
 c.tab('range');assert.equal(c.queries.get('[data-market-widget]').hidden,true);
 c.api.render(c.root,'poll',{symbol:'BTC-USDT-SWAP',selector:''});assert.equal(c.paint(),1);
 c.tab('tradingview');assert.equal(c.queries.get('[data-market-widget]').hidden,false);assert.equal(c.paint(),1);
 c.api.stop();
});
test('late range responses cannot overwrite the newly selected contract',async()=>{
 const c=marketHarness();c.api.render(c.root,'',{symbol:'BTC-USDT-SWAP',selector:''});c.tab('range');
 const btc=c.requests.find(x=>x.url.includes('/context'));
 c.api.render(c.root,'',{symbol:'ETH-USDT-SWAP',selector:''});
 const eth=c.requests.find(x=>x.url.includes('/context')&&x.url.includes('ETH'));
 assert.equal(btc.options.signal.aborted,true);
 eth.resolve({symbol:'ETH-USDT-SWAP',timezone:'America/New_York',candles:[{}]});await settle();
 btc.resolve({symbol:'BTC-USDT-SWAP',timezone:'America/New_York',candles:[{}]});await settle();
 assert.match(c.queries.get('[data-range-content]').innerHTML,/CHART:ETH-USDT-SWAP/);
 assert.doesNotMatch(c.queries.get('[data-range-content]').innerHTML,/BTC/);
 assert.equal(c.paint(),1);c.tab('tradingview');assert.equal(c.paint(),2);c.api.stop();
});

test('a saved range tab defers external widget initialization until it becomes visible',()=>{
 const c=marketHarness('range');c.api.render(c.root,'',{symbol:'BTC-USDT-SWAP',selector:''});
 assert.equal(c.paint(),0);assert.equal(c.queries.get('[data-market-help]').hidden,true);
 c.api.render(c.root,'poll',{symbol:'BTC-USDT-SWAP',selector:''});assert.equal(c.paint(),0);
 c.tab('tradingview');assert.equal(c.paint(),1);assert.equal(c.queries.get('[data-market-help]').hidden,false);
 c.tab('range');c.tab('tradingview');assert.equal(c.paint(),1);c.api.stop();
});
