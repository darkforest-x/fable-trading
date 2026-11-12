"""Replay all 5m and 15m closes (not just formula candidates). No threshold changes.
Count fresh same-side structural proposals per signal-day in first 4h. Additional
nine closes only resolve late-window blind-reference matches, not first-4h hits.
A box is known only on the rightmost candle close, independent of the box end.
"""
import hashlib
import logging
import numpy as np
import pandas as pd
from ultralytics.utils import LOGGER
from yoyo.monitor.yolo_detector import YoloDetector,RenderedWindow,parse_prediction,MODEL_CLASSES,PREDICT_PARAMETERS
from yoyo.layers.l1_detection.render import render_chart
from windows import DATA,HERE,MINUTE,cohort,frame,window_end,after_read,expiry

def scan(row, tf, detector):
    bars=frame(row,tf); step=tf*MINUTE; ts=bars.open_ms.to_numpy(dtype='int64')
    boundary=window_end(row)
    indices=np.flatnonzero((ts>=row.known_ms)&(ts+step<=min(boundary+9*step,expiry(row.month)-1)))
    batch=[]; proposals=[]
    def flush():
        if not batch:return
        outputs=detector._model.predict(source=[w.image for _,w in batch],batch=len(batch),
                                        device=detector._device,**PREDICT_PARAMETERS)
        assert len(outputs)==len(batch)
        for (i,w),result in zip(batch,outputs):
            boxes=result.boxes
            if boxes is None or not len(boxes):continue
            for p in parse_prediction(boxes.xywhn.cpu().numpy(),boxes.cls.cpu().numpy(),
                                      boxes.conf.cpu().numpy(),w,row.symbol,str(tf)+'m'):
                stamp=int(ts[i]+step)
                proposals.append(dict(case_id=row.case_id,tf=tf,read_ms=stamp,within4=stamp<=boundary,
                    same_side=p['side']==row.side,fresh=p['core_start_ms']>=row.known_ms,**p))
        batch.clear()
    for i in indices:
        for length in (18,19):
            crop=slice(i-length+1,i+1)
            image,transform=render_chart(bars.iloc[crop])
            w=RenderedWindow(image,transform,tuple(map(int,ts[crop])),hashlib.sha256(image.tobytes()).hexdigest())
            batch.append((i,w))
            if len(batch)==8:flush()
    flush()
    return proposals, 2*int((ts[indices]+step<=boundary).sum()),2*len(indices)

def main():
    LOGGER.setLevel(logging.ERROR)
    detector=YoloDetector()
    assert detector.warmup()['ready'] and detector._model.names==MODEL_CLASSES
    result=[];out=[]
    DATA.mkdir(parents=True,exist_ok=True)
    for row in cohort().itertuples():
        raw=np.load(DATA/'tapes'/f'{row.case_id}.npy')
        for tf in (5,15):
            proposals,main_images,total_images=scan(row,tf,detector)
            out.extend(proposals)
            accepted=[p for p in proposals if p['within4'] and p['structural_pass'] and p['same_side'] and p['fresh']]
            summary=dict(case_id=row.case_id,pair_id=row.pair_id,role=row.role,symbol=row.symbol,
                side=row.side,tf=tf,known_ms=row.known_ms,within4_images=main_images,total_images=total_images,
                any_same=any(p['within4'] and p['same_side'] and p['structural_pass'] for p in proposals),
                found=bool(accepted))
            if accepted:
                p=min(accepted,key=lambda p:(p['read_ms'],-p['confidence']))
                summary.update(read_ms=p['read_ms'],delay=(p['read_ms']-row.known_ms)//MINUTE,
                    confidence=p['confidence'],core_start_ms=p['core_start_ms'],core_end_ms=p['core_end_ms'],
                    **after_read(row,p['read_ms'],raw))
            result.append(summary)
        print(row.case_id,flush=True)
    pd.DataFrame(out).to_csv(DATA/'all_boxes.csv.gz',index=False)
    r=pd.DataFrame(result)
    assert not r.duplicated(['case_id','tf']).any() and len(r)==len(cohort())*2
    r.to_csv(HERE/'first_reads.csv',index=False)
    print(r.groupby(['role','tf']).found.agg(['sum','size']))
if __name__=='__main__':main()
