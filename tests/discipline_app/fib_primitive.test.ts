import test from 'node:test';
import assert from 'node:assert/strict';
import {FibRangePrimitive} from '../../yoyo/discipline_app/mobile/src/FibRangePrimitive.ts';

function chart(format?:(price:number)=>string){
 const viewport={scale:2,from:90,to:210};
 const segments:number[][]=[],labels:string[]=[],dashes:number[][]=[];
 const primitive=new FibRangePrimitive({high:110,low:90,upper:122.36,lower:77.64,start:100,end:200},format);
 const bars=[90,110,130,150,170,190,210].map(time=>({time}));
 primitive.attached({series:{priceToCoordinate:(price:number)=>300-price*viewport.scale,data:()=>bars},chart:{timeScale:()=>({getVisibleRange:()=>({from:viewport.from,to:viewport.to}),timeToCoordinate:(stamp:number)=>bars.some(b=>b.time===stamp)?stamp-viewport.from:null})},requestUpdate:()=>{}} as never);
 let start:number[]=[];
 const context={save(){},restore(){},setLineDash(dash:number[]){dashes.push(dash)},beginPath(){},moveTo(...p:number[]){start=p},lineTo(...p:number[]){segments.push([...start,...p])},stroke(){},measureText(){return {width:30}},fillText(text:string){labels.push(text)},fillRect(){throw Error('Range or label background is forbidden')}};
 const paint=()=>primitive.paneViews()[0].renderer()!.draw({useMediaCoordinateSpace:(fn:(scope:unknown)=>void)=>fn({context,mediaSize:{width:400,height:600}})} as never);
 return {viewport,primitive,segments,labels,dashes,paint};
}

test('solid levels start at the exact close between native bar timestamps; text has no boxes',()=>{
 const {paint,segments,labels,dashes}=chart();paint();
 assert.equal(segments.length,5);
 for(const [x1,y1,x2,y2] of segments){assert.equal(x1,10);assert.equal(x2,110);assert.equal(y1,y2)}
 assert.deepEqual(dashes,[[]]);
 assert.deepEqual(labels,['上 1.618 122.36','高点 110','低点 90','下 1.618 77.64']);
});

test('pan and scale update native coordinates; detachment stops drawing',()=>{
 const {paint,segments,viewport,primitive}=chart();paint();
 viewport.scale=1;viewport.from=120;viewport.to=180;paint();
 assert.equal(segments.length,10);assert.deepEqual(segments[6],[0,190,400,190]);
 assert.deepEqual(primitive.autoscaleInfo()?.priceRange,{minValue:77.64,maxValue:122.36});
 primitive.detached();paint();assert.equal(segments.length,10);
});

test('no levels or forced price scale before Monday close or after the week',()=>{
 const {paint,segments,viewport,primitive}=chart();viewport.to=95;paint();
 assert.equal(segments.length,0);assert.equal(primitive.autoscaleInfo(),null);
 viewport.from=205;viewport.to=250;paint();assert.equal(segments.length,0);assert.equal(primitive.autoscaleInfo(),null);
});

test('all four visible labels use the provided symbol price precision',()=>{
 const {paint,labels}=chart(price=>price.toFixed(7));paint();
 assert.deepEqual(labels,['上 1.618 122.3600000','高点 110.0000000','低点 90.0000000','下 1.618 77.6400000']);
});
