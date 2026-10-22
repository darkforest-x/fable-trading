import test from 'node:test';
import assert from 'node:assert/strict';
import {FibRangePrimitive} from '../../yoyo/discipline_app/mobile/src/FibRangePrimitive.ts';
test('range follows native coordinates on pan/scale and stops after detachment',()=>{
 let scale=2,from=90,to=210;const drawn:number[][]=[];
 const primitive=new FibRangePrimitive({high:110,low:90,upper:122.36,lower:77.64,start:100,end:200});
 primitive.attached({series:{priceToCoordinate:(price:number)=>300-price*scale},chart:{timeScale:()=>({getVisibleRange:()=>({from,to}),timeToCoordinate:(stamp:number)=>stamp-from})},requestUpdate:()=>{}} as never);
 const paint=()=>primitive.paneViews()[0].renderer()!.draw({useMediaCoordinateSpace:(fn:(scope:unknown)=>void)=>fn({context:{fillRect:(...rect:number[])=>drawn.push(rect)},mediaSize:{width:400,height:600}})} as never);
 paint();assert.deepEqual(drawn.at(-1),[10,80,100,40]);
 scale=1;from=120;to=180;paint();assert.deepEqual(drawn.at(-1),[0,190,400,20]);
 assert.deepEqual(primitive.autoscaleInfo().priceRange,{minValue:77.64,maxValue:122.36});
 primitive.detached();paint();assert.equal(drawn.length,2);
});
