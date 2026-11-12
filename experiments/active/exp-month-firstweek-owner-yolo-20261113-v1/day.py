"""Follow first post-alert YOLO appearance up to 24h or rail expiry.
Reuse all windows already read in the 4h+9-close study; only replay the missing
later interval for rows still without a first structural, fresh, same-side box.
"""
import logging
import numpy as np
import pandas as pd
from ultralytics.utils import LOGGER
from yoyo.monitor.yolo_detector import YoloDetector
from windows import HERE,DATA,MINUTE,cohort,after_read,expiry,window_end
from detect import scan

def main():
    LOGGER.setLevel(logging.ERROR)
    det=YoloDetector(); assert det.warmup()['ready']
    early=pd.read_csv(DATA/'all_boxes.csv.gz')
    early=early[early.structural_pass & early.same_side & early.fresh]
    extra=[];out=[]
    for r in cohort().itertuples():
        raw=np.load(DATA/'tapes'/f'{r.case_id}.npy')
        for tf in (5,15):
            hits=early[(early.case_id==r.case_id)&(early.tf==tf)]
            extra_n=0
            if hits.empty:
                proposals,_,extra_n=scan(r,tf,det,True)
                extra.extend(proposals)
                hits=pd.DataFrame([p for p in proposals if p['structural_pass'] and p['same_side'] and p['fresh']])
            hours=min(r.known_ms+24*60*MINUTE,expiry(r.month))-r.known_ms
            z=dict(case_id=r.case_id,pair_id=r.pair_id,role=r.role,symbol=r.symbol,tf=tf,
                   hours_available=hours/(60*MINUTE),found=not hits.empty,additional_images=extra_n)
            if not hits.empty:
                best=hits.sort_values(['read_ms','confidence'],ascending=[True,False]).iloc[0]
                z.update(read_ms=best.read_ms,delay=(best.read_ms-r.known_ms)//MINUTE,
                    confidence=best.confidence,**after_read(r,int(best.read_ms),raw))
            out.append(z)
        print(r.case_id, '24h',flush=True)
    pd.DataFrame(extra).to_csv(DATA/'later_boxes.csv.gz',index=False)
    pd.DataFrame(out).to_csv(HERE/'first_in_day.csv',index=False)
if __name__=='__main__':main()
