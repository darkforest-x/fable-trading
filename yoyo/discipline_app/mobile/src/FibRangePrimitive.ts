/** Native series primitive: the range moves with both price and time scaling.
 * Uses Lightweight Charts 5.2 ISeriesPrimitive / media-coordinate renderer API.
 */
import type {ISeriesPrimitive,SeriesAttachedParameter,IPrimitivePaneView,IPrimitivePaneRenderer,UTCTimestamp} from 'lightweight-charts';
import type {FibRange} from './fibData';
export class FibRangePrimitive implements ISeriesPrimitive {
 private context?:SeriesAttachedParameter;
 private views:IPrimitivePaneView[];
 private range:FibRange;
 constructor(range:FibRange){
  this.range=range;
  const renderer:IPrimitivePaneRenderer={draw:target=>target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
   const source=this.context;if(!source)return;
   const y1=source.series.priceToCoordinate(this.range.high),y2=source.series.priceToCoordinate(this.range.low);
   const times=source.chart.timeScale(),visible=times.getVisibleRange();if(y1===null||y2===null||!visible)return;
   const coordinate=(stamp:number)=>stamp<=Number(visible.from)?0:stamp>=Number(visible.to)?mediaSize.width:times.timeToCoordinate(stamp as UTCTimestamp);
   const x1=coordinate(this.range.start),x2=coordinate(this.range.end);if(x1===null||x2===null||x2<=x1)return;
   ctx.fillStyle='rgba(56,189,248,0.065)';ctx.fillRect(x1,y1,x2-x1,y2-y1);
  })};
  this.views=[{zOrder:()=> 'bottom',renderer:()=>renderer}];
 }
 attached(context:SeriesAttachedParameter){this.context=context;context.requestUpdate()}
 detached(){this.context=undefined}
 paneViews(){return this.views}
 autoscaleInfo(){return {priceRange:{minValue:Math.max(0,this.range.lower),maxValue:this.range.upper}}}
}
