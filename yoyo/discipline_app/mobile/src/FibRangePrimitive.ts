/** Solid levels and bare text, clipped to the completed NY Monday range.
 * Uses Lightweight Charts 5.2 ISeriesPrimitive / media-coordinate renderer API.
 * Stitch 332e4a5b46a14da5b5787c27cd1d5b50. No fill or axis-label backgrounds.
 */
import type {ISeriesPrimitive,SeriesAttachedParameter,IPrimitivePaneView,IPrimitivePaneRenderer,UTCTimestamp} from 'lightweight-charts';
import {fibLevels,type FibRange} from './fibData.ts';
export class FibRangePrimitive implements ISeriesPrimitive {
 private context?:SeriesAttachedParameter;
 private views:IPrimitivePaneView[];
 private range:FibRange;
 constructor(range:FibRange){
  this.range=range;
  const renderer:IPrimitivePaneRenderer={draw:target=>target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
   const source=this.context;if(!source)return;
   const times=source.chart.timeScale(),visible=times.getVisibleRange();if(!visible)return;
   const coordinate=(stamp:number)=>{
    if(stamp<=Number(visible.from))return 0;
    if(stamp>=Number(visible.to))return mediaSize.width;
    const exact=times.timeToCoordinate(stamp as UTCTimestamp);if(exact!==null)return exact;
    // A NY close can fall between 4H candle opens, especially after DST.
    // Interpolate between actual native bar coordinates rather than shifting it.
    const bars=source.series.data();let left=0,right=bars.length;
    while(left<right){const mid=(left+right)>>>1;if(Number(bars[mid].time)<stamp)left=mid+1;else right=mid}
    const before=bars[left-1]?.time,after=bars[left]?.time;
    if(typeof before!=='number'||typeof after!=='number')return null;
    const xBefore=times.timeToCoordinate(before),xAfter=times.timeToCoordinate(after);
    return xBefore===null||xAfter===null?null:xBefore+(xAfter-xBefore)*(stamp-before)/(after-before);
   };
   const x1=coordinate(this.range.start),x2=coordinate(this.range.end);if(x1===null||x2===null||x2<=x1)return;
   ctx.save();ctx.setLineDash([]);ctx.lineWidth=1;
   ctx.font='11px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';ctx.textAlign='right';ctx.textBaseline='bottom';
   for(const level of fibLevels(this.range)){
    const y=source.series.priceToCoordinate(level.price);if(y===null||y<0||y>mediaSize.height)continue;
    ctx.strokeStyle=level.color;ctx.beginPath();ctx.moveTo(x1,y);ctx.lineTo(x2,y);ctx.stroke();
    if(level.title&&x2-x1>ctx.measureText(level.title).width+8){ctx.fillStyle=level.color;ctx.fillText(level.title,x2-4,y-4)}
   }
   ctx.restore();
  })};
  this.views=[{zOrder:()=> 'bottom',renderer:()=>renderer}];
 }
 attached(context:SeriesAttachedParameter){this.context=context;context.requestUpdate()}
 detached(){this.context=undefined}
 paneViews(){return this.views}
 autoscaleInfo(){
  const visible=this.context?.chart.timeScale().getVisibleRange();
  if(visible&&(Number(visible.to)<this.range.start||Number(visible.from)>=this.range.end))return null;
  return {priceRange:{minValue:Math.max(0,this.range.lower),maxValue:this.range.upper}};
 }
}
