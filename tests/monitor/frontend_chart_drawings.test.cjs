'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const D=require('../../yoyo/monitor/static/chart-drawings.js');
const rows=Array.from({length:8},(_,i)=>({time:1000+i*60,volume:i*10,confirmed:true}));
test('measure preserves direction, exact time and inclusive contract volume in either time direction',()=>{
 const index=new D.DataIndex(rows,60),a={time:1060,price:100},b={time:1240,price:120};
 const m=index.measure(a,b);assert.equal(m.delta,20);assert.equal(m.percent,20);assert.equal(m.bars,3);assert.equal(m.count,4);assert.equal(m.seconds,180);assert.equal(m.volume,100);assert.equal(m.complete,true);
 const reverse=index.measure(b,a);assert.equal(reverse.delta,-20);assert.ok(Math.abs(reverse.percent+100/6)<1e-12);assert.equal(reverse.volume,100);assert.equal(reverse.seconds,180);
 assert.equal(index.measure({time:1000,price:1},{time:1000,price:2}).volume,0);
});
test('missing bars, unavailable volume and partial loaded coverage cannot masquerade as a complete interval',()=>{
 const a={time:1000,price:100},b={time:1240,price:90};
 const gap=new D.DataIndex(rows.filter((_,i)=>i!==2),60).measure(a,b);assert.equal(gap.complete,false);assert.equal(gap.count,4);assert.equal(gap.volume,80);
 const invalid=new D.DataIndex(rows.map((r,i)=>i===2?{...r,volume:null}:r),60).measure(a,b);assert.equal(invalid.volume,null);
 const outside=new D.DataIndex(rows,60).measure({time:400,price:100},b);assert.equal(outside.complete,false);
 const empty=new D.DataIndex([],60).measure(a,b);assert.equal(empty.volume,null);assert.equal(empty.complete,false);assert.equal(empty.count,0);
 assert.match(D.measurementText(gap)[3],/覆盖不完整/);assert.match(D.measurementText(invalid)[2],/—/);
});
test('stored timestamp anchors survive prepend, rolling history and timeframe changes without index drift',()=>{
 const full=new D.DataIndex(rows,60),rolled=new D.DataIndex(rows.slice(2),60),prepended=new D.DataIndex([{time:940,volume:10},...rows],60);
 for(const time of [1000,1030,1240,1540]){const logical=full.logical(time);assert.equal(full.timeAt(logical),time);assert.equal(rolled.logical(time),logical-2);assert.equal(prepended.logical(time),logical+1);}
 const coarse=new D.DataIndex([{time:1000,volume:10},{time:1300,volume:20},{time:1600,volume:30}],300);
 assert.equal(coarse.logical(1120),.4);assert.equal(coarse.timeAt(.4),1120);
 assert.equal(coarse.measure({time:1120,price:100},{time:1480,price:110}).complete,false);
});
test('a live last candle stays explicitly provisional, with volume recalculated on receipt',()=>{
 const a={time:1360,price:100},b={time:1420,price:101};
 const live=rows.map((r,i)=>i===7?{...r,confirmed:false,volume:80}:r);
 const m=new D.DataIndex(live,60).measure(a,b);assert.equal(m.volume,140);assert.equal(m.live,true);assert.match(D.measurementText(m)[3],/未收盘/);
});
test('channel boundaries stay parallel after screen-coordinate reprojection, including reversed baselines',()=>{
 for(const points of [[{x:10,y:50},{x:110,y:10},{x:60,y:10}],[{x:110,y:10},{x:10,y:50},{x:60,y:60}]]){
  const q=D.channelPoints(points);assert.equal(q[1].y-q[0].y,q[2].y-q[3].y);assert.equal(q[3].y-q[0].y,q[2].y-q[1].y);
  assert.equal(D.distance(points[2],q[3],q[2]),0);
 }
 assert.equal(D.channelPoints([{x:2,y:2},{x:2,y:10},{x:3,y:5}]),null);
});
test('local storage accepts only bounded well-formed price/time anchors and never silently replaces invalid records',()=>{
 const item={id:'1',type:'channel',points:[{time:1000,price:100},{time:1060,price:101},{time:1060,price:105}]};
 assert.deepEqual(D.decode(JSON.stringify({version:1,items:[item]})),[item]);assert.deepEqual(D.decode(null),[]);
 for(const data of [{version:2,items:[]},{version:1,items:[item,item]},{version:1,items:[{...item,type:'execute'}]},{version:1,items:[{...item,points:[{time:1000,price:0}]}]},{version:1,items:[{...item,points:[item.points[0],item.points[0],item.points[2]]}]}])assert.throws(()=>D.decode(JSON.stringify(data)));
 assert.throws(()=>D.decode('bad json'));assert.throws(()=>D.decode(JSON.stringify({version:1,items:Array.from({length:D.MAX+1},(_,i)=>({...item,id:String(i)}))})));
});
test('duration and sub-cent price differences remain meaningful without currency or tick-size guesses',()=>{
 assert.equal(D.duration(1555200),'18 天');assert.equal(D.duration(3660),'1 小时 1 分钟');assert.equal(D.duration(0),'0 分钟');
 const text=D.measurementText({delta:.00000002,percent:2,seconds:900,bars:1,count:2,volume:0,complete:true});assert.match(text[0],/0\.00000002/);assert.match(text[2],/0 张/);
});

test('historical Binance volume is labelled in base units rather than OKX contracts',()=>{
 const m={delta:1,percent:1,seconds:300,bars:1,count:2,volume:100,complete:true};
 assert.match(D.measurementText(m,'基础币')[2],/100 基础币/);
 assert.match(D.measurementText(m)[2],/100 张/);
});
