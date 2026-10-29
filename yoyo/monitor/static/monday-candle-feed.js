/* Display-only OKX native candle stream. No signal/stop consumers.
 * Contract: https://app.okx.com/docs-v5/en/#order-book-trading-market-data-ws-candlesticks-channel
 * Socket messages are full OHLC snapshots, never synthetic ticker candles.
 */
(() => {
  'use strict';
  const PERIODS={'1m':60000,'2m':120000,'3m':180000,'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
  const nativeBar=period=>({'2m':'1m','12H':'12Hutc','1D':'1Dutc'}[period]||period);
  function valid(b,period,now){
    const step=PERIODS[period],boundary=Math.floor(now/step)*step;
    return !!step&&[b.time,b.open,b.high,b.low,b.close,b.volume].every(Number.isFinite)
      &&b.time>=boundary-3*step&&b.time%step===0&&b.time<=now
      &&Math.min(b.open,b.high,b.low,b.close)>0&&b.volume>=0
      &&b.high>=Math.max(b.open,b.close,b.low)&&b.low<=Math.min(b.open,b.close)
      &&(b.confirmed===true?b.time+step<=now:b.confirmed===false&&b.time===boundary);
  }
  function parse(rows,period,now){
    if(!Array.isArray(rows))return [];
    return rows.flatMap(r=>{
      if(!Array.isArray(r)||r.length!==9||!['0','1'].includes(r[8]))return [];
      if(r.slice(0,8).some(x=>typeof x!=='string'||!x.trim()||!Number.isFinite(Number(x))))return [];
      const b={time:+r[0],open:+r[1],high:+r[2],low:+r[3],close:+r[4],volume:+r[5],confirmed:r[8]==='1'};
      return valid(b,period,now)?[b]:[];
    }).sort((a,b)=>a.time-b.time);
  }
  function mergeDisplay(context,live,period){
    if(!live)return context;
    // Historical confirmed OHLC always wins. The live map is private to this
    // chart and cannot widen last_close_ms or enter the order-context API.
    const rows=new Map((context.candles||[]).map(b=>[b.time,b]));
    for(const b of live.bars||[])if(valid(b,period,live.now_ms)&&b.time>=context.last_close_ms)rows.set(b.time,b);
    return {...context,candles:[...rows.values()].sort((a,b)=>a.time-b.time),display_live:true};
  }
  function create({symbol,period,onChange,onReconnect=()=>{},socket=()=>new WebSocket('wss://ws.okx.com/ws/v5/business'),request=(...args)=>fetch(...args),now=Date.now,
    later=setTimeout,cancel=clearTimeout,repeat=setInterval,clearRepeat=clearInterval}){
    const channel='candle'+nativeBar(period),bars=new Map();
    let active=false,ws=null,retry=null,beat=null,paint=null,read=null,readTimeout=null;
    let offset=0,lastData=0,lastSuccess=0,lastMessage=0,connectedAt=0,startedAt=0,attempts=0,revision=0,opened=false,lastPaint='';
    const clock=()=>now()+offset;
    function emit(){
      cancel(paint);paint=null;if(!active)return;
      const time=clock();for(const [t,b] of bars)if(!valid(b,period,time))bars.delete(t);
      const ordered=[...bars.values()].sort((a,b)=>a.time-b.time),error=now()-Math.max(lastSuccess,startedAt)>15000?'实时 K 线更新延迟，正在重连':'';
      // Heartbeats and repeated exchange snapshots must not recompute 720 bars
      // and six averages when the visible OHLC/volume/status is unchanged.
      const fingerprint=JSON.stringify([ordered,error]);if(fingerprint===lastPaint)return;
      lastPaint=fingerprint;onChange({bars:ordered,now_ms:time,error});
    }
    function changed(next){
      for(const b of next){const old=bars.get(b.time);if(!old?.confirmed||b.confirmed)bars.set(b.time,b);}
      if(!paint)paint=later(emit,100);
    }
    async function refresh(){
      if(!active||read)return;
      const c=new AbortController(),version=revision;read=c;const timeout=later(()=>c.abort(),8000);readTimeout=timeout;
      try{
        const r=await request('/api/monday-fib/live-candles?symbol='+encodeURIComponent(symbol)+'&period='+encodeURIComponent(period),{cache:'no-store',credentials:'same-origin',signal:c.signal});
        const value=await r.json();
        if(!active||c.signal.aborted)return;
        if(!r.ok||value.error||value.symbol!==symbol||value.bar!==period||value.native_bar!==nativeBar(period)||!Number.isFinite(value.now_ms))throw Error('live_candle_unavailable');
        // A slow REST snapshot cannot overwrite a newer websocket update.
        if(version!==revision)return;
        offset=value.now_ms-now();
        const next=[...(value.closed_candles||[]).map(b=>({...b,confirmed:true})),...(value.forming_candle?[{...value.forming_candle,confirmed:false}]:[])].filter(b=>valid(b,period,clock()));
        if(!next.length)throw Error('live_candle_empty');
        lastSuccess=now();changed(next);
      }catch{if(active)emit();}
      finally{cancel(timeout);if(read===c){read=null;readTimeout=null;}}
    }
    function connect(){
      if(!active||ws)return;
      let current;
      const reconnect=()=>{
        if(!active||ws!==current)return;
        ws=null;lastData=0;
        retry=later(connect,Math.min(15000,1000*2**attempts++));
        void refresh();
      };
      try{current=socket();}catch{retry=later(connect,Math.min(15000,1000*2**attempts++));return;}
      ws=current;connectedAt=lastMessage=now();
      current.onopen=()=>{
        if(!active||ws!==current)return;
        current.send(JSON.stringify({op:'subscribe',args:[{channel,instId:symbol}]}));
        if(opened){onReconnect();void refresh();}opened=true;
      };
      current.onmessage=event=>{
        if(!active||ws!==current)return;lastMessage=now();
        if(event.data==='pong')return;
        try{
          const value=JSON.parse(event.data);
          if(value.event==='error'){current.close();return;}
          if(value.arg?.instId!==symbol||value.arg?.channel!==channel)return;
          const next=parse(value.data,period,clock());if(!next.length)return;
          lastData=lastSuccess=now();attempts=0;revision++;changed(next);
        }catch{/* Invalid public messages never replace chart data. */}
      };
      current.onerror=()=>{if(active&&ws===current)current.close();};
      current.onclose=reconnect;
    }
    return {
      start(){
        if(active)return;active=true;startedAt=connectedAt=now();void refresh();if(period!=='2m')connect();
        beat=repeat(()=>{
          if(!active)return;
          if(ws?.readyState===0&&now()-connectedAt>10000)ws.close();
          if(ws?.readyState===1){if(now()-lastMessage>20000)ws.close();else ws.send('ping');}
          if(!lastData||now()-lastData>5000)void refresh();
          emit();
        },3000);
      },
      setClock(stamp){if(Number.isFinite(stamp))offset=stamp-now();},
      stop(){active=false;cancel(retry);cancel(paint);cancel(readTimeout);clearRepeat(beat);read?.abort();read=null;
        if(ws){ws.onopen=ws.onmessage=ws.onerror=ws.onclose=null;ws.close();ws=null;}}
    };
  }
  const api={PERIODS,nativeBar,valid,parse,mergeDisplay,create};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeMondayCandleFeed=api;
})();
