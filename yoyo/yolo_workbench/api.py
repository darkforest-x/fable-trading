"""Same-origin workbench endpoints. Reads never start inference or training."""
from __future__ import annotations
import uuid
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import DEFAULT_MODEL, MODELS, TIMEFRAMES, catalog, freeze_spec
from .store import Book, now


class Scan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    model_id:str=DEFAULT_MODEL
    timeframe:Literal['5m','15m','30m','1H','4H','1Dutc']='15m'
    mode:Literal['latest','history']='latest'
    scope:Literal['selected','all']='selected'
    symbols:list[str]=Field(default_factory=lambda:['ETH-USDT-SWAP'],max_length=1000)
    start_ms:Optional[int]=None
    end_ms:Optional[int]=None

    @model_validator(mode='after')
    def validate_selection(self):
        import re
        if self.model_id not in MODELS:raise ValueError('模型尚无匹配的推理契约')
        if self.scope=='selected' and not self.symbols:raise ValueError('请选择币种')
        if any(not re.fullmatch(r'[A-Z0-9]{1,24}-USDT-SWAP',s) for s in self.symbols):raise ValueError('币种格式无效')
        if len(set(self.symbols))!=len(self.symbols):raise ValueError('币种重复')
        if self.mode=='history':
            step=TIMEFRAMES[self.timeframe]
            if self.start_ms is None or self.end_ms is None or not 0<self.start_ms<self.end_ms<=now():raise ValueError('历史起止时间无效或尚未收盘')
            if self.start_ms%step or self.end_ms%step:raise ValueError('历史时间须落在所选周期的 UTC K线边界')
            if (self.end_ms-self.start_ms)//step>10000:raise ValueError('每个任务最多检测 10,000 根；较长历史请分段提交')
        return self


class Submission(Scan):
    request_id:str=Field(default_factory=lambda:uuid.uuid4().hex,pattern=r'^[a-zA-Z0-9-]{16,80}$')


class Schedule(BaseModel):
    model_config=ConfigDict(extra='forbid')
    spec:Scan
    interval_minutes:int=Field(ge=5,le=10080)
    @model_validator(mode='after')
    def latest_only(self):
        if self.spec.mode!='latest':raise ValueError('定时任务只扫描最新收盘窗口')
        return self


def same_origin(request:Request):
    if request.method in ('GET','HEAD','OPTIONS'):return
    if request.headers.get('origin')!=f'{request.url.scheme}://{request.url.netloc}' or request.headers.get('sec-fetch-site','same-origin')!='same-origin':
        raise HTTPException(403,'请从当前工作台提交')


def install(app,runtime,launch_worker=True):
    book=Book(runtime);app.state.yolo_workbench=book
    from .discoveries import Feed
    feed=Feed(book)
    from .chart_tail import ChartTail
    charts=ChartTail(runtime)
    router=APIRouter(prefix='/api/yolo-detect',dependencies=[Depends(same_origin)])
    def launch():
        if launch_worker:
            from .worker import start
            start(runtime)
    from .supervisor import Supervisor
    app.state.yolo_workbench_supervisor=Supervisor(runtime)
    app.state.start_yolo_workbench=app.state.yolo_workbench_supervisor.start if launch_worker else lambda:None
    def job(identity):
        value=book.get(identity)
        if value is None:raise HTTPException(404,'检测任务不存在')
        return value
    @router.get('/catalog')
    def get_catalog():return catalog()
    @router.get('/symbols')
    def symbols():
        from .market import Market
        try:
            items=Market().universe();return dict(items=items,observed_ms=now())
        except Exception as exc:raise HTTPException(503,'OKX 币种列表不可用：'+str(exc)[:180]) from exc
    @router.get('/jobs')
    def jobs(limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0)):
        return dict(items=book.jobs(limit,offset),total=book.job_count(),worker=book.meta('worker'),now_ms=now())
    @router.post('/jobs',status_code=202)
    def submit(body:Submission):
        spec=freeze_spec(body.model_dump(exclude={'request_id'}))
        if not catalog()['items'][list(MODELS).index(body.model_id)]['available']:raise HTTPException(409,'模型权重文件不可用')
        try:value=book.submit(spec,body.request_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        launch();return value
    @router.get('/jobs/{identity}')
    def detail(identity:str):return dict(job(identity),results=book.summaries(identity))
    @router.post('/jobs/{identity}/cancel')
    def cancel(identity:str):job(identity);return book.cancel(identity)
    @router.get('/jobs/{identity}/results/{symbol}')
    def result(identity:str,symbol:str):
        job(identity);value=book.result(identity,symbol)
        if value is None:raise HTTPException(404,'该币种尚无结果')
        return value
    @router.get('/jobs/{identity}/results/{symbol}/input/{detection}')
    def image(identity:str,symbol:str,detection:str,annotated:bool=False):
        value=result(identity,symbol)
        from .inference import input_png
        try:return Response(input_png(value,detection,annotated),media_type='image/png')
        except KeyError:raise HTTPException(404,'检测框不存在') from None
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    @router.get('/jobs/{identity}/results/{symbol}/chart')
    def chart(identity:str,symbol:str):
        value=result(identity,symbol)
        if value.get('error') or not value.get('candles'):raise HTTPException(409,'该币种没有可续接的K线')
        try:return charts.read(identity,value)
        except Exception as exc:raise HTTPException(503,'行情更新失败：'+str(exc)[:180]) from exc
    @router.get('/schedules')
    def schedules():
        items=[]
        for s in book.schedules():
            j=book.get(s['last_job_id']) if s.get('last_job_id') else None
            items.append(dict(s,last_run=dict({k:j[k] for k in ('id','status','progress','updated_ms','error')},cutoff_ms=j['spec'].get('end_ms')) if j else None))
        items.sort(key=lambda s:(not s.get('incremental',False),TIMEFRAMES[s['spec']['timeframe']]))
        return dict(items=items,worker=book.meta('worker'))
    @router.get('/discoveries')
    def discoveries(days:int=Query(7,ge=0,le=30),q:str=Query('',max_length=80),
                    timeframe:Literal['','5m','15m','30m','1H','4H','1Dutc']='',
                    side:Literal['','long','short']='',limit:int=Query(30,ge=1,le=500),sort:Literal['newest','score','symbol']='newest'):
        return feed.read(days=days,query=q,timeframe=timeframe,side=side,limit=limit,sort=sort)
    @router.post('/schedules',status_code=201)
    def schedule(body:Schedule):
        if len(book.schedules())>=12:raise HTTPException(409,'已有 12 个计划，请先删除不需要的计划')
        from .patrol import next_due
        stamp=now();step=TIMEFRAMES[body.spec.timeframe]
        value=book.schedule(dict(id=uuid.uuid4().hex,spec=freeze_spec(body.spec.model_dump()),enabled=True,incremental=True,
                    baseline_after_ms=stamp//step*step-max(3,body.interval_minutes*60000//step)*step,
                    interval_minutes=body.interval_minutes,next_due_ms=next_due(stamp,body.interval_minutes),created_ms=stamp,last_job_id=None))
        launch();return value
    @router.delete('/schedules/{identity}')
    def delete_schedule(identity:str):book.remove_schedule(identity);return dict(deleted=True)
    app.include_router(router)
