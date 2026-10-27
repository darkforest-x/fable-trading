'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ui=require('../../yoyo/monitor/static/reclaim-strategy-book.js');
const data=require('../../yoyo/monitor/static/strategy-booklet/research.json');
const paper=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
test('all seven accounts have separate selection and accessible booklet buttons',()=>{
 const html=ui.pickerHTML(paper.TRACKS);
 for(const t of paper.TRACKS){assert.ok(html.includes(`data-strategy-select="${t}"`));assert.ok(html.includes(`data-strategy-book="${t}"`));assert.ok(data.accounts[t]);assert.doesNotThrow(()=>ui.researchHTML(data,t));}
 assert.equal((html.match(/<button /g)||[]).length,14);
 for(const match of html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g))assert.doesNotMatch(match[1],/<button/);
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
