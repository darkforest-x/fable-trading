/* Paper fills decorate an independent instance of the Monday wallpaper chart.
 * Price/time geometry uses actual fills, exits and activated entry stops only.
 * Current Monday rays stay current even when a historical trade is selected.
 */
(() => {
  'use strict';
  const fin=Number.isFinite,periods={'5m':300000,'15m':900000,'1H':3600000,'4H':14400000};
  const valid=p=>p && /^[A-Z0-9]{1,32}-USDT-SWAP$/.test(p.symbol) && [1,-1].includes(p.side) && fin(p.fill_ms) && p.fill_ms>0 && fin(p.entry) && p.entry>0;
  function latest(track){return [...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].filter(p=>valid(p)&&p.taken===1&&p.notional>0).sort((a,b)=>b.fill_ms-a.fill_ms||b.id-a.id)[0]||null;}
  function select(track,id,pinned){return id ? [...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].find(p=>String(p.id)===id&&valid(p)) || (pinned&&String(pinned.id)===id?pinned:null) : latest(track);}
  function candleIndex(rows,stamp,step){return rows.findIndex(r=>r.time*1000<=stamp&&stamp<r.time*1000+step);}
  function geometry(p,rows,step,now){
    if(!valid(p)||!rows.length)return null;
    const end=p.stage==='closed'?p.exit_ms:now;
    if(!fin(end)||end<p.fill_ms||end<rows[0].time*1000)return null;
    return {entry:candleIndex(rows,p.fill_ms,step),exit:p.stage==='closed'?candleIndex(rows,p.exit_ms,step):-1,
      from:p.fill_ms,to:end,stop:fin(p.be_from_ms)&&p.be_from_ms>=p.fill_ms&&p.be_from_ms<=end?p.be_from_ms:null};
  }
  class TradeDrawing {
    constructor(owner){this.owner=owner;this.view={zOrder:()=> 'top',renderer:()=>({draw:target=>this.paint(target)})};}
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}
    detached(){this.requestUpdate=null;}
    paneViews(){return [this.view];}updateAllViews(){}
    autoscaleInfo(from,to){const g=this.shape,p=this.trade;if(!g||to<g.entry||(g.exit>=0&&from>g.exit))return null;const values=[p.entry,p.target,p.exit_price].filter(fin);return {priceRange:{minValue:Math.min(...values),maxValue:Math.max(...values)}};}
    paint(target){
      const o=this.owner,p=this.trade,g=this.shape;if(!g)return;
      const scale=o.chart.timeScale(),x=i=>scale.logicalToCoordinate(i),y=v=>o.series.priceToCoordinate(v),entry=y(p.entry),targetY=fin(p.target)?y(p.target):null;
      if(entry==null)return;
      const at=(i,stamp)=>{const a=x(i),b=x(i+1);return a==null||b==null?null:a+(b-a)*(stamp-o.rows[i].time*1000)/this.step;};
      const a=g.entry<0?(g.from<o.rows[0].time*1000?0:null):at(g.entry,g.from),b=g.exit>=0?at(g.exit,g.to):scale.width()-12;
      if(a==null||b==null)return;
      const color=p.side===1?'#169873':'#d7355d',format=window.SpikeChartTools.axisPrice;
      target.useMediaCoordinateSpace(({context:ctx,mediaSize:s})=>{
        ctx.save();ctx.beginPath();ctx.rect(0,0,s.width,s.height);ctx.clip();ctx.font='11px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';ctx.textBaseline='middle';
        const left=Math.max(0,a),right=Math.min(s.width,b);
        const line=(value,label,tone,start=left,dash=[])=>{const yy=y(value);if(yy==null||start>right)return;ctx.strokeStyle=tone;ctx.lineWidth=1.4;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(start,yy);ctx.lineTo(right,yy);ctx.stroke();ctx.setLineDash([]);const text=label+' '+format(value),w=ctx.measureText(text).width+14;if(right-start>w){ctx.fillStyle='#f7faf8ee';ctx.fillRect(right-w,yy+3,w,19);ctx.fillStyle=tone;ctx.textAlign='right';ctx.fillText(text,right-6,yy+12);}};
        if(right>=left){if(targetY!=null){ctx.fillStyle=color;ctx.globalAlpha=.065;ctx.fillRect(left,Math.min(entry,targetY),right-left,Math.abs(entry-targetY));ctx.globalAlpha=1;line(p.target,'目标',color,left,[4,4]);}line(p.entry,'开仓',color);}
        if(g.stop!=null){const i=candleIndex(o.rows,g.stop,this.step),sx=i>=0?x(i):g.stop<o.rows[0].time*1000?0:null;if(sx!=null)line(p.entry,'入场价止损已启用','#8b6ab1',Math.max(0,sx),[3,4]);}
        const marker=(i,value,label,up,tone)=>{const xx=x(i),yy=y(value);if(xx==null||yy==null||xx<0||xx>s.width)return;const dir=up?1:-1;ctx.setLineDash([]);ctx.fillStyle=tone;ctx.beginPath();ctx.moveTo(xx,yy);ctx.lineTo(xx-5,yy+dir*9);ctx.lineTo(xx+5,yy+dir*9);ctx.closePath();ctx.fill();const text=label+' · '+format(value),w=ctx.measureText(text).width+16,bx=Math.max(4,Math.min(s.width-w-4,xx-w/2)),by=yy+dir*26-11>(s.height-125)?yy-39:yy+dir*26-11;ctx.beginPath();ctx.roundRect(bx,by,w,22,6);ctx.fill();ctx.fillStyle='#fff';ctx.textAlign='center';ctx.fillText(text,bx+w/2,by+11);};
        if(g.entry>=0)marker(g.entry,p.entry,p.side===1?'多头开仓':'空头开仓',p.side===1,color);
        if(g.exit>=0&&fin(p.exit_price))marker(g.exit,p.exit_price,'平仓',p.side!==1,'#617488');
        ctx.restore();
      });
    }
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={latest,select,valid,candleIndex,geometry,TradeDrawing};return;}
  function create(host,notice){
    let active=true,request=null,epoch=0,desired=null,anchor=null,received=0,pinnedFocus='';
    const chart=window.SpikeMondayFibChart.create({decorate:(instance,data)=>{
      const p=data.paperTrade,step=periods[data.bar];
      if(!instance.paperDrawing){instance.paperDrawing=new TradeDrawing(instance);instance.series.attachPrimitive(instance.paperDrawing);}
      const draw=instance.paperDrawing;draw.trade=p;draw.step=step;draw.shape=geometry(p,instance.rows,step,Math.max(data.now_ms,p?.asof_ms||0));draw.requestUpdate?.();
      if(!p)return;
      const focusKey=p.id+'|'+data.bar;
      if(draw.shape&&pinnedFocus!==focusKey){pinnedFocus=focusKey;
        if(draw.shape.entry>=0){const i=draw.shape.entry,span=host.clientWidth<700?55:115;instance.chart.priceScale('right').applyOptions({autoScale:true});instance.chart.timeScale().setVisibleLogicalRange({from:i-span*.7,to:i+span*.3});}
      }
      notice(!draw.shape||draw.shape.entry===-1?'开仓时间不在当前已加载 K 线内；可切换更大周期查看。':'');
    }});
    function paint(){if(active&&anchor&&desired)chart.show(host,anchor,anchor.week,{period:desired.period,ma:desired.ma,trade:desired.trade});}
    async function show(trade,period='5m',ma=false){
      const changed=desired?.trade?.symbol!==trade?.symbol;desired={trade,period,ma};
      if(!active)return;
      if(changed){epoch++;request?.abort();request=null;anchor=null;chart.hide();pinnedFocus='';}
      if(!valid(trade)){chart.hide();notice('尚无计入资金的模拟交易，成交后自动显示 K 线。');return;}
      if(anchor)paint();
      if(request||(anchor&&Date.now()-received<60000))return;
      const revision=epoch,c=new AbortController();request=c;const timeout=setTimeout(()=>c.abort(),15000);
      if(!anchor)notice('正在加载 '+trade.symbol.replace('-USDT-SWAP','')+' 的 1.618 K 线…');
      try{const r=await fetch('/api/monday-fib/levels?symbol='+encodeURIComponent(trade.symbol),{signal:c.signal,cache:'no-store'});if(!r.ok)throw Error('周一区间读取失败');const a=await r.json();if(revision!==epoch||!active)return;
        if(a.symbol!==trade.symbol||a.timezone!=='America/New_York'||!a.week)throw Error('周一区间币种或时间不匹配');
        anchor=a;received=Date.now();notice('');paint();
      }catch(e){if(revision===epoch&&active)notice((e.name==='AbortError'?'图表读取超时':e.message)+' · 点击刷新重试');}
      finally{clearTimeout(timeout);if(request===c)request=null;}
    }
    return {show,fit:chart.fit,latest:chart.latest,zoom:chart.zoom,retry(){received=0;if(desired)show(desired.trade,desired.period,desired.ma);},
      setActive(value){active=value;if(!value){epoch++;request?.abort();request=null;chart.pause();}else if(desired)show(desired.trade,desired.period,desired.ma);},
      resetFocus(){pinnedFocus='';},destroy(){active=false;epoch++;request?.abort();chart.hide();}};
  }
  window.SpikeReclaimChart={create,latest,select,valid};
})();
