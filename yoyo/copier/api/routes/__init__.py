from fastapi import APIRouter

from yoyo.copier.api.routes import control, dashboard, messages, monitor, notifications, orders, paper, settings, system

api_router = APIRouter(prefix="/api")
api_router.include_router(dashboard.router)
api_router.include_router(messages.router)
api_router.include_router(orders.router)
api_router.include_router(settings.router)
api_router.include_router(control.router)
api_router.include_router(system.router)
api_router.include_router(monitor.router)
api_router.include_router(notifications.router)
api_router.include_router(paper.router)
