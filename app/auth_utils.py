from fastapi import Request, HTTPException
from app.db import SessionLocal
from app.models import User


def get_current_user(request: Request):
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user
    finally:
        db.close()


def admin_required(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail="Admin only")
    return user
