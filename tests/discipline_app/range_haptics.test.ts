import {test} from 'node:test';
import assert from 'node:assert/strict';
import {rangeImpact} from '../../yoyo/discipline_app/mobile/src/rangeHaptics.ts';
test('allocation has medium detents, stronger endpoints and no repeated stationary impact',()=>{
 assert.equal(rangeImpact(48,50,100),'medium');assert.equal(rangeImpact(99,100,60),'heavy');
 assert.equal(rangeImpact(1,0,60),'heavy');assert.equal(rangeImpact(100,100,1000),null);
 assert.equal(rangeImpact(52,53,1000),null);assert.equal(rangeImpact(50,49,100),'medium');
 assert.equal(rangeImpact(45,50,30),null);assert.equal(rangeImpact(99,100,20),null);
 assert.equal(rangeImpact(NaN,50,1000),null);
});
test('a fast drag cannot flood the native bridge but still lands with an endpoint impact',()=>{
 let count=0,last=-Infinity,kind:string|null=null;
 for(let i=1;i<=100;i++){const at=i*10;const result=rangeImpact(i-1,i,at-last);if(result){count++;last=at;kind=result}}
 assert.ok(count<=12);assert.equal(kind,'heavy');
});
