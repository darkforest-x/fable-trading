"""Authenticated paper replay endpoints, deliberately without account selection."""
from fastapi import Depends
from fastapi.responses import JSONResponse
from .replay_history import ReplayError


def install(app, auth):
    @app.exception_handler(ReplayError)
    async def replay_error(_, exc):
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    @app.get('/v1/replay/catalog', dependencies=[Depends(auth)])
    def catalog():
        return app.state.replay.history.catalog()

    @app.get('/v1/replay/sessions', dependencies=[Depends(auth)])
    def sessions():
        return app.state.replay.list()

    @app.post('/v1/replay/sessions', dependencies=[Depends(auth)])
    def create(body: dict):
        return app.state.replay.create(body)

    @app.get('/v1/replay/sessions/{identity}', dependencies=[Depends(auth)])
    def read(identity: str):
        return app.state.replay.get(identity)

    @app.post('/v1/replay/sessions/{identity}/commands', dependencies=[Depends(auth)])
    def command(identity: str, body: dict):
        return app.state.replay.command(identity, body)
