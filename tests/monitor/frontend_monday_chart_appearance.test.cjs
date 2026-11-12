'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const appearance=require('../../yoyo/monitor/static/monday-chart-appearance.js');
const settings=require('../../yoyo/monitor/static/monday-chart-settings.js');
const candleColors=require('../../yoyo/monitor/static/cloud-candle-colors.js');

test('appearance defaults keep original candle colors off and resolve four lines by theme',()=>{
 const value=appearance.normalize();
 assert.equal(appearance.KEY,'spike.monday-chart-appearance.v1');
 assert.notEqual(appearance.KEY,candleColors.KEY);
 assert.equal(value.candleEnabled,false);assert.equal(value.monthlyEnabled,true);
 assert.deepEqual(value.candleColors,{up:'#8B9099',down:'#8B9099',monday:'#30D158',weekend:'#8B9099'});
 assert.deepEqual(value.lines,{
  '周一高':{color:null,width:1,style:'solid'},'周一低':{color:null,width:1,style:'solid'},
  '上 1.618':{color:null,width:1,style:'solid'},'下 1.618':{color:null,width:1,style:'solid'}
 });
 for(const label of ['周一高','周一低'])assert.equal(appearance.defaultsForTheme('light')[label],'#9399A2');
 for(const label of ['上 1.618','下 1.618']){
  assert.equal(appearance.defaultsForTheme('light')[label],'#17191D');
  assert.equal(appearance.defaultsForTheme('dark')[label],'#E3E7EC');
 }
});

test('normalization bounds colors, widths and line styles and ignores unknown fields',()=>{
 const value=appearance.normalize({candleEnabled:1,candleColors:{up:'#abc',down:'url(x)',monday:'#12abEF',extra:'#000000'},lines:{
  '周一高':{color:'#fff',width:3,style:'dot'},'周一低':{color:'invalid',width:99,style:'dashed'},
  '上 1.618':{color:null,width:2,style:'dash'},'下 1.618':{color:'red',width:1,style:'solid'},unknown:{color:'#000000'}
 },extra:true});
 assert.equal(value.candleEnabled,false);
 assert.equal(value.monthlyEnabled,true);
 assert.deepEqual(value.candleColors,{up:'#AABBCC',down:'#8B9099',monday:'#12ABEF',weekend:'#8B9099'});
 assert.deepEqual(value.lines,{
  '周一高':{color:'#FFFFFF',width:3,style:'dot'},'周一低':{color:null,width:1,style:'solid'},
  '上 1.618':{color:null,width:2,style:'dash'},'下 1.618':{color:null,width:1,style:'solid'}
 });
});

test('appearance reads and writes only its own local-storage key and recovers malformed data',()=>{
 const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
 const saved=appearance.normalize({candleEnabled:true,lines:{'上 1.618':{color:'#12abef',width:2,style:'dot'}}});
 assert.equal(appearance.write(storage,saved),true);
 assert.equal(values.has(appearance.KEY),true);assert.equal(values.has(candleColors.KEY),false);
 assert.deepEqual(appearance.read(storage),saved);
 values.set(appearance.KEY,'{bad');assert.deepEqual(appearance.read(storage),appearance.normalize());
 assert.deepEqual(appearance.read({getItem(){throw Error('blocked');}}),appearance.normalize());
 assert.equal(appearance.write({setItem(){throw Error('blocked');}},saved),false);
});
test('legacy gray migration retains disabled state and custom lines, then respects an intentional gray save',()=>{
 const old=appearance.normalize({lines:{'上 1.618':{color:'#123456',style:'dot',width:3}}});
 old.candleColors.monday='#8B9099';let saved=JSON.stringify(old);
 const storage={getItem:()=>saved,setItem:(_,v)=>{saved=v;}};
 const migrated=appearance.read(storage);
 assert.equal(migrated.candleEnabled,false);assert.equal(migrated.candleColors.monday,'#30D158');
 assert.deepEqual(migrated.lines,old.lines);assert.equal(JSON.parse(saved).paletteVersion,2);
 appearance.write(storage,old);assert.deepEqual(appearance.read(storage),old);
 old.candleColors.weekend='#123456';saved=JSON.stringify(old);
 assert.deepEqual(appearance.read(storage),old);
});

test('line appearance maps only four Monday labels and retains history and chart data',()=>{
 const other={label:'上 0.618',price:12,color:'#408DDD'},levels=[
  {label:'上 1.618',price:14,kind:'fib',historical:true,inlinePriceVisible:true,start_ms:1,end_ms:2},
  {label:'周一高',price:13,kind:'range',historical:false,start_ms:2,end_ms:3},other,
  {label:'周一低',price:11,kind:'range',historical:true,start_ms:3,end_ms:4},
  {label:'下 1.618',price:10,kind:'fib',historical:false,start_ms:4,end_ms:5}
 ];
 const before=JSON.stringify(levels),value=appearance.normalize({lines:{'上 1.618':{color:'#ABCDEF',width:3,style:'dot'},'周一低':{color:null,width:2,style:'dash'}}});
 const light=appearance.applyLevels(levels,value,'light'),dark=appearance.applyLevels(levels,value,'dark');
 assert.deepEqual(light.map(level=>level.label),levels.map(level=>level.label));
 assert.deepEqual([light[0].color,light[0].lineWidth,light[0].lineStyle,light[0].startMarkerVisible],['#ABCDEF',3,1,false]);
 assert.deepEqual([light[1].color,light[1].lineWidth,light[1].lineStyle,light[1].startMarkerVisible],['#9399A2',1,0,false]);
 assert.deepEqual([light[3].color,light[3].lineWidth,light[3].lineStyle],['#9399A2',2,2]);
 assert.equal(light[0].historical,true);assert.equal(light[0].inlinePriceVisible,true);assert.equal(light[0].start_ms,1);assert.equal(light[0].end_ms,2);
 assert.equal(dark[0].color,'#ABCDEF','custom colors survive a theme change');assert.equal(dark[4].color,'#E3E7EC');
 assert.equal(light[2],other,'non-Monday levels retain their original objects');assert.equal(JSON.stringify(levels),before,'appearance does not mutate its input');
});

test('custom candle callback is absent while disabled and shares cloud calendar categories when enabled',()=>{
 const disabled=appearance.normalize(),helper={colorizer:candleColors.colorizer};
 assert.equal(appearance.candleColorizer(disabled,helper),null);
 const enabled=appearance.normalize({candleEnabled:true,candleColors:{up:'#112233',down:'#445566',monday:'#778899',weekend:'#AABBCC'}});
 const color=appearance.candleColorizer(enabled,helper);
 assert.equal(color({time:Date.parse('2026-11-02T17:00:00Z')/1000,open:1,close:2}),'#778899');
 assert.equal(color({time:Date.parse('2026-11-01T17:00:00Z')/1000,open:1,close:2}),'#AABBCC');
 assert.equal(color({time:Date.parse('2026-11-03T17:00:00Z')/1000,open:1,close:2}),'#112233');
 assert.equal(color({time:Date.parse('2026-11-03T17:00:00Z')/1000,open:2,close:1}),'#445566','up/down palettes remain separately configurable');
});

test('settings panel offers three tabs and keeps existing indicators and drawing controls',()=>{
 const markup=settings.html('<div data-cd-tool="trend">绘图</div>');
 for(const label of ['>指标<','>K线<','>区间线<'])assert.match(markup,new RegExp(label));
 assert.match(markup,/data-rp-action="ma"/);assert.match(markup,/data-rp-action="internal"/);
 assert.match(markup,/data-cd-tool="trend"/);
 assert.match(markup,/<span>周一<\/span>/);assert.doesNotMatch(markup,/图表指标|绘图保存在本浏览器|关闭时使用原配色/);assert.doesNotMatch(markup,/默认保留红涨绿跌/);
 assert.match(markup,/data-mcs-candle-enabled/);assert.match(markup,/data-mcs-line-color="上 1\.618"/);
 assert.match(markup,/data-mcs-width="下 1\.618"/);assert.match(markup,/data-mcs-style="周一高"/);
 assert.match(markup,/data-mcs-monthly-enabled/);
});

test('Windows TV link is downloadable and is omitted on other platforms',()=>{
 const windows=settings.html('', 'Windows'),mac=settings.html('', 'MacIntel');
 assert.match(windows,/<a class="mcs-windows-link" href="\/static\/spike-chart-link-windows\.zip" download/);
 assert.match(windows,/安装 \/ 更新本机 TV 入口/);
 assert.doesNotMatch(mac,/spike-chart-link-windows\.zip/);
});

test('monthly overlay setting defaults on, can be toggled from the panel, and persists across reload',()=>{
 const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)},listeners=new Map();
 const monthlyToggle={attrs:{},dataset:{},setAttribute(key,value){this.attrs[key]=value;},hasAttribute:name=>name==='data-mcs-monthly-enabled'};
 const panel={querySelector:selector=>selector==='[data-mcs-monthly-enabled]'?monthlyToggle:null,querySelectorAll:()=>[],
  addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),contains:()=>true};
 const changes=[],controller=settings.create({panel,storage,document:null,onChange:value=>changes.push(value)});
 assert.equal(controller.getState().monthlyEnabled,true);assert.equal(monthlyToggle.attrs['aria-checked'],'true');assert.equal(controller.activeCount(),1);
 listeners.get('click')({target:{closest:()=>monthlyToggle}});
 assert.equal(controller.getState().monthlyEnabled,false);assert.equal(monthlyToggle.attrs['aria-checked'],'false');
 assert.equal(JSON.parse(values.get(appearance.KEY)).monthlyEnabled,false);assert.equal(changes.at(-1).monthlyEnabled,false);assert.equal(controller.activeCount(),0);
 controller.destroy();
 const restored=settings.create({panel,storage,document:null});
 assert.equal(restored.getState().monthlyEnabled,false);assert.equal(monthlyToggle.attrs['aria-checked'],'false');restored.destroy();
});

test('settings tabs support arrow and Home/End navigation and remove listeners on destroy',()=>{
 const tabs=['indicators','candles','lines'].map(mcsTab=>({dataset:{mcsTab},attributes:{},focusCount:0,setAttribute(key,value){this.attributes[key]=value;},focus(){this.focusCount++;}}));
 const sections=['indicators','candles','lines'].map(mcsContent=>({dataset:{mcsContent},hidden:false})),listeners=new Map();
 const panel={querySelectorAll(selector){if(selector==='[data-mcs-tab]')return tabs;if(selector==='[data-mcs-content]')return sections;return [];},
  querySelector(){return null;},addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),contains:()=>true};
 const controller=settings.create({panel,storage:{getItem:()=>null,setItem(){}},document:null});
 const press=(index,key)=>{let prevented=false;listeners.get('keydown')({target:{closest:()=>tabs[index]},key,preventDefault(){prevented=true;}});return prevented;};
 assert.equal(tabs[0].tabIndex,0);assert.equal(tabs[1].tabIndex,-1);
 assert.equal(press(0,'ArrowRight'),true);assert.equal(tabs[1].attributes['aria-selected'],'true');assert.equal(tabs[1].focusCount,1);
 assert.equal(press(1,'ArrowLeft'),true);assert.equal(tabs[0].attributes['aria-selected'],'true');
 assert.equal(press(2,'Home'),true);assert.equal(tabs[0].focusCount,2);
 assert.equal(press(0,'End'),true);assert.equal(tabs[2].attributes['aria-selected'],'true');assert.equal(tabs[2].focusCount,1);
 assert.equal(press(2,'Escape'),false);controller.destroy();assert.equal(listeners.has('keydown'),false);
});
