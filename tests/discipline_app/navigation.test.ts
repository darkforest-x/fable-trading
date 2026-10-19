import test from 'node:test';
import assert from 'node:assert/strict';
import {swipeDestination} from '../../yoyo/discipline_app/mobile/src/navigation.ts';
test('horizontal swipes follow four tabs and do not wrap or leave settings',()=>{
 assert.equal(swipeDestination('合约',-100,10,200),'纪律');
 assert.equal(swipeDestination('纪律',-100,0,200),'持仓');
 assert.equal(swipeDestination('持仓',-100,0,200),'统计');
 assert.equal(swipeDestination('持仓',100,0,200),'纪律');
 for(const [tab,dx] of [['合约',100],['统计',-100],['设置',-100]] as const)assert.equal(swipeDestination(tab,dx,0,200),null);
});
test('vertical scrolling, tiny movements and long drags cannot change tabs',()=>{
 for(const [x,y,t] of [[30,0,200],[80,100,200],[100,0,701]])assert.equal(swipeDestination('合约',x,y,t),null);
});
