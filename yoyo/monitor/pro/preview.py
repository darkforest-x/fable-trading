"""Run only the exported Pro UI, without starting any trading or scan workers.

This is an explicitly labelled design preview. It never proxies order APIs.
The current production frontend and backend continue running independently.
"""
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from .delivery import ASSETS, install


def create_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install(app)

    @app.get("/")
    def home():
        return RedirectResponse("/pro/market?reference=true")

    app.mount("/static/pro", StaticFiles(directory=ASSETS), name="pro-assets")
    return app


if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
