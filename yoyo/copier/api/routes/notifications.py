from __future__ import annotations

from fastapi import APIRouter, Depends

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.notifications.telegram import (
    masked_chat_id,
    send_telegram_control_panel,
    send_telegram_notification,
    telegram_notifications_configured,
    telegram_notifications_enabled,
)
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/telegram/status")
def telegram_status(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    return {
        "configured": telegram_notifications_configured(),
        "enabled": telegram_notifications_enabled(db),
        "chat_id": masked_chat_id(),
    }


@router.post("/telegram/test")
async def telegram_test(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    result = await send_telegram_notification(
        db,
        "交易跟单系统测试通知",
        [
            "这是一条测试消息。",
            "收到它说明 Telegram 群通知已经接通。",
        ],
    )
    return result


@router.post("/telegram/panel")
async def telegram_panel(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    return await send_telegram_control_panel(db)
