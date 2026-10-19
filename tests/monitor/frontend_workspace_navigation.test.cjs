'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../yoyo/monitor/static/workspace-navigation.js'),'utf8');
function harness(hash='#langlang'){
 const events={},storage=new Map(),pushed=[],frames=[];
 const location={hash},window={scrollY:0,scrollTo({top}){window.scrollY=top;},addEventListener:(name,fn)=>events[name]=fn};
 const history={scrollRestoration:'auto',pushState(_,__,value){location.hash=value;pushed.push(value);},replaceState(_,__,value){location.hash=value;}};
 const document={documentElement:{scrollHeight:6000},getElementById:()=>({})};
 vm.runInNewContext(source,{window,document,history,location,innerHeight:800,sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},URLSearchParams,requestAnimationFrame:fn=>frames.push(fn),MutationObserver:class{observe(){}},setTimeout,clearTimeout});
 return {api:window.SpikeNavigation,window,location,events,pushed,flush(){while(frames.length)frames.shift()();}};
}
test('route and subpage history restore reading position and do not override user scrolling',()=>{
 const c=harness();let restored;
 c.api.register('langlang',p=>restored=p);c.api.begin('langlang',false);
 c.api.sync('langlang',{tab:'read',chapter:'c1'});c.api.settled();c.flush();c.window.scrollY=1300;
 c.api.begin('knowledge',true);c.api.settled();c.flush();assert.equal(c.window.scrollY,0);
 c.api.begin('langlang',true);c.api.restore('langlang');c.api.settled();c.flush();
 assert.equal(restored.chapter,'c1');assert.equal(c.window.scrollY,1300);
 c.api.sync('langlang',{tab:'read',chapter:'c2'});assert.equal(c.pushed.at(-1),'#langlang?tab=read&chapter=c2');
 c.location.hash='#langlang?tab=read&chapter=c1';c.api.begin('langlang',false);c.api.restore('langlang');assert.equal(restored.chapter,'c1');
 c.events.wheel();c.window.scrollY=100;c.api.settled();c.flush();assert.equal(c.window.scrollY,100);
});
test('initial direct links are restored without creating extra history entries',()=>{
 const c=harness('#langlang?tab=sessions&q=BTC&page=2');let restored;
 c.api.begin('langlang',false);c.api.register('langlang',p=>restored=p);
 assert.equal(restored.tab,'sessions');assert.equal(restored.q,'BTC');assert.equal(restored.page,'2');
 assert.equal(c.pushed.length,0);
});
test('switching study units within a source chapter creates a browser history step',()=>{
 const c=harness('#trading-books?tab=book&chapter=I&mode=study&unit=u1');
 c.api.begin('trading-books',false);c.api.sync('trading-books',{tab:'book',chapter:'I',mode:'study',unit:'u2'});
 assert.equal(c.pushed.length,1);assert.match(c.pushed[0],/unit=u2/);
});

test('detection menu opens latest while browser historical deep links stay explicit',()=>{
 const c=harness('#yolo-detect?tab=results&job=old&symbol=ETH');let restored;
 c.api.begin('yolo-detect',false);c.api.register('yolo-detect',p=>restored=p);assert.equal(restored.job,'old');
 c.api.begin('market',true);c.api.begin('yolo-detect',true);c.api.restore('yolo-detect');assert.equal(c.location.hash,'#yolo-detect');assert.equal(restored.job,undefined);
 c.location.hash='#yolo-detect?job=old';c.api.begin('yolo-detect',false);c.api.restore('yolo-detect');assert.equal(restored.job,'old');
});

test('signal pane and record layout changes each create a browser Back step',()=>{
 const c=harness('#signals?pane=records&layout=cards&side=short');
 c.api.begin('signals',false);c.api.sync('signals',{pane:'overview',layout:'history',side:'short'});
 assert.equal(c.pushed.length,1);assert.match(c.pushed[0],/pane=overview/);
 c.api.sync('signals',{pane:'records',layout:'cards',side:'short'});assert.equal(c.pushed.length,2);
 c.api.sync('signals',{pane:'records',layout:'history',side:'short'});assert.equal(c.pushed.length,3);
 c.api.sync('signals',{pane:'records',layout:'history',side:'long'});assert.equal(c.pushed.length,3);
});
