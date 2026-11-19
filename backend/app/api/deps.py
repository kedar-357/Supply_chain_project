from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import Perm, decode_access_token, permissions_for
from app.services import user_service
from app.services.user_service import User
from app.storage.store import Store, get_store

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def store_dep() -> Store:
    return get_store()


def get_current_user(token: str = Depends(oauth2_scheme), store: Store = Depends(store_dep)) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = user_service.get_by_id(store, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require(*perms: Perm) -> Callable[..., User]:
    """Dependency factory enforcing role-based permissions."""

    def checker(user: User = Depends(get_current_user)) -> User:
        granted = permissions_for(user.role)
        missing = [p.value for p in perms if p not in granted]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' lacks permission: {', '.join(missing)}",
            )
        return user

    return checker
