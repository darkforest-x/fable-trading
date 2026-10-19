"""Owner-confirmed management intents for already owned manual lots.

The Pro server can preview/confirm a command but cannot write to the exchange.
The existing locked ManualEngine consumes it. Stops may only tighten; TP, risk
budget, entry policy and external-position ownership are never changed here.
"""
from contextlib import closing
import json
import sqlite3
import time
import uuid
from pathlib import Path
from .risk import D
from .client import APIError


class Commands:
    def __init__(self, runtime):
        self.path=Path(runtime)/'manual-commands.sqlite3'
        if self.path.is_symlink():raise ValueError('unsafe commands path')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, lot TEXT, state TEXT, value TEXT)')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_pending_lot ON commands(lot) WHERE state IN ('confirmed','processing')")
            db.commit()
        self.path.chmod(0o600)

    def db(self):return closing(sqlite3.connect(self.path, timeout=3))

    def get(self, identity):
        with self.db() as db:row=db.execute('SELECT state,value FROM commands WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('操作预览不存在')
        return dict(json.loads(row[1]),state=row[0])

    def save(self, value, state):
        with self.db() as db:
            db.execute('UPDATE commands SET state=?,value=? WHERE id=?',(state,json.dumps(value),value['id']));db.commit()
        return self.get(value['id'])

    def preview(self, engine, lot_id, action, stop=None):
        account=engine.account();engine.owned(account)
        if account['uid']!=engine.store.meta('account_uid'):raise ValueError('执行账户已变化')
        trade=next((r for r in engine.store.trades() if r['id']==lot_id),None)
        if not trade or trade['state']!='active' or trade.get('tp_basis')!='actual_fill':raise ValueError('仅可管理保护已核对的系统分笔')
        if trade.get('event',{}).get('source')!='owner_manual_yolo':raise ValueError('不属于手动检测分笔')
        engine.verify_stop(trade,engine.algo(trade))
        if action=='stop':validate_stop(trade,stop)
        elif action!='close':raise ValueError('不支持的操作')
        now=engine.clock();draft=dict(id=uuid.uuid4().hex,lot_id=lot_id,action=action,account_uid=account['uid'],symbol=trade['symbol'],side=trade['side'],size=trade['size'],algo_id=trade['algo_id'],old_stop=trade['stop'],stop=stop,take_profit=trade['take_profit'],created_ms=now,expires_ms=now+60000)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE commands SET state='superseded' WHERE lot=? AND state='preview'",(lot_id,))
            db.execute('INSERT INTO commands VALUES(?,?,?,?)',(draft['id'],lot_id,'preview',json.dumps(draft)));db.commit()
        return dict(draft,state='preview')

    def confirm(self, identity, now):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT state,value FROM commands WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('预览不存在')
            value=json.loads(row[1])
            if row[0] in {'confirmed','processing','done','unknown'}:return dict(value,state=row[0])
            if row[0]!='preview' or not value['created_ms']<=now<value['expires_ms']:raise ValueError('预览已过期或被替换')
            value['confirmed_ms']=now
            try:db.execute("UPDATE commands SET state='confirmed',value=? WHERE id=?",(json.dumps(value),identity));db.commit()
            except sqlite3.IntegrityError:raise ValueError('该分笔已有待处理操作') from None
        return dict(value,state='confirmed')

    def pending(self):
        with self.db() as db:return [dict(json.loads(r[1]),state=r[0]) for r in db.execute("SELECT state,value FROM commands WHERE state IN ('confirmed','processing') ORDER BY rowid")]


def validate_stop(trade, stop):
    if stop is None:raise ValueError('请输入新的止损价格')
    price=D(stop)
    if not price.is_finite() or price<=0 or price % D(trade['tick'])!=0:raise ValueError('止损必须是正数并符合最小报价单位')
    if (trade['side']=='long' and price<D(trade['stop'])) or (trade['side']=='short' and price>D(trade['stop'])):raise ValueError('只允许收紧止损，不得扩大已确认风险')
    if (trade['side']=='long' and price>=D(trade['take_profit'])) or (trade['side']=='short' and price<=D(trade['take_profit'])):raise ValueError('止损与止盈相冲突')


def consume(engine, commands):
    for cmd in commands.pending():
        trade=next((r for r in engine.store.trades() if r['id']==cmd['lot_id']),None)
        try:
            if not trade:raise ValueError('原分笔不存在')
            if cmd['state']=='processing':
                # A crash between ledger persistence and queue persistence must
                # not redispatch. The ledger is authoritative for the transition.
                if trade.get('owner_command_id')==cmd['id']:
                    if trade['state']=='closed':commands.save(dict(cmd,reason='原分笔已平仓'), 'done' if cmd['action']=='close' else 'rejected')
                    elif cmd['action']=='stop' and trade['state']=='active':
                        cmd['reason']='止损回执已核对' if D(trade['stop'])==D(cmd['stop']) else '交易所保留原保护；未重复改价'
                        commands.save(cmd,'done' if D(trade['stop'])==D(cmd['stop']) else 'rejected')
                    continue
                commands.save(dict(cmd,reason='执行边界中断，需核对原分笔；未重复发出请求'),'unknown');continue
            if not cmd.get('confirmed_ms') or not cmd['created_ms']<=engine.clock()<cmd['expires_ms']:raise ValueError('操作过期，请重新核对')
            account=engine.account()
            if account['uid']!=cmd['account_uid'] or account['uid']!=engine.store.meta('account_uid'):raise ValueError('账户归属变化')
            engine.owned(account)
            if trade['state']!='active' or any(str(trade[k])!=str(cmd[k]) for k in ('symbol','side','size','algo_id','take_profit')) or D(trade['stop'])!=D(cmd['old_stop']):raise ValueError('持仓或保护已变化，请重新预览')
            engine.verify_stop(trade,engine.algo(trade))
            if cmd['action']=='stop':
                validate_stop(trade,cmd['stop'])
                quote=engine.client.get('/api/v5/market/ticker',instId=trade['symbol'])[0]
                if not 0<=engine.clock()-int(quote['ts'])<15000:raise ValueError('报价过期')
                if (trade['side']=='long' and D(cmd['stop'])>=D(quote['bidPx'])) or (trade['side']=='short' and D(cmd['stop'])<=D(quote['askPx'])):raise ValueError('新止损已触及当前报价，请使用平仓操作')
            commands.save(cmd,'processing')
            trade['owner_command_id']=cmd['id']
            if cmd['action']=='close':
                trade.update(state='exit_needed',reason='owner_confirmed_close');engine.store.save(trade)
            else:
                trade.update(state='manual_owner_stop_pending',owner_stop=cmd['stop'],owner_stop_ms=engine.clock());engine.store.save(trade)
                try:engine.client.post('/api/v5/trade/amend-algos',dict(instId=trade['symbol'],algoId=trade['algo_id'],newSlTriggerPx=cmd['stop'],newSlOrdPx='-1',cxlOnFail=False))
                except APIError:
                    trade.update(state='active');engine.store.save(trade);commands.save(dict(cmd,reason='交易所拒绝，保留原保护'),'rejected')
        except ValueError as exc:commands.save(dict(cmd,reason=str(exc)),'rejected')
        # Transport errors intentionally propagate. The persisted pending state
        # is GET-reconciled by manage(), never POST-retried by consume().


def reconcile_stop(engine, trade):
    algo=engine.algo(trade)
    if algo.get('state')=='effective':trade['state']='stop_executing';engine.store.save(trade);return
    accepted=D(algo.get('slTriggerPx'))==D(trade['owner_stop'])
    candidate=dict(trade,stop=trade['owner_stop']) if accepted else trade
    try:engine.verify_stop(candidate,algo)
    except ValueError:
        trade.update(state='exit_needed',reason='owner_stop_protection_unverified');engine.store.save(trade);return
    if not accepted and engine.clock()-trade['owner_stop_ms']<10000:return
    trade.update(state='active',stop=candidate['stop'],stop_receipt=algo,protection_checked_ms=engine.clock())
    engine.store.save(trade)
