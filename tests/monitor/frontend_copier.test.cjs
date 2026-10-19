"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("yoyo/monitor/static/copier.js", "utf8");
const flush = async () => { for (let i=0;i<30;i++) await Promise.resolve(); };

test("changing a filter during a request discards old messages and requests the current filter", async () => {
  const listeners={}, root={innerHTML:"",querySelectorAll:()=>[]}, window={}, pending=[];
  const focus={total:0,intents:{},counts:{},reasons:[],author_performance:{}};
  const paper={accounts:[],r_stats_all:{},open:[],pending:[],closed:[]};
  vm.runInNewContext(source,{window,document:{hidden:false,getElementById:()=>root,
    addEventListener:(event,fn)=>listeners[event]=fn},URL,URLSearchParams,Date,Intl,Number,Set,
    clearInterval(){},setInterval(){},fetch:async url=>({ok:true,json:()=>url.includes('messages?')?
      new Promise(resolve=>pending.push({url,resolve})):Promise.resolve(url.includes('monitor/focus')?focus:paper)})});
  window.SpikeCopier.setActive(true); await flush();
  listeners.click({target:{closest:()=>({dataset:{tab:'signals'}})}});
  listeners.change({target:{id:'copier-intent',value:'open'}});
  pending[0].resolve({items:[{id:1,intent:'noise',content:'STALE_ROW'}],total:1});
  await flush();
  assert.equal(pending.length,2);
  assert.match(pending[1].url,/intent=open/);
  assert.doesNotMatch(root.innerHTML,/STALE_ROW/);
  pending[1].resolve({items:[{id:2,intent:'open',content:'CURRENT_ROW'}],total:1});
  await flush();
  assert.match(root.innerHTML,/CURRENT_ROW/);
  assert.doesNotMatch(root.innerHTML,/STALE_ROW/);
  assert.match(root.innerHTML,/按入库时间倒序/);
});
