import os
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")
SESSION_COOKIE = "rb_session"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours

_serializer = URLSafeTimedSerializer(SESSION_SECRET)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_session_token(username: str) -> str:
    return _serializer.dumps({"u": username})


def _decode_token(token: str):
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _decode_token(token)


def require_auth(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user
