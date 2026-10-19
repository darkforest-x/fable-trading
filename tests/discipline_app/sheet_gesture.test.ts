import test from 'node:test';
import assert from 'node:assert/strict';
import {shouldDismissSheet} from '../../yoyo/discipline_app/mobile/src/sheetGesture.ts';
test('sheet handle needs deliberate downward intent, not a tap or sideways/upward drag',()=>{
 assert.equal(shouldDismissSheet(10,80),true);
 for(const [x,y] of [[0,0],[0,40],[80,70],[0,-100]])assert.equal(shouldDismissSheet(x,y),false);
});
