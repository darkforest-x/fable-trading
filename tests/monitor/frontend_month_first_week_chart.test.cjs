'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {mondayFibSymbolHash,mondayFibEventHash}=require('../../yoyo/monitor/static/month-first-week.js');

test('monthly monitor symbol navigation returns to the canonical Monday chart',()=>{
  assert.equal(mondayFibSymbolHash('ETH-USDT-SWAP'),'#monday-fib?symbol=ETH-USDT-SWAP');
  assert.equal(mondayFibSymbolHash('invalid'),'#monday-fib');
});

test('monthly event navigation preserves the exact event identity in the canonical chart hash',()=>{
  const event={symbol:'PARTI-USDT-SWAP',event_id:'2026-11|PARTI-USDT-SWAP|lower'};
  assert.equal(mondayFibEventHash(event),'#monday-fib?symbol=PARTI-USDT-SWAP&monthEvent=2026-11%7CPARTI-USDT-SWAP%7Clower');
  assert.equal(mondayFibEventHash({...event,event_id:''}),'#monday-fib');
  assert.equal(mondayFibEventHash({...event,symbol:'not-a-swap'}),'#monday-fib');
});
