/** Lazy chart boundary: collapsed tickets load no canvas library or candle requests. */
import { Component, lazy, memo, Suspense, useState, type ReactNode } from 'react';
import { Icon } from './Icon';
import { feedback } from './feedback';
import type { Connection } from './api';
import { PERIODS, type CandlePeriod } from './candleData';
const CandlePanel = lazy(() => import('./CandlePanel'));
export type CandleProps = { symbol: string; connection?: Connection | null; tick?: string; entry?: string; stop?: string; active?:boolean };
class ChartBoundary extends Component<{children:ReactNode;onClose:()=>void},{failed:boolean}> {
 state = { failed:false };
 static getDerivedStateFromError() { return { failed:true }; }
 render() { return this.state.failed ? <div className="candle-failure"><span>K线暂时无法显示</span><button onClick={this.props.onClose}>收起</button></div> : this.props.children; }
}
export const CandleModule = memo(function CandleModule(props: CandleProps) {
 const [expanded,setExpanded] = useState(false);
 const [period,setPeriod] = useState<CandlePeriod>(() => { try { const saved=localStorage.getItem('spike-chart-period'); if(saved && Object.hasOwn(PERIODS,saved)) return saved as CandlePeriod; } catch {} return '15m'; });
 function changePeriod(value:CandlePeriod) { setPeriod(value); try {localStorage.setItem('spike-chart-period',value)}catch{} feedback('light'); }
 function collapse() { setExpanded(false); feedback('light'); }
 return <section className="candle-module" data-no-swipe aria-label="K线行情">
  {!expanded ? <button className="candle-toggle" aria-expanded={false} onClick={()=>{setExpanded(true);feedback('light')}}><Icon name="trade" size={17}/><b>K线</b><small>{PERIODS[period]}</small><span>展开</span><Icon name="chevron" size={14}/></button>
   : <ChartBoundary onClose={collapse}><Suspense fallback={<div className="candle-loading"><span>正在加载图表…</span><button onClick={collapse}>收起</button></div>}><CandlePanel {...props} period={period} onPeriod={changePeriod} onCollapse={collapse}/></Suspense></ChartBoundary>}
 </section>;
});
