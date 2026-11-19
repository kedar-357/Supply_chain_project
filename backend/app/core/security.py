"""Password hashing, JWT tokens and the role → permission matrix."""
from datetime import datetime, timedelta, timezone
from enum import Enum

import bcrypt
import jwt

from app.core.config import get_settings


class RoleName(str, Enum):
    ADMIN = "Administrator"
    MANAGER = "Supply Chain Manager"
    ANALYST = "Analyst"


class Perm(str, Enum):
    VIEW_ANALYTICS = "view_analytics"
    MANAGE_MASTER_DATA = "manage_master_data"  # products, suppliers, warehouses, inventory records
    UPLOAD_DATA = "upload_data"
    RUN_PREDICTIONS = "run_predictions"
    TRAIN_MODELS = "train_models"
    OPTIMIZE = "optimize"  # inventory/route optimization, draft POs
    SIMULATE = "simulate"
    GENERATE_REPORTS = "generate_reports"
    CONFIGURE = "configure"  # business parameters
    MANAGE_USERS = "manage_users"


ROLE_PERMISSIONS: dict[RoleName, set[Perm]] = {
    RoleName.ADMIN: set(Perm),
    RoleName.MANAGER: {
        Perm.VIEW_ANALYTICS,
        Perm.RUN_PREDICTIONS,
        Perm.TRAIN_MODELS,
        Perm.OPTIMIZE,
        Perm.SIMULATE,
        Perm.GENERATE_REPORTS,
    },
    RoleName.ANALYST: {
        Perm.VIEW_ANALYTICS,
        Perm.UPLOAD_DATA,
        Perm.RUN_PREDICTIONS,
        Perm.GENERATE_REPORTS,
    },
}


def permissions_for(role_name: str) -> set[Perm]:
    try:
        return ROLE_PERMISSIONS[RoleName(role_name)]
    except ValueError:
        return set()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, role: str, expires_minutes: int | None = None) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes or s.jwt_expire_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
