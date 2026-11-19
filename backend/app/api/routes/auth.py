from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import get_current_user, require, store_dep
from app.core.security import Perm, RoleName, create_access_token, permissions_for, verify_password
from app.schemas.common import LoginIn, PreferencesIn, RegisterIn, TokenOut, UserOut, UserUpdateIn
from app.services import user_service
from app.services.activity import log_activity
from app.services.user_service import User
from app.storage.store import Store, utcnow_iso

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
users_router = APIRouter(prefix="/api/users", tags=["Users"])
prefs_router = APIRouter(prefix="/api/preferences", tags=["User preferences"])


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, full_name=u.full_name, role=u.role, is_active=u.is_active,
                   permissions=sorted(p.value for p in permissions_for(u.role)), last_login_at=u.last_login_at)


def _authenticate(store: Store, email: str, password: str) -> TokenOut:
    user = user_service.get_by_email(store, email)
    if user is None or not verify_password(password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    user = user_service.update(store, user.id, last_login_at=utcnow_iso())
    return TokenOut(access_token=create_access_token(str(user.id), user.role), user=user_out(user))


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, store: Store = Depends(store_dep)):
    """Self-registration creates an **Analyst** account. Administrators can change roles afterwards."""
    user = user_service.create(store, body.email, body.full_name, body.password, RoleName.ANALYST.value)
    log_activity(store, user_id=user.id, action="register", entity_type="user", entity_id=user.id,
                 description=f"New user registered: {user.full_name}")
    return user_out(user)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, store: Store = Depends(store_dep)):
    return _authenticate(store, body.email, body.password)


@router.post("/token", response_model=TokenOut, summary="OAuth2 password flow (Swagger 'Authorize')")
def token(form: OAuth2PasswordRequestForm = Depends(), store: Store = Depends(store_dep)):
    return _authenticate(store, form.username, form.password)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@users_router.get("", response_model=list[UserOut])
def list_users(store: Store = Depends(store_dep), _: User = Depends(require(Perm.MANAGE_USERS))):
    return [user_out(u) for u in user_service.list_users(store)]


@users_router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdateIn, store: Store = Depends(store_dep), admin: User = Depends(require(Perm.MANAGE_USERS))):
    if user_service.get_by_id(store, user_id) is None:
        raise HTTPException(404, "User not found")
    if user_id == admin.id and (body.is_active is False or (body.role and body.role != RoleName.ADMIN.value)):
        raise HTTPException(400, "You cannot demote or disable your own account")
    u = user_service.update(store, user_id, role=body.role, is_active=body.is_active)
    log_activity(store, user_id=admin.id, action="update", entity_type="user", entity_id=u.id,
                 description=f"User {u.email} updated", details=body.model_dump(exclude_none=True))
    return user_out(u)


@prefs_router.get("")
def get_preferences(store: Store = Depends(store_dep), user: User = Depends(get_current_user)):
    """Per-user UI preferences stored in data/preferences.json."""
    return user_service.get_preferences(store, user.id)


@prefs_router.put("")
def put_preferences(body: PreferencesIn, store: Store = Depends(store_dep), user: User = Depends(get_current_user)):
    return user_service.set_preferences(store, user.id, body.model_dump(exclude_none=True))
