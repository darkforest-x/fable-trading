'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const icons=require('../../yoyo/monitor/static/coin-icons.js');
test('known artwork uses existing same-origin bytes; missing symbols never guess a remote logo',()=>{
  const root=path.resolve(__dirname,'../../yoyo/discipline_app/mobile/src/assets/coins');
  for(const file of fs.readdirSync(root)){
    const key=file.split('.')[0],digest=crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex').slice(0,20);
    assert.equal(icons.url(key),`/coin-assets/${file}?v=${digest}`);
    assert.equal(icons.url(key.toUpperCase()+'-USDT-SWAP'),icons.url(key));
  }
  assert.match(icons.avatar('LAYER-USDT-SWAP'),/data-coin-icon/);
  for(const symbol of ['DGAI','SHEIN','NOTLISTED','<img src=x>','../secret','constructor','__proto__']){
    assert.equal(icons.url(symbol),'');assert.doesNotMatch(icons.avatar(symbol),/<img\b/);
  }
  assert.match(icons.avatar('DGAI'),/>DG</);assert.match(icons.avatar('SHEIN'),/>SH</);
});
test('a transient image error retries once and successful loading restores visibility',()=>{
  const listeners={},timers=[],root={addEventListener:(name,fn)=>listeners[name]=fn};
  icons.bind(root,fn=>timers.push(fn));
  const img={matches:()=>true,isConnected:true,hidden:false,getAttribute:()=>'/coin-assets/layer.png'};
  listeners.error({target:img});assert.equal(img.hidden,true);assert.equal(timers.length,1);
  timers.shift()();assert.equal(img.src,'/coin-assets/layer.png?icon_retry=1');
  listeners.load({target:img});assert.equal(img.hidden,false);
  listeners.error({target:img});assert.equal(timers.length,0);
});
test('retired image nodes are not retried after page disposal',()=>{
  const listeners={},timers=[];icons.bind({addEventListener:(k,v)=>listeners[k]=v},fn=>timers.push(fn));
  const img={matches:()=>true,isConnected:false};listeners.error({target:img});timers[0]();assert.equal(img.src,undefined);
});
