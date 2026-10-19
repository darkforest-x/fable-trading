import test from 'node:test';
import assert from 'node:assert/strict';
import {activityTime,coinQuantity} from '../../yoyo/discipline_app/mobile/src/activityFormat.ts';

test('activity timestamps are Beijing seconds, including date rollover and missing legacy values',()=>{
 assert.equal(activityTime(Date.parse('2026-10-18T16:00:01Z')/1000),'2026-10-19 00:00:01');
 for(const value of [undefined,NaN,0,-1,Infinity])assert.equal(activityTime(value),'—');
});
test('pending quantities preserve small base-coin units without fixed six-place rounding',()=>{
 assert.equal(coinQuantity('0.00000001'),'0.00000001');
 assert.equal(coinQuantity('0.020000'),'0.02');
 assert.equal(coinQuantity('1234.125'),'1,234.125');
 assert.equal(coinQuantity(undefined),'—');
});
