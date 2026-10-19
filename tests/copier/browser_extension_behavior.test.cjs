"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync("tools/discord_copier_extension/background.js","utf8");
test("exact scope, retry after failure, source routing and version dedup",async()=>{
 let calls=[],fail=true,listener;const stored={},context={chrome:{runtime:{onInstalled:{addListener(){}},onMessage:{addListener(fn){listener=fn;}}},storage:{local:{get:async()=>stored,set:async v=>Object.assign(stored,v)}}},fetch:async(url,options)=>{calls.push(JSON.parse(options.body));if(fail)throw Error("offline");return {ok:true,json:async()=>({message_id:1})};},Date,Set,JSON};
 vm.createContext(context);vm.runInContext(source,context);
 const payload={guild_id:"1004707886657699901",channel_id:"1131521990814089276",external_id:"abc",content:"Entry 100",visible_recent_scan:true};
 assert.equal((await context.handleDiscordMessage({...payload,channel_id:"other"})).reason,"outside_watchlist");assert.equal(calls.length,0);
 assert.equal((await context.handleDiscordMessage(payload)).ok,false);fail=false;
 assert.equal((await context.handleDiscordMessage(payload)).ok,true);assert.equal(calls[1].source,"discord-web-visible");
 assert.equal((await context.handleDiscordMessage(payload)).reason,"duplicate");assert.equal(calls.length,2);
 await context.handleDiscordMessage({...payload,content:"Entry 101"});assert.equal(calls.length,3);
 await context.handleDiscordMessage({...payload,external_id:"old",recovery_capture:true});assert.equal(calls[3].source,"discord-web_recovery");
 stored.enabled=false;
 const heartbeat=await new Promise(resolve=>listener({type:"DISCORD_OKX_HEARTBEAT",payload},{},resolve));
 assert.equal(heartbeat.ok,false);assert.equal(calls.length,4);
});
