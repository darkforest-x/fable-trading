"""Pro management endpoints persist confirmations, never exchange writes."""
from typing import Literal, Optional
import time
import sqlite3
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from yoyo.okx_execution.manual_commands import Commands
from yoyo.okx_execution.config import DEFAULT_MANUAL_RUNTIME, Config, read_private
from yoyo.okx_execution.manual_engine import ManualEngine
from yoyo.okx_execution.manual_queue import ReadLedger
from yoyo.okx_execution.client import Client, APIError, TransportError
from yoyo.okx_execution.source import Source
from pathlib import Path

class Preview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    lot_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    action: Literal['close','stop']
    stop: Optional[str]=Field(default=None,pattern=r'^\d{1,12}(\.\d{1,18})?$')
class Confirm(BaseModel):
    model_config=ConfigDict(extra='forbid')
    acknowledgement: Literal['CONFIRM-LIVE-MANAGEMENT']

def install(app,guard):
    def commands():return Commands(DEFAULT_MANUAL_RUNTIME)
    def ready():
        store=ReadLedger(DEFAULT_MANUAL_RUNTIME)
        if (store.meta('management_revision')!='owner-management-20261018-v1'
                or not 0<=int(time.time()*1000)-int(store.meta('heartbeat_ms') or 0)<60000):
            raise ValueError('实盘管理服务尚未就绪，请在系统运行页核对执行器')
    def public(value):
        value=dict(value);value.pop('account_uid',None);return value
    def engine():
        c=Config(**read_private(DEFAULT_MANUAL_RUNTIME/'config.json'));c.validate()
        return ManualEngine(c,Client(c,read_private(DEFAULT_MANUAL_RUNTIME/'credentials.json'),writes=False),Source(Path(c.source)),ReadLedger(DEFAULT_MANUAL_RUNTIME))
    def check(request):
        guard(request)
        if request.headers.get('x-fable-manual')!='owner-confirmation':raise HTTPException(403,'需要手动确认')
    def call(fn,*args):
        try:return public(fn(*args))
        except (ValueError,APIError) as e:raise HTTPException(409,str(e)) from None
        except (OSError,TransportError,sqlite3.Error):raise HTTPException(503,'账户回执暂不可用，请重新核对') from None
    @app.post('/api/pro/management/preview')
    def preview(body:Preview,request:Request):
        check(request);return call(lambda:(ready(),commands().preview(engine(),body.lot_id,body.action,body.stop))[1])
    @app.post('/api/pro/management/{identity}/confirm')
    def confirm(identity:str,body:Confirm,request:Request):
        check(request)
        def perform():
            queue=commands();existing=queue.get(identity)
            if existing['state'] in {'confirmed','processing','done','unknown'}:
                return existing
            ready()
            return queue.confirm(identity,int(time.time()*1000))
        return call(perform)
    @app.get('/api/pro/management/{identity}')
    def status(identity:str):
        value=call(lambda:commands().get(identity));value.pop('account_uid',None);return value
