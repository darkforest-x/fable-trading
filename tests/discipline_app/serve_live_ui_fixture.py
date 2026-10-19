"""Loopback-only synthetic OKX fixture; no credentials or network exchange client."""
import tempfile
import threading
import time
from pathlib import Path
import uvicorn
from fastapi.responses import HTMLResponse
from test_live_book import Exchange, quote
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.api import create_app

TOKEN='ui-fixture-only-not-an-account-secret-123456'

def main():
    stopped=threading.Event()
    with tempfile.TemporaryDirectory(prefix='spike-live-ui-fixture-') as directory:
        book=LiveBook(Path(directory)/'fixture.sqlite3',Exchange(time.time))
        quote(book);book.reconcile()
        def update():
            while not stopped.wait(2):
                quote(book);book.reconcile()
        thread=threading.Thread(target=update,daemon=True);thread.start()
        app=create_app(book,TOKEN,market_enabled=False)
        @app.get('/qa/mobile',response_class=HTMLResponse)
        def mobile(width:int=390):
            width=320 if width==320 else 390
            return f'<html><body style="margin:0;background:#e9eeea;display:flex;justify-content:center"><iframe title="Synthetic live UI {width}px" src="/" style="width:{width}px;height:844px;border:0"></iframe></body></html>'
        try:uvicorn.run(app,host='127.0.0.1',port=8915,access_log=False)
        finally:stopped.set();thread.join(3)

if __name__=='__main__':main()
