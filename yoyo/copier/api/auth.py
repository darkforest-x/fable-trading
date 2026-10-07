from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from yoyo.copier.config import app_config, env

ALGORITHM = "HS256"
security = HTTPBearer(auto_error=False)


def auth_enabled() -> bool:
    return bool(app_config.admin.require_auth)


def create_token(subject: str = "admin") -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    return jwt.encode({"sub": subject, "exp": expire}, env.jwt_secret, algorithm=ALGORITHM)


def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    if not auth_enabled():
        return "anonymous"
    if not credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    try:
        payload = jwt.decode(credentials.credentials, env.jwt_secret, algorithms=[ALGORITHM])
        return str(payload.get("sub") or "")
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效令牌") from exc
