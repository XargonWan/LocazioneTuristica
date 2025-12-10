import os
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/db.sqlite3")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from . import models  # noqa: E402


def init_db():
    """Create database tables if not exists"""
    models.Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    init_db()

