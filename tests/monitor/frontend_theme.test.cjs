"use strict";

// Exercise pre-paint startup and browser preference changes without market APIs.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../yoyo/monitor/static/theme.js"), "utf8");

function page({ stored = null, dark = true, blocked = false, design = undefined } = {}) {
  const attributes = {}, meta = {}, documentEvents = {}, windowEvents = {};
  let ready = false;
  const select = { value: "system", addEventListener: (_, listener) => { select.change = listener; } };
  const media = { matches: dark, addEventListener: (_, listener) => { media.change = listener; } };
  const storage = {
    getItem: () => { if (blocked) throw new Error("Storage disabled"); return stored; },
    setItem: (key, value) => { if (blocked) throw new Error("Storage disabled"); assert.equal(key, "spike.theme"); stored = value; },
  };
  vm.runInNewContext(source, {
    document: {
      documentElement: { dataset:{design}, setAttribute: (key, value) => { attributes[key] = value; } },
      querySelector: () => ({ setAttribute: (key, value) => { meta[key] = value; } }),
      getElementById: () => ready ? select : null,
      addEventListener: (key, listener) => { documentEvents[key] = listener; },
    },
    window: {
      localStorage: storage, matchMedia: () => media,
      addEventListener: (key, listener) => { windowEvents[key] = listener; },
    },
  });
  return {
    attributes, meta, select, stored: () => stored,
    ready() { ready = true; documentEvents.DOMContentLoaded(); },
    choose(value) { select.value = value; select.change({ target: select }); },
    os(value) { media.matches = value; media.change(); },
    otherTab(value, key = "spike.theme") { stored = value; windowEvents.storage({ key }); },
  };
}

test("saved light appearance applies before DOM readiness and survives reload", () => {
  const p = page({ stored: "light", dark: true });
  assert.equal(p.attributes["data-theme"], "light");
  assert.equal(p.meta.content, "#f5f5f7");
  p.ready();
  assert.equal(p.select.value, "light");
  p.choose("dark");
  assert.equal(p.attributes["data-theme"], "dark");
  assert.equal(page({ stored: p.stored(), dark: false }).attributes["data-theme"], "dark");
});

test("system changes update automatic appearance but preserve an explicit choice", () => {
  const p = page({ stored: "system", dark: false });
  assert.equal(p.attributes["data-theme"], "light");
  p.ready();
  assert.equal(p.select.value, "system");
  p.os(true);
  assert.equal(p.attributes["data-theme"], "dark");
  p.choose("light");
  p.os(true);
  assert.equal(p.attributes["data-theme"], "light");
  p.choose("system");
  assert.equal(p.attributes["data-theme"], "dark");
});

test("restricted storage and corrupt preferences cannot break startup or manual switching", () => {
  const p = page({ blocked: true, dark: true });
  p.ready();
  p.choose("light");
  assert.equal(p.attributes["data-theme"], "light");
  p.os(true);
  assert.equal(p.attributes["data-theme"], "light");
  assert.equal(page({ stored: "invalid", dark: false }).attributes["data-theme"], "light");
});

test("other tabs update the preference and clearing storage restores the approved dark default", () => {
  const p = page({ stored: "dark", dark: false });
  p.ready();
  p.otherTab("light");
  assert.equal(p.select.value, "light");
  assert.equal(p.attributes["data-theme"], "light");
  p.otherTab(null, null);
  assert.equal(p.select.value, "dark");
  p.os(true);
  assert.equal(p.attributes["data-theme"], "dark");
});

test("first visit uses the selected dark workspace even on a light operating system", () => {
  const p = page({ dark: false });
  p.ready();
  assert.equal(p.select.value, "dark");
  assert.equal(p.attributes["data-theme"], "dark");
  assert.equal(p.meta.content, "#101114");
});

test('approved Stitch appearance is stable across old preferences without overwriting them',()=>{
 const p=page({stored:'light',dark:false,design:'stitch-obsidian'});p.ready();
 assert.equal(p.attributes['data-theme'],'dark');assert.equal(p.select.value,'dark');assert.equal(p.meta.content,'#0b0e14');assert.equal(p.stored(),'light');
 p.os(false);p.otherTab('system');assert.equal(p.attributes['data-theme'],'dark');assert.equal(p.select.value,'dark');
});

test('Apple Glass starts light before paint and preserves old user preferences',()=>{
 const p=page({stored:'dark',dark:true,design:'apple-glass'});p.ready();
 assert.equal(p.attributes['data-theme'],'light');assert.equal(p.select.value,'light');
 assert.equal(p.meta.content,'#f7f8f6');assert.equal(p.stored(),'dark');
 p.os(true);p.otherTab('dark');p.choose('dark');
 assert.equal(p.attributes['data-theme'],'light');assert.equal(p.stored(),'dark');
});
