from app.db import SessionLocal
from app.models import Settings


def get_setting(key: str, default=None):
    db = SessionLocal()
    try:
        s = db.query(Settings).filter(Settings.key == key).first()
        if s:
            return s.value
        return default
    finally:
        db.close()
