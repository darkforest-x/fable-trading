// Minimal DOM logic check; this is not a browser or a screenshot test.
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const file=process.argv[2], html=fs.readFileSync(file,'utf8');
const match=html.match(/<script id="audit-payload" type="application\/json">([\s\S]*?)<\/script>/);
assert(match,'embedded payload');
const payload=JSON.parse(match[1]), dom={};
const element=id=>dom[id]||(dom[id]={innerHTML:'',textContent:'',dataset:{},querySelectorAll:()=>[]});
element('audit-payload').textContent=match[1];
const document={getElementById:element,querySelectorAll:()=>[]};
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length,1);
vm.runInNewContext(scripts[0][1],{document,console},{timeout:10000});
let count=0;
for(const [arm,rows] of Object.entries(payload.trades)){
 element('arm').onchange({target:{value:arm}});
 for(let i=0;i<rows.length;i++){
  assert.equal(element('counter').textContent,(i+1)+' / '+rows.length+' 笔');
  const svg=element('chart').innerHTML;
  assert(svg.includes('class="bar"'),'missing candles');
  assert(!/NaN|Infinity|undefined/.test(svg),'nonfinite chart');
  assert.equal((element('rows').innerHTML.match(/data-i=/g)||[]).length,rows.length);
  if(arm==='original') assert(svg.includes('实际有效止损路径'));
  assert(element('detail').innerHTML.includes('退出'));
  if(i+1<rows.length)element('next').onclick();
  count++;
 }
}
assert.equal((element('signals').innerHTML.match(/<tr>/g)||[]).length,payload.signals.length);
console.log(JSON.stringify({charts_checked:count,signals:payload.signals.length,all_arm_navigation:true,finite_svg:true,kind:'minimal DOM logic check; no browser visual QA'}));
