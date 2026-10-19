'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {candles,daily,average}=require('../../yoyo/monitor/static/market-terminal.js');
const day=Date.UTC(2026,9,16)/1000;
const rows=Array.from({length:6},(_,i)=>({time:day+i*14400,open:100+i,high:110+i,low:90+i,close:101+i,volume:i+1}));
test('daily display uses complete closed UTC days and exact OHLCV',()=>{
 assert.deepEqual(daily(rows,(day+86400)*1000),[{time:day,open:100,high:115,low:90,close:106,volume:21}]);
 assert.deepEqual(daily(rows,(day+86400)*1000-1),[]);
});
test('missing or duplicated source candles never become a daily candle',()=>{
 assert.deepEqual(daily(rows.slice(1),(day+86400)*1000),[]);
 assert.deepEqual(daily([...rows.slice(0,5),rows[4]],(day+86400)*1000),[]);
 assert.deepEqual(daily([...rows,rows[5]],(day+86400)*1000),[]);
});
test('daily aggregation does not join incomplete adjacent calendar days',()=>{
 const next=rows.map(x=>({...x,time:x.time+86400}));
 assert.deepEqual(daily([...rows.slice(1),...next.slice(0,5)],(day+172800)*1000),[]);
 assert.equal(daily([...next,...rows].reverse(),(day+172800)*1000).length,2);
});
test('display moving averages require warmup and remain unchanged when future bars arrive',()=>{
 const first=average(rows.slice(0,4),3,14400),all=average(rows,3,14400);
 assert.deepEqual(first,[{time:day+28800,value:102},{time:day+43200,value:103}]);
 assert.deepEqual(all.slice(0,2),first);assert.deepEqual(average(rows,120,14400),[]);
});
test('moving averages break across a missing bar instead of bridging the gap',()=>{
 const gap=[rows[0],rows[1],...rows.slice(3)];
 assert.deepEqual(average(gap,3,14400),[{time:rows[5].time,value:105}]);
});
test('both existing chart schemas normalize to identical timestamps and OHLC',()=>{
 const source={t:day*1000,o:100,h:110,l:90,c:105,v:7};
 assert.deepEqual(candles([source]),[{time:day,open:100,high:110,low:90,close:105,volume:7}]);
 assert.deepEqual(candles([{time:day*1000,open:100,high:110,low:90,close:105,volume:7}]),candles([source]));
 assert.deepEqual(candles([{...source,c:NaN}]),[]);
});

test('all-market sorting uses the entire universe and puts missing/stale changes last',()=>{
 const {rankedSymbols}=require('../../yoyo/monitor/static/market-terminal.js');
 const symbols=Array.from({length:494},(_,i)=>`COIN${String(i).padStart(3,'0')}-USDT-SWAP`);
 const quotes=new Map(symbols.map((symbol,i)=>[symbol,{change_24h_percent:String(i-250),volume_usdt_24h:String(i*100),expires_ms:2000}]));
 quotes.set(symbols[0],{change_24h_percent:'9999',expires_ms:999});
 quotes.set(symbols[1],{change_24h_percent:null,expires_ms:2000});
 quotes.set(symbols[2],{change_24h_percent:'NaN',expires_ms:2000});
 const gainers=rankedSymbols(symbols,quotes,'gainers','',1000),losers=rankedSymbols(symbols,quotes,'losers','',1000);
 assert.equal(gainers.length,494);assert.equal(gainers[0],symbols[493]);assert.equal(losers[0],symbols[3]);
 assert.ok(gainers.indexOf(symbols[250])<gainers.indexOf(symbols[249]));
 assert.deepEqual(new Set(gainers.slice(-3)),new Set(symbols.slice(0,3)));
 assert.deepEqual(new Set(losers.slice(-3)),new Set(symbols.slice(0,3)));
 assert.deepEqual(rankedSymbols(symbols,quotes,'gainers',' COIN49 ',1000),symbols.slice(490).reverse());
 assert.equal(symbols[0],'COIN000-USDT-SWAP');
});
test('rank ties are deterministic and only the requested watchlist is ranked',()=>{
 const {rankedSymbols}=require('../../yoyo/monitor/static/market-terminal.js');
 const quotes=new Map(['BTC','ETH','SOL'].map(s=>[s+'-USDT-SWAP',{change_24h_percent:'-2',volume_usdt_24h:s==='ETH'?'200':'100',expires_ms:2000}]));
 assert.deepEqual(rankedSymbols(['SOL-USDT-SWAP','BTC-USDT-SWAP'],quotes,'losers','',1000),['BTC-USDT-SWAP','SOL-USDT-SWAP']);
 assert.equal(rankedSymbols([...quotes.keys()],quotes,'volume','',1000)[0],'ETH-USDT-SWAP');
});
test('native chart normalization preserves all six source-owned warmed averages',()=>{
 const source={t:day*1000,o:100,h:110,l:90,c:105,v:7,sma20:101,sma60:102,sma120:103,ema20:104,ema60:105,ema120:106};
 const row=candles([source])[0];for(const key of ['sma20','sma60','sma120','ema20','ema60','ema120'])assert.equal(row[key],source[key]);
 assert.equal(row.ma7,undefined);
});
test('daily EMA warmup resets at gaps and remains prefix-causal',()=>{
 const {exponential}=require('../../yoyo/monitor/static/market-terminal.js');
 assert.deepEqual(exponential(rows,3,14400).slice(0,2),exponential(rows.slice(0,4),3,14400));
 assert.equal(exponential(rows,120,14400).length,0);
 assert.equal(exponential([rows[0],rows[1],...rows.slice(3)],3,14400)[0].time,rows[5].time);
});

test('six-line display is readable for both BTC and low-price coins without changing source values',()=>{
 const {legend,legendPrice,lines}=require('../../yoyo/monitor/static/chart-style.js');
 const row=Object.fromEntries(lines.map((l,i)=>[l.key,0.000000238578333+i*1e-11]));
 const original={...row},html=legend(row);
 assert.equal(legendPrice(0.000000238578333),'0.000000238578');
 assert.equal(legendPrice(81562.12345),'81,562.1');
 assert.equal(legendPrice(NaN),'缺样');assert.equal(legend({}).match(/缺样/g).length,6);
 assert.equal(html.match(/chart-ma-value/g).length,6);assert.match(html,/SMA 120/);assert.match(html,/EMA 120/);
 assert.deepEqual(row,original);assert.ok(html.includes(String(row.sma20)));
});

test('expanded chart mounts the existing panel in a modal and restores it without rebuilding candles',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),source=fs.readFileSync(require.resolve('../../yoyo/monitor/static/market-terminal.js'),'utf8');
 const classes=new Set(),bodyClasses=new Set(),attrs={};let focus=0,restored=null;
 const panel={classList:{contains:x=>classes.has(x),add:x=>classes.add(x),remove:x=>classes.delete(x)}};
 const button={setAttribute:(k,v)=>attrs[k]=v,focus(){focus++;}},evidence={};
 const dialog={open:false,append(node){this.child=node;},showModal(){this.open=true;},close(){this.open=false;}};
 const nodes={'.mt-chart-panel':panel,'.mt-chart-dialog':dialog,'.mt-evidence':evidence,'[data-chart-expand]':button};
 const ctx={active:true,$:selector=>nodes[selector],view:{insertBefore(node,anchor){restored={node,anchor};}},document:{body:{classList:{add:x=>bodyClasses.add(x),remove:x=>bodyClasses.delete(x)}}}};
 const api=vm.runInNewContext(source.slice(source.indexOf('  function restoreChartPanel(){'),source.indexOf('  function paintQuote(){'))+';({setChartExpanded,restoreChartPanel})',ctx);
 api.setChartExpanded(true);assert.equal(dialog.open,true);assert.equal(dialog.child,panel);assert.equal(bodyClasses.has('market-chart-open'),true);assert.equal(attrs['aria-label'],'收起行情图表');
 dialog.open=false;api.restoreChartPanel();assert.equal(restored.node,panel);assert.equal(restored.anchor,evidence);assert.equal(bodyClasses.size,0);assert.equal(attrs['aria-label'],'放大行情图表');assert.equal(focus,2);
 api.setChartExpanded(true);ctx.active=false;api.setChartExpanded(false);assert.equal(focus,3);assert.equal(dialog.open,false);assert.equal(bodyClasses.size,0);
});
