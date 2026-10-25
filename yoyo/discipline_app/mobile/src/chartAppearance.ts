/** Recolor the existing canvas; never replace series, data, range or subscriptions. */
import {ColorType,type IChartApi} from 'lightweight-charts';
export function observeChartAppearance(chart:IChartApi){
 const apply=()=>{
  const css=getComputedStyle(document.documentElement);
  const color=(name:string,fallback:string)=>css.getPropertyValue(name).trim()||fallback;
  const muted=color('--chart-text','#657281'),grid=color('--chart-grid','#e4eae6');
  chart.applyOptions({layout:{background:{type:ColorType.Solid,color:color('--canvas','#f0f4f8')},textColor:muted},grid:{vertLines:{color:grid},horzLines:{color:grid}},crosshair:{vertLine:{color:muted,labelBackgroundColor:color('--chart-label','#47685a')},horzLine:{color:muted,labelBackgroundColor:color('--chart-label','#47685a')}}});
 };
 apply();
 const observer=new MutationObserver(apply);
 observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
 return()=>observer.disconnect();
}
