"""Select examples by fixed source order within the anchor-matched photo cohort.
Show the actual last closed candle seen by the model or the blinded rater.
The title and rectangles are review overlays applied after inference.
"""
from PIL import Image,ImageDraw,ImageFont
import numpy as np
import pandas as pd
import cv2
from yoyo.layers.l1_detection.render import render_chart
from windows import HERE,DATA,OUT,MINUTE,cohort,frame

def export(row, tf, stamp, box, title, subtitle, name):
    bars=frame(row,tf)
    width=int(box.window_len) if box is not None else 18
    endpoint=int(np.searchsorted(bars.open_ms,stamp-tf*MINUTE))
    assert bars.open_ms.iat[endpoint]+tf*MINUTE==stamp
    view=bars.iloc[endpoint-width+1:endpoint+1]; img,trans=render_chart(view)
    if box is not None:
        x,y,w,h=[box[k] for k in ['prediction_cx_norm','prediction_cy_norm','prediction_w_norm','prediction_h_norm']]
        height,length=img.shape[:2]
        cv2.rectangle(img,(int(length*(x-w/2)),int(height*(y-h/2))),(int(length*(x+w/2)),int(height*(y+h/2))),(178,115,44),3)
    else:
        ticks=(trans.x_at(width-5),trans.x_at(width-2))
        cv2.line(img,(ticks[0],img.shape[0]-14),(ticks[1],img.shape[0]-14),(140,105,45),3)
    chart=Image.fromarray(cv2.cvtColor(img,cv2.COLOR_BGR2RGB));h=118
    sheet=Image.new('RGB',(chart.width,chart.height+h),'white');sheet.paste(chart,(0,h))
    draw=ImageDraw.Draw(sheet)
    def txt(y,t,n):
        draw.text((24,y),t,font=ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc',n),fill='#283946')
    clock=pd.Timestamp(stamp,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
    txt(7,f'{row.symbol}   ·   {tf} 分钟   ·   {title}',28)
    txt(53,f'{clock} 北京时间   |   '+('下轨观察多头' if row.side=='long' else '上轨观察空头'),20)
    txt(84,subtitle,17)
    OUT.mkdir(parents=True,exist_ok=True);sheet.save(OUT/name)

def main():
    rows=pd.concat([pd.read_csv(p/'cohort.csv') for p in (HERE,HERE/'ruler')]).set_index('case_id',drop=False)
    anchors=pd.read_csv(HERE/'primary_anchors.csv')
    selected=set(anchors.loc[anchors.in_primary,'case_id'])
    boxes=pd.concat([pd.read_csv(p/'all_boxes.csv.gz') for p in (DATA,DATA/'ruler')],ignore_index=True)
    refs=pd.concat([pd.read_csv(p/'blind_reconciliation.csv') for p in (HERE,HERE/'ruler')],ignore_index=True)
    for name,flag in [('detected',True),('missed',False)]:
        valid=refs[refs.case_id.isin(selected)&refs.vote.eq('yes')&refs.matched.eq(flag)].sort_values(['case_id','tf','read_ms'])
        if valid.empty:
            if not flag:
                continue
            eligible=boxes[boxes.case_id.isin(selected)&boxes.within4&boxes.fresh&boxes.same_side&boxes.structural_pass]
            if eligible.empty:
                continue
            box=eligible.sort_values(['case_id','read_ms','confidence'],ascending=[True,True,False]).iloc[0]
            export(rows.loc[box.case_id],int(box.tf),int(box.read_ms),box,'前四小时首次同向出框',
                   '蓝框标出模型看到的核心。截图截至模型实际给出确认的那根收盘。','owner-model.png')
            print('model example',box.case_id,int(box.tf))
            continue
        r=valid.iloc[0];row=rows.loc[r.case_id];tf=int(r.tf)
        if flag:
            match=boxes[boxes.case_id.eq(r.case_id)&boxes.tf.eq(tf)&boxes.structural_pass&boxes.same_side&boxes.fresh&boxes.read_ms.eq(r.matched_ms)].copy()
            overlap=np.minimum(match.core_end_ms,r.core_end_ms)-np.maximum(match.core_start_ms,r.core_start_ms)
            box=match[overlap>=2*tf*MINUTE].sort_values('confidence',ascending=False).iloc[0]
            export(row,tf,int(r.matched_ms),box,'盲看与模型框到同一段','蓝框为旧模型原始输出，画面截至这次模型确认收盘。','owner-detected.png')
        else:
            export(row,tf,int(r.read_ms),None,'盲看清楚，模型在前四小时未框中同一段','底部短线标出盲看的四根候选核心，最后一根为顺向收出六线的收盘。','owner-missed.png')
        print(name,r.case_id,tf,r.key)
if __name__=='__main__':main()
