"""Simple local login (prototype). Real deployment will use AURAK Microsoft SSO via IT."""
import hashlib
import hmac
import os

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.models import Student, User


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(test.hex(), digest)


def current_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("uid")
    return db.get(User, uid) if uid else None


class LoginRequired(Exception):
    pass


class AccessPending(Exception):
    """A student whose dashboard access is not approved yet tried to open a dashboard page."""


# The only pages a student without approved access can use.
PENDING_STUDENT_PATHS = {"/me", "/me/request-access"}


def student_has_access(db: Session, user: User | None) -> bool:
    if user is None or user.role != "student":
        return True
    st = db.get(Student, user.student_id) if user.student_id else None
    return bool(st and st.access_status == "approved")


def require(request: Request, db: Session, *roles: str) -> User:
    user = current_user(request, db)
    if user is None:
        raise LoginRequired()
    if roles and user.role not in roles:
        raise HTTPException(status_code=403, detail="This page is not available for your role.")
    if request.url.path not in PENDING_STUDENT_PATHS and not student_has_access(db, user):
        raise AccessPending()
    return user
