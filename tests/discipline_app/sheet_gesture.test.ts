import test from 'node:test';
import assert from 'node:assert/strict';
import {shouldDismissSheet} from '../../yoyo/discipline_app/mobile/src/sheetGesture.ts';

test('short fast downward flick closes; horizontal, upward and tiny movement never do',()=>{
 assert.equal(shouldDismissSheet(2,32,.8),true);
 assert.equal(shouldDismissSheet(2,32,.2),false);
 assert.equal(shouldDismissSheet(1,12,2),false);
 assert.equal(shouldDismissSheet(30,32,1),false);
 assert.equal(shouldDismissSheet(1,-80,2),false);
 assert.equal(shouldDismissSheet(0,70,-.1),true);
});
test('sheet handle needs deliberate downward intent, not a tap or sideways/upward drag',()=>{
 assert.equal(shouldDismissSheet(10,80),true);
 for(const [x,y] of [[0,0],[0,40],[80,70],[0,-100]])assert.equal(shouldDismissSheet(x,y),false);
});
