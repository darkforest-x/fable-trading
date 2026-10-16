"""Frozen model rendering and raw-box projection for the display workbench.

Close/HL2 SMA and EMA 20/60/120 read only each prefix, with 1,000 warmup
candles before the display start. W18/W19 use the existing clean 1280x742 L1
renderer, not browser chart pixels. Every box retains the raw xywhn and input
pixel identity. Bounds are inverse-mapped from the actual render transform;
we never replace predicted vertical edges by candle highs/lows.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .market import Cancelled
from .models import MODELS, ROOT, PARAMETERS, TIMEFRAMES

MA_COLUMNS = tuple(f'{kind}{period}' for kind in ('sma','ema') for period in (20,60,120))


def enrich(rows, source):
    """Causal o/h/l/c/v; SMA trailing 20/60/120, EMA adjust=False prefix."""
    frame = pd.DataFrame(rows).rename(columns=dict(o='open',h='high',l='low',c='close',v='volume'))
    prices = frame['close'] if source == 'close' else (frame['high']+frame['low'])/2
    for period in (20,60,120):
        frame[f'sma{period}'] = prices.rolling(period).mean()
        frame[f'ema{period}'] = prices.ewm(span=period,adjust=False).mean()
    return frame


def projected(xywh, label, confidence, window, transform, profile, symbol, timeframe, pixel_sha):
    box = [float(v) for v in xywh]
    if len(box)!=4 or not all(np.isfinite(box)) or any(v<0 or v>1 for v in box) or label not in (0,1) or not np.isfinite(confidence) or not 0<=confidence<=1:
        raise ValueError('模型返回非法框坐标或类别')
    cx,cy,w,h = box
    if w<=0 or h<=0 or confidence<PARAMETERS['conf']:
        return None
    step = TIMEFRAMES[timeframe]
    times = [int(t) for t in window['t']]
    centers = np.array([transform.x_at(i) for i in range(len(times))])
    a,b = sorted(int(np.argmin(abs(centers-x*transform.width))) for x in (cx-w/2,cx+w/2))
    def time_at(x):
        logical = (x*transform.width-transform.left)/transform.plot_w*(len(times)-1)
        return times[0]+float(logical)*step
    def price_at(y):
        return transform.price_max-(y*transform.height-transform.top)/transform.plot_h*(transform.price_max-transform.price_min)
    identity = [profile['sha256'],symbol,timeframe,times,pixel_sha,int(label),box]
    return dict(id=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24],
                side='long' if label==0 else 'short', confidence=float(confidence),
                raw_xywhn=box, price_high=price_at(cy-h/2), price_low=price_at(cy+h/2),
                x_start_ms=time_at(cx-w/2),x_end_ms=time_at(cx+w/2),
                core_start_ms=times[a],core_end_ms=times[b],core_bars=b-a+1,
                post_bars=len(times)-1-b,structural_pass=b-a+1 in (4,5) and 2<=len(times)-1-b<=9,
                window_start_ms=times[0],window_end_ms=times[-1],window_bars=len(times),
                detected_at_ms=times[-1]+step,input_pixel_sha256=pixel_sha)


def episodes(detections):
    """Greedy same-side overlapping core groups; keep first detection clock.

    Highest-confidence representative supplies geometry. First-seen time and
    the representative's later input endpoint are stored separately.
    """
    groups=[]
    for box in sorted(detections,key=lambda r:(r['detected_at_ms'],r['core_start_ms'])):
        matches=[g for g in groups if g['side']==box['side'] and box['core_start_ms']<=g['end'] and box['core_end_ms']>=g['start']]
        if not matches:
            groups.append(dict(side=box['side'],start=box['core_start_ms'],end=box['core_end_ms'],boxes=[box]))
        else:
            group=matches[0];group['boxes'].append(box)
            for other in matches[1:]:group['boxes'].extend(other['boxes']);groups.remove(other)
            group['start']=min(b['core_start_ms'] for b in group['boxes']);group['end']=max(b['core_end_ms'] for b in group['boxes'])
    return [dict(max(g['boxes'],key=lambda b:b['confidence']), first_detected_at_ms=min(b['detected_at_ms'] for b in g['boxes']),
                 observations=len(g['boxes'])) for g in groups]


class Engine:
    def __init__(self):
        self.models={}
        self.implementation_sources={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('models.py','market.py','inference.py','worker.py')}
        import subprocess
        self.source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()

    def load(self, profile):
        if profile['id'] in self.models:return self.models[profile['id']]
        path=ROOT/profile['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=profile['sha256']:
            raise ValueError('权重与冻结版本不一致，拒绝加载')
        import torch
        from ultralytics import YOLO
        torch.set_num_threads(2)
        model=YOLO(str(path))
        if model.names!={0:'dense_long',1:'dense_short'}:raise ValueError('模型类别契约不匹配')
        device='mps' if torch.backends.mps.is_available() else 'cpu'
        self.models[profile['id']]=(model,device)
        return model,device

    def scan(self,rows,spec,symbol,progress=lambda *_:None,cancelled=lambda:False):
        from yoyo.layers.l1_detection.render import render_chart
        profile=MODELS[spec['model_id']]
        if spec.get('model_sha256') and spec['model_sha256']!=profile['sha256']:raise ValueError('排队后模型版本发生变化，请重新提交')
        if spec.get('renderer_sha256') and spec['renderer_sha256']!=hashlib.sha256((ROOT/'yoyo/layers/l1_detection/render.py').read_bytes()).hexdigest():raise ValueError('排队后渲染器变化，请重新提交')
        if spec.get('parameters') and spec['parameters']!=PARAMETERS:raise ValueError('排队后推理参数变化，请重新提交')
        model,device=self.load(profile)
        step=TIMEFRAMES[spec['timeframe']]
        rows=[r for r in rows if r['t']+step<=spec['end_ms']]
        frame=enrich(rows,profile['source'])
        if spec['mode']=='latest':
            endpoints=list(range(len(frame)-3,len(frame)))
        else:
            endpoints=list(frame.index[(frame.t>=spec['start_ms']) & (frame.t+step<=spec['end_ms'])])
        if not endpoints or min(endpoints)<137:raise ValueError('检测范围或均线预热不足')
        tasks=[(i,n) for i in endpoints for n in (18,19)]
        detections=[]
        for offset in range(0,len(tasks),8):
            if cancelled():raise Cancelled()
            batch=[]
            for endpoint,length in tasks[offset:offset+8]:
                window=frame.iloc[endpoint-length+1:endpoint+1]
                img,transform=render_chart(window,out_path=None)
                sha=hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest()
                batch.append((window,img,transform,sha))
            predictions=model.predict(source=[b[1] for b in batch],batch=len(batch),device=device,**PARAMETERS)
            if len(predictions)!=len(batch):raise ValueError('模型批次长度不一致')
            for prediction,(window,img,transform,sha) in zip(predictions,batch):
                boxes=prediction.boxes
                if boxes is None:continue
                for xywh,label,conf in zip(boxes.xywhn.cpu().numpy(),boxes.cls.cpu().numpy(),boxes.conf.cpu().numpy()):
                    b=projected(xywh,float(label),float(conf),window,transform,profile,symbol,spec['timeframe'],sha)
                    if b:detections.append(b)
            progress(min(offset+len(batch),len(tasks)),len(tasks))
        first=max(119,(endpoints[0]-100 if spec['mode']=='history' else len(frame)-240))
        chart=frame.iloc[first:].replace({np.nan:None}).to_dict('records')
        result=dict(symbol=symbol,timeframe=spec['timeframe'],model_id=profile['id'],model_sha256=profile['sha256'],
                    mode=spec['mode'],cutoff_ms=spec['end_ms'],ma_source=profile['source'],warmup_bars=1000,
                    candles=chart,raw_detections=detections,episodes=episodes([b for b in detections if b['structural_pass']]),
                    windows_scored=len(tasks),endpoints_scored=len(endpoints),device=device,
                    parameters=PARAMETERS,production_eligible=False,
                    data_sha256=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
                    runtime_versions={k:importlib.metadata.version(k) for k in ('torch','ultralytics','numpy','pandas')},
                    implementation_sources=self.implementation_sources,source_commit=self.source_commit,
                    renderer_sha256=hashlib.sha256((ROOT/'yoyo/layers/l1_detection/render.py').read_bytes()).hexdigest())
        return result


def input_png(result, detection_id, annotated=False):
    """Reconstruct only from saved input columns; pixel identity must match."""
    import cv2
    from yoyo.layers.l1_detection.render import render_chart
    box=next((b for b in result['raw_detections'] if b['id']==detection_id),None)
    if box is None:raise KeyError(detection_id)
    frame=pd.DataFrame([r for r in result['candles'] if box['window_start_ms']<=r['t']<=box['window_end_ms']])
    img,_=render_chart(frame,out_path=None)
    if hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest()!=box['input_pixel_sha256']:
        raise ValueError('当前渲染与原模型输入不一致，无法展示复原图')
    if annotated:
        cx,cy,w,h=box['raw_xywhn'];height,width=img.shape[:2]
        cv2.rectangle(img,(round((cx-w/2)*width),round((cy-h/2)*height)),(round((cx+w/2)*width),round((cy+h/2)*height)),(0,130,90) if box['side']=='long' else (90,70,220),2)
    ok,png=cv2.imencode('.png',img)
    if not ok:raise ValueError('PNG 编码失败')
    return png.tobytes()
