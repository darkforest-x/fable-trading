'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const engine=require('../../yoyo/monitor/static/transcript-search.js');
const sessions=[{id:'b',kind:'live',date:'2024-02-01'},{id:'a',kind:'clip',date:'2024-01-01'}];
const tx={segments:{b:[[1,'箱体 BTC'],[2,'向上'],[3,'ETH 反例']],a:[[1,'箱体 btc'],[2,'ETH'],[3,'<script> & .+']]}};
test('indexed context keeps central-segment semantics, chronology, kinds and literal terms',async()=>{
 const rows=engine.index(tx,sessions);
 const all=await engine.search(rows,'箱体 BTC','all');
 assert.deepEqual(all.hits.map(h=>h.id),['a','b']);assert.equal(all.total,2);
 assert.equal((await engine.search(rows,'箱体','clip')).total,1);
 assert.equal((await engine.search(rows,'箱体 ETH','all')).total,2);
 assert.equal((await engine.search(rows,'.+','all')).total,1);
 assert.equal((await engine.search(rows,'missing','all')).total,0);
 assert.equal((await engine.search(rows,'','all')).total,0);
});
test('paged results match the full ordered list without duplication and fallback yields preserve results',async()=>{
 const rows=engine.index({segments:{a:Array.from({length:240},(_,i)=>[i,'箱体 '+i])}},sessions);
 const all=await engine.search(rows,'箱体','all',0,1000), pages=[];
 for(let offset=0;offset<240;offset+=50){const page=await engine.search(rows,'箱体','all',offset,50,true);assert.equal(page.total,240);assert.ok(page.hits.length<=50);pages.push(...page.hits);}
 assert.deepEqual(pages,all.hits);
});
test('highlighting treats markup and regular expressions as text',()=>{
 assert.equal(engine.highlight('<script>& mark','script & mark'), '&lt;<mark>script</mark>&gt;<mark>&amp;</mark> <mark>mark</mark>');
 assert.equal(engine.highlight('a.+b','.+'),'a<mark>.+</mark>b');
});
test('a worker message failure falls back to the same indexed search',async()=>{
 const fs=require('node:fs'),vm=require('node:vm');const self={};
 class BrokenWorker {postMessage(message){if(message.type==='search')throw Error('worker unavailable');}}
 vm.runInNewContext(fs.readFileSync(require.resolve('../../yoyo/monitor/static/transcript-search.js'),'utf8'),{self,document:{currentScript:{src:'/static/transcript-search.js'}},Worker:BrokenWorker,setTimeout,clearTimeout});
 const result=await self.SpikeTranscriptSearch.create(tx,sessions).search('箱体','all');
 assert.equal(result.total,2);assert.equal(result.hits.length,2);
});
test('disposing a search client terminates the worker and rejects pending work without fallback',async()=>{
 const fs=require('node:fs'),vm=require('node:vm'),self={};let stopped=0;
 class Worker {postMessage(){}terminate(){stopped++;}}
 vm.runInNewContext(fs.readFileSync(require.resolve('../../yoyo/monitor/static/transcript-search.js'),'utf8'),{self,document:{currentScript:{src:'/static/transcript-search.js'}},Worker,setTimeout,clearTimeout});
 const client=self.SpikeTranscriptSearch.create(tx,sessions),pending=client.search('箱体','all');
 client.dispose();assert.equal(stopped,1);await assert.rejects(pending,{name:'AbortError'});await assert.rejects(client.search('箱体','all'),{name:'AbortError'});
});
