import os
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/db.sqlite3")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from . import models  # noqa: E402


def init_db():
    """Create database tables if not exists and apply lightweight migrations"""
    models.Base.metadata.create_all(bind=engine)
    # ensure cleaning-related columns exist (sqlite only)
    if engine.dialect.name == 'sqlite':
        conn = engine.raw_connection()
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA table_info(expense)")
            cols = [row[1] for row in cur.fetchall()]
            if 'is_cleaning' not in cols:
                try:
                    cur.execute("ALTER TABLE expense ADD COLUMN is_cleaning INTEGER DEFAULT 0;")
                except Exception:
                    pass
            cur.execute("PRAGMA table_info(company)")
            cols = [row[1] for row in cur.fetchall()]
            if 'is_cleaning_company' not in cols:
                try:
                    cur.execute("ALTER TABLE company ADD COLUMN is_cleaning_company INTEGER DEFAULT 0;")
                except Exception:
                    pass
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cleaning'")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(cleaning)")
                cols = [row[1] for row in cur.fetchall()]
                if 'income_id' not in cols:
                    try:
                        cur.execute("ALTER TABLE cleaning ADD COLUMN income_id INTEGER;")
                    except Exception:
                        pass
            conn.commit()
        finally:
            conn.close()

if __name__ == "__main__":
    init_db()

