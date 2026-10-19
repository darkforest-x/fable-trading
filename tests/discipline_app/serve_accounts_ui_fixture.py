"""Synthetic multi-account UI QA. No network exchange client or real credentials."""
import tempfile
import threading
import time
from pathlib import Path
import uvicorn
from fastapi.responses import HTMLResponse
from yoyo.discipline_app.accounts import Accounts
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.live_book import LiveBook
from test_accounts_catalog import IdentityExchange,Probe

TOKEN='ui-fixture-only-not-an-account-secret-123456'

def main():
    stopped=threading.Event()
    with tempfile.TemporaryDirectory(prefix='spike-accounts-ui-') as directory:
        root=Path(directory)
        primary=LiveBook(root/'live.sqlite3',IdentityExchange('first'))
        manager=Accounts(primary,root,lambda c:IdentityExchange(c.key),Probe)
        primary.reconcile()
        manager.add('备用测试账户','second','synthetic-secret','synthetic-phrase')
        instruments={f'{s}-USDT-SWAP':dict(symbol=f'{s}-USDT-SWAP',coin=s,tradable=True,step='.01',minimum='.01',tick='.01',contract_value='.01',fee_group='2',max_leverage=100) for s in ['BTC','ETH','SOL','DOGE','PEPE','SUI','XRP','ADA','AVAX']}
        def update():
            while not stopped.is_set():
                manager.update_market(instruments,{s:dict(last='100',bid='100',ask='100',time=time.time()) for s in instruments})
                for book in list(manager.books.values()):book.reconcile()
                stopped.wait(2)
        thread=threading.Thread(target=update,daemon=True);thread.start()
        app=create_app(manager,TOKEN,market_enabled=False,web_directory='yoyo/discipline_app/mobile/dist-fixture')
        @app.get('/qa/mobile',response_class=HTMLResponse)
        def mobile(width:int=390):
            width=320 if width==320 else 390
            return f'<html><body style="margin:0;background:#e9eeea;display:flex;justify-content:center"><iframe title="Synthetic multi-account {width}px" src="/" style="width:{width}px;height:844px;border:0"></iframe></body></html>'
        @app.get('/qa/counts')
        def counts():
            return {k:{'writes':len(b.client.writes),'entries':sum(1 for path,payload in b.client.writes if path.endswith('/order') and payload.get('attachAlgoOrds')),'closes':sum(1 for path,payload in b.client.writes if path.endswith('/order') and not payload.get('attachAlgoOrds'))} for k,b in manager.books.items()}
        try:uvicorn.run(app,host='127.0.0.1',port=8916,access_log=False)
        finally:stopped.set();thread.join(3)

if __name__=='__main__':main()
