'use strict';
// User preference is binary and applied before render, independently of market state.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('yoyo/monitor/static/theme.js','utf8');
function page({stored=null,blocked=false,design='apple-glass'}={}){
 const attrs={},meta={},docEvents={},winEvents={};
 const document={documentElement:{dataset:{design},setAttribute:(k,v)=>{attrs[k]=v}},querySelector:()=>({setAttribute:(k,v)=>{meta[k]=v}}),addEventListener:(k,fn)=>{docEvents[k]=fn}};
 const window={localStorage:{getItem(){if(blocked)throw Error('blocked');return stored},setItem(k,v){assert.equal(k,'spike.theme');if(blocked)throw Error('blocked');stored=v}},addEventListener:(k,fn)=>{winEvents[k]=fn},matchMedia(){throw Error('Binary theme must not depend on OS appearance')}};
 vm.runInNewContext(source,{document,window});
 return {attrs,meta,stored:()=>stored,toggle:()=>window.SpikeTheme.toggle(),click(){docEvents.click({target:{closest:()=>({disabled:false})}})},otherTab(value,key='spike.theme'){stored=value;winEvents.storage({key})}};
}
test('first visit keeps light appearance and explicit dark applies before render',()=>{
 assert.equal(page().attrs['data-theme'],'light');
 const p=page({stored:'dark'});assert.equal(p.attrs['data-theme'],'dark');assert.equal(p.meta.content,'#101318');
});
test('one icon action switches only light/dark and survives reload',()=>{
 const p=page();p.click();assert.equal(p.attrs['data-theme'],'dark');assert.equal(p.stored(),'dark');
 assert.equal(page({stored:p.stored()}).attrs['data-theme'],'dark');p.click();assert.equal(p.stored(),'light');
});
test('legacy system and corrupt values safely resolve to light',()=>{
 for(const stored of ['system','invalid','',null])assert.equal(page({stored}).attrs['data-theme'],'light');
});
test('restricted storage cannot prevent startup or switching',()=>{
 const p=page({blocked:true});p.toggle();assert.equal(p.attrs['data-theme'],'dark');p.toggle();assert.equal(p.attrs['data-theme'],'light');
});
test('same-origin tabs synchronize explicit choices; clearing returns light',()=>{
 const p=page({stored:'dark'});p.otherTab('light');assert.equal(p.attrs['data-theme'],'light');p.otherTab('dark');assert.equal(p.attrs['data-theme'],'dark');p.otherTab(null,null);assert.equal(p.attrs['data-theme'],'light');
});
test('appearance is no longer locked by a design identity',()=>{
 for(const design of ['apple-glass','stitch-obsidian',undefined]){const p=page({design});p.toggle();assert.equal(p.attrs['data-theme'],'dark');p.toggle();assert.equal(p.attrs['data-theme'],'light')}
});
test('standalone research services ship the same binary controller',()=>{
 for(const dir of ['yoyo/rotation/web','yoyo/vision_research/static']){
  assert.equal(fs.readFileSync(dir+'/theme.js','utf8'),source);
  assert.equal(fs.readFileSync(dir+'/theme-toggle.css','utf8'),fs.readFileSync('yoyo/monitor/static/theme-toggle.css','utf8'));
 }
});
