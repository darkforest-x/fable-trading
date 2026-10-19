import test from 'node:test';
import assert from 'node:assert/strict';
import {sizeTicket,entryBlock,EXAMPLE} from '../src/demo.ts';
test('cost-inclusive risk and exposure remain below example limits',()=>{for(const side of ['long','short'])for(const fraction of [.25,.5,.75,1])for(const distance of [1,6,60,600,6000]){const p=sizeTicket({side,fraction,price:60000,stop:60000+(side==='long'?-distance:distance),step:.0001});assert.ok(p.plannedLoss<=25*fraction+1e-9);assert.ok(p.notional<=EXAMPLE.equity*fraction+1e-9);assert.ok(p.costs>0)}});
test('invalid stop directions and values reject previews',()=>{for(const a of [{side:'short',stop:59400},{side:'long',stop:60600},{side:'long',stop:60000},{side:'long',stop:NaN},{side:'long',stop:59400,fraction:2}])assert.throws(()=>sizeTicket({price:60000,step:.0001,fraction:1,...a}))});
test('minimum quantity is rejected rather than rounded up',()=>{assert.throws(()=>sizeTicket({price:60000,stop:59400,side:'long',fraction:.01,step:.1}))});
test('entry rejects locks, cooldowns, pending exposure and exhausted counts',()=>{const s={entries:0,occupied:false,locked:false,cooling:false};assert.equal(entryBlock(s),null);for(const u of [{locked:true},{cooling:true},{occupied:true},{entries:3}])assert.ok(entryBlock({...s,...u}))});
