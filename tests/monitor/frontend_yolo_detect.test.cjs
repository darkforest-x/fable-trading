'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../yoyo/monitor/static/yolo-detect.js'),'utf8');
// Exercise the shipped primitive with the bundled 4.2 integer-only scale behavior.
const code=source.slice(source.indexOf('class DetectionPrimitive{'),source.indexOf('  function destroyChart'));
const Primitive=vm.runInNewContext(code+';DetectionPrimitive');
function primitive(){
 const p=new Primitive([],100000,1000);
 const scale={logicalToCoordinate:i=>Number.isInteger(i)?30+8*i:0,width:()=>1000};
 p.attached({chart:{timeScale:()=>scale},series:{priceToCoordinate:p=>500-p*10},requestUpdate:()=>{}});return p;
}
test('predicted sub-bar boundaries retain nonzero width instead of collapsing to x=0',()=>{
 const p=primitive(),r=p.rect({x_start_ms:102250,x_end_ms:106750,price_high:30,price_low:20});
 assert.deepEqual(Array.from(r),[48,200,84,300]);
});
test('sub-bar extrapolation preserves geometry outside the viewport',()=>{
 const p=primitive();assert.equal(p.x(99400),25.2);assert.equal(p.x(100000),30);
});
