"""Personal trade planning and append-only journal endpoints; never orders."""
from fastapi import HTTPException

from .manual import Event, ManualWorkspace, Playbook, Session, Ticket


def install(api, app, store):
    workspace = ManualWorkspace(store)
    app.state.manual_workspace = workspace

    def invoke(fn, *args):
        try:
            return fn(*args)
        except KeyError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))

    @api.get("/manual")
    def overview():
        return workspace.overview()

    @api.post("/manual/playbooks", status_code=201)
    def create_playbook(payload: Playbook):
        return invoke(workspace.save_playbook, payload)

    @api.put("/manual/playbooks/{key}")
    def edit_playbook(key: str, payload: Playbook):
        return invoke(workspace.save_playbook, payload, key)

    @api.post("/manual/sessions", status_code=201)
    def create_session(payload: Session):
        return invoke(workspace.save_session, payload)

    @api.put("/manual/sessions/{key}")
    def edit_session(key: str, payload: Session):
        return invoke(workspace.save_session, payload, key)

    @api.post("/manual/tickets", status_code=201)
    def create_ticket(payload: Ticket):
        return invoke(workspace.save_ticket, payload)

    @api.put("/manual/tickets/{key}")
    def edit_ticket(key: str, payload: Ticket):
        return invoke(workspace.save_ticket, payload, key)

    @api.get("/manual/tickets/{key}")
    def ticket_detail(key: str):
        result = invoke(workspace.get, "ticket", key)
        result["history"] = store.history("manual_ticket", key)
        return result

    @api.post("/manual/tickets/{key}/events")
    def append_event(key: str, payload: Event):
        return invoke(workspace.event, key, payload)
