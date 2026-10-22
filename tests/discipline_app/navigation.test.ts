import test from 'node:test';
import assert from 'node:assert/strict';
import {swipeDestination,swipeAxis,swipeOffset} from '../../yoyo/discipline_app/mobile/src/navigation.ts';
test('horizontal swipes follow five tabs and do not wrap or leave settings',()=>{
 assert.equal(swipeDestination('1.618',-100,10,200),'持仓');
 assert.equal(swipeDestination('1.618',100,10,200),'纪律');
 assert.equal(swipeDestination('合约',-100,10,200),'纪律');
 assert.equal(swipeDestination('纪律',-100,0,200),'1.618');
 assert.equal(swipeDestination('持仓',-100,0,200),'统计');
 assert.equal(swipeDestination('持仓',100,0,200),'1.618');
 for(const [tab,dx] of [['合约',100],['统计',-100],['设置',-100]] as const)assert.equal(swipeDestination(tab,dx,0,200),null);
});
test('vertical scrolling, tiny movements and long drags cannot change tabs',()=>{
 for(const [x,y,t] of [[30,0,200],[80,100,200],[-100,0,1601]])assert.equal(swipeDestination('合约',x,y,t),null);
});
test('a deliberate slower swipe succeeds, while initial vertical intent remains locked',()=>{
 assert.equal(swipeDestination('合约',-100,10,1100),'纪律');
 assert.equal(swipeAxis('pending',4,3),'pending');
 assert.equal(swipeAxis('pending',9,12),'vertical');
 assert.equal(swipeAxis('vertical',160,13),'vertical');
 assert.equal(swipeAxis('pending',12,4),'horizontal');
 assert.equal(swipeAxis('horizontal',20,40),'horizontal');
});
test('valid neighbor follows the finger, boundaries resist and large travel softens',()=>{
 assert.equal(swipeOffset('纪律',-100,390),-100);
 assert.equal(swipeOffset('合约',100,390),32);
 assert.equal(swipeOffset('统计',-100,390),-32);
 assert.ok(swipeOffset('纪律',300,390)>214.5);
 assert.ok(swipeOffset('纪律',300,390)<300);
 assert.equal(swipeOffset('纪律',0,390),0);
});
