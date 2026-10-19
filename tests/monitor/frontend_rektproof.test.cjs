'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const core=require('../../yoyo/monitor/static/rektproof-core.js');
const good={kind:'trade',monday:'2026-10-12',coin:'BTC',high:'110',low:'100',side:'long',entry:'101',stop:'99',result:'0',checks:{}};

test('prices reject partial numbers, NaN, separators and empty values',()=>{
 for(const value of ['110abc','1,100','1e3','Infinity','NaN','',' ',null])assert.equal(core.number(value),null,value);
 for(const [value,n] of [['.2',.2],['-1',-1],['0',0],[' 110.50 ',110.5]])assert.equal(core.number(value),n);
});
test('planner checks Monday, geometry, direction and record semantics',()=>{
 assert.deepEqual(core.validatePlan(good),[]);
 for(const change of [{monday:'2026-10-13'},{monday:'2026-02-30'},{high:'100'},{high:'110abc'},{stop:'102'},{side:'short'},{entry:''},{result:'1R'},{kind:'plan'}])assert.ok(core.validatePlan({...good,...change}).length,JSON.stringify(change));
 assert.deepEqual(core.validatePlan({...good,kind:'no_trade',high:'',low:'',entry:'',stop:'',result:''}),[]);
});
test('long and short R use the actual entry-relative stop and fixed round-trip cost',()=>{
 const long=core.calculate(good);assert.equal(long.cost_r,.101);assert.equal(long.risk_pct,2/101);assert.ok(Math.abs(long.rFar-2.59)<1e-12);
 const short=core.calculate({...good,side:'short',entry:'109',stop:'111'});assert.equal(short.cost_r,.109);assert.ok(Math.abs(short.rFar-2.59)<1e-12);
 assert.equal(core.calculate({...good,high:'110abc'}),null);
 assert.equal(core.calculate({...good,stop:'102'}).risk_pct,undefined);
});
test('legacy migration preserves chapter notes and zero-R results without overwriting same-week trades',()=>{
 const old={chapters:{c1:{status:'reading',notes:'原笔记',stage:2}},plans:{'2026-10-12|BTC':good}};
 const a=core.normalize(old);assert.equal(a.chapters.c1.notes,'原笔记');assert.equal(Object.keys(a.plans).length,1);assert.equal(Object.values(a.plans)[0].kind,'trade');
 a.plans.second={...good,id:'second',side:'short',entry:'109',stop:'111'};
 const b=core.normalize(a,true);assert.equal(Object.keys(b.plans).length,2);assert.equal(b.plans.second.side,'short');
 assert.equal(core.normalize({plans:{old:{...good,result:0,kind:undefined}}}).plans['legacy-old'].kind,'trade');
});
test('import validates complete schema before mutation and merge preserves local conflicts',()=>{
 const current=core.normalize({chapters:{c1:{status:'reading',notes:'本机'}},plans:{x:{...good,id:'x'}}});
 for(const v of [null,[],{chapters:[]},{chapters:{bad:{status:'x'}}},{chapters:{},plans:{x:{...good,high:'bad'}}},{chapters:{},attempts:{a:{reason:'x'}}}])assert.throws(()=>core.normalize(v,true));
 const merged=core.merge(current,{chapters:{c1:{status:'done',notes:'导入'},c2:{status:'done',notes:'新章'}},plans:{x:{...good,id:'x',result:'5'},y:{...good,id:'y'}}});
 assert.equal(merged.chapters.c1.notes,'本机');assert.equal(merged.chapters.c2.notes,'新章');assert.equal(merged.plans.x.result,'0');assert.equal(Object.keys(merged.plans).length,2);
});
test('search highlights raw text without breaking entities or interpreting markup',()=>{
 assert.equal(core.highlight('A & B <img>', 'amp'),'A &amp; B &lt;img&gt;');
 assert.equal(core.highlight('A & B <img>', '<img>'),'A &amp; B <mark>&lt;img&gt;</mark>');
 assert.equal(core.highlight('A.*B','.*'),'A<mark>.*</mark>B');
});
test('replay price domain and visible bars are unchanged by any unrevealed future',()=>{
 const a=[{low:99,high:103},{low:98,high:104},{low:1,high:99999}];const b=[...a.slice(0,2),{low:-90000,high:900000}];
 assert.deepEqual(core.domain(a,2),core.domain(b,2));assert.deepEqual(core.visibleBars(a,2),a.slice(0,2));assert.deepEqual(core.visibleBars(a,-1),[]);assert.deepEqual(core.visibleBars(a,NaN),[]);
 assert.notDeepEqual(core.domain(a,3),core.domain(a,2));
});
function study(){const window={SpikeRektproofCore:core,addEventListener(){}};vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../yoyo/monitor/static/rektproof-study.js'),'utf8'),{window});return window.SpikeRektproofStudy;}
const context=()=>({tab:'replay',base:'/static/rektproof',esc:x=>String(x??'').replaceAll('<','&lt;'),store:core.normalize({}),data:{cases:[{id:'a',kind:'发布时序',replay_title:'中性名字',title:'有结局的标题',result:'SECRET_FINAL',limitations:'LIMIT',steps:[{at:'T1',title:'当前节点',text:'CURRENT',images:[],reference:'SECRET_REFERENCE'},{at:'T2',title:'SECRET_NEXT',text:'SECRET_FUTURE',images:[]}]}],candles:[]}});
test('message replay hides outcome, future headings and reference until complete',()=>{
 const c=context();const html=study().render(c);assert.match(html,/CURRENT/);assert.doesNotMatch(html,/SECRET_|有结局的标题/);
 c.store.replay.step=1;c.store.replay.completed=true;assert.match(study().render(c),/SECRET_FINAL/);
});
test('malformed imported replay cursor is clamped before any decision is recorded',()=>{
 const c=context();c.store.replay={mode:'messages',caseId:'a',step:-999};study().render(c);assert.equal(c.store.replay.step,0);
 c.store.replay.step=999;study().render(c);assert.equal(c.store.replay.step,1);
});
test('merge respects local deletion and prevents active/trash collisions',()=>{
 const a={chapters:{},plans:{},trash:{x:{...good,id:'x'}}};
 const b={chapters:{},plans:{x:{...good,id:'x'}},trash:{}};
 const m=core.merge(a,b);assert.equal(m.plans.x,undefined);assert.ok(m.trash.x);
 const n=core.merge(b,a);assert.ok(n.plans.x);assert.equal(n.trash.x,undefined);
});

test('recovered channel images retain their Discord source instead of a fabricated X post',()=>{
 const c=context();c.data.channel={messages:[{images:[{path:'images/channel.png',post_id:'123',source_kind:'discord',source_url:'https://discord.com/channels/1/2/123'}]}]};
 c.data.cases[0].steps[0].images=['images/channel.png'];
 const html=study().render(c);
 assert.match(html,/https:\/\/discord.com\/channels\/1\/2\/123/);
 assert.doesNotMatch(html,/x.com\/RektProof\/status\/123/);
 assert.doesNotMatch(html,/SECRET_FUTURE|SECRET_FINAL/);
});
test('a changed case sequence restarts safely while preserving prior answers and the draft',()=>{
 const c=context();c.data.cases[0].revision='new-evidence';
 c.store.replay={mode:'messages',caseId:'a',step:1,completed:true,reason:'unsaved reasoning'};
 c.store.attempts.old={mode:'messages',reason:'prior answer',saved_at:'T0',cutoff:'T1'};
 const html=study().render(c);
 assert.equal(c.store.replay.step,0);assert.equal(c.store.replay.completed,false);
 assert.equal(c.store.replay.reason,'unsaved reasoning');assert.equal(c.store.attempts.old.reason,'prior answer');
 assert.match(html,/案例补入了中间消息/);assert.doesNotMatch(html,/SECRET_FUTURE|SECRET_FINAL/);
 c.store.replay.step=1;c.store.replay.completed=true;
 assert.match(study().render(c),/SECRET_FINAL/);
});

test('public source search combines topic, year and case-insensitive raw-text terms',()=>{
 const threads=[{id:'a',topic:'形态与确认',title:'做多延续',posts:[{published_at:'2026-08-19',text:'Bullish Continuation'}]},
 {id:'b',topic:'账户日志',title:'2021 日志',posts:[{text:'A BTC loss'}]}];
 const api=study();
 assert.equal(api.filterSources(threads,'2026 BULLISH','形态与确认')[0].id,'a');
 assert.equal(api.filterSources(threads,'2026','账户日志').length,0);
 assert.equal(api.filterSources(threads,'BTC loss')[0].id,'b');
 assert.equal(api.filterSources(threads,'   ').length,2);
 assert.equal(api.filterSources(threads,'missing').length,0);
});

test('reused original image links to its current node, not its earliest archive reference',()=>{
 const c=context();c.data.threads=[{images:[{path:'images/reused.png',post_id:'old'}]}];
 c.data.cases[0].steps[0].images=['images/reused.png'];
 c.data.cases[0].steps[0].source='https://x.com/RektProof/status/current';
 const html=study().render(c);
 assert.match(html,/https:\/\/x.com\/RektProof\/status\/current/);
 assert.doesNotMatch(html,/https:\/\/x.com\/RektProof\/status\/old/);
});

test('source gallery retains text-only updates and reports selected media coverage',()=>{
 const c=context();c.data.threads=[{id:'a',title:'教学',topic:'形态与确认',url:'https://x.com/RektProof/status/1',images:[],posts:[{id:'1',url:'https://x.com/RektProof/status/1',published_at:'2026-01-01T00:00:00Z',images:[],media_total:2}]}];
 const html=study().gallery(c);
 assert.match(html,/1 组 · 1 条推文/);assert.match(html,/本地选存 0 \/ 2 张图/);
 assert.match(html,/阅读该条原文/);assert.match(html,/rp-source-query/);
});
