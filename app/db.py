import os
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .constants import DEFAULT_IVA, DEFAULT_STAMP_DUTY, DIRECT_BOOKING_PLATFORM_NAME, DIRECT_BOOKING_PLATFORM_NOTE
from .backup import record_session_commit

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/db.sqlite3")


class BackupAwareSession(Session):
    def commit(self):
        changed_objects = list(self.new)
        changed_objects.extend(
            obj for obj in self.dirty if self.is_modified(obj, include_collections=False)
        )
        changed_objects.extend(list(self.deleted))

        super().commit()

        if changed_objects:
            record_session_commit(changed_objects)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=BackupAwareSession)

from . import models  # noqa: E402


def init_db():
    """Create database tables if not exists and apply lightweight migrations"""
    models.Base.metadata.create_all(bind=engine)
    # ensure cleaning-related columns exist (sqlite only)
    if engine.dialect.name == 'sqlite':
        conn = engine.raw_connection()
        cur = conn.cursor()
        try:
            def ensure_column(table_name, column_name, ddl):
                cur.execute(f"PRAGMA table_info({table_name})")
                cols = [row[1] for row in cur.fetchall()]
                if column_name not in cols:
                    try:
                        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl};")
                    except Exception:
                        pass

            cur.execute("PRAGMA table_info(expense)")
            cols = [row[1] for row in cur.fetchall()]
            if 'is_cleaning' not in cols:
                try:
                    cur.execute("ALTER TABLE expense ADD COLUMN is_cleaning INTEGER DEFAULT 0;")
                except Exception:
                    pass
            ensure_column('expense', 'orig_recurrence_id', 'INTEGER REFERENCES recurrence(id)')
            ensure_column('income', 'orig_recurrence_id', 'INTEGER REFERENCES recurrence(id)')
            ensure_column('income', 'has_stamp_duty', 'INTEGER DEFAULT 0')
            ensure_column('income', 'stamp_duty_amount', 'DECIMAL(10, 2) DEFAULT 0.0')
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

    db = SessionLocal()
    try:
        def ensure_setting(key, value):
            setting = db.query(models.Settings).filter(models.Settings.key == key).first()
            if setting:
                return setting
            setting = models.Settings(key=key, value=value)
            db.add(setting)
            return setting

        platform = (
            db.query(models.Platform)
            .filter(models.Platform.notes == DIRECT_BOOKING_PLATFORM_NOTE)
            .order_by(models.Platform.id.asc())
            .first()
        )
        if not platform:
            platform = (
                db.query(models.Platform)
                .filter(models.Platform.name == DIRECT_BOOKING_PLATFORM_NAME)
                .order_by(models.Platform.id.asc())
                .first()
            )
            if not platform:
                platform = models.Platform(
                    name=DIRECT_BOOKING_PLATFORM_NAME,
                    link='',
                    notes=DIRECT_BOOKING_PLATFORM_NOTE,
                )
                db.add(platform)
            else:
                platform.notes = DIRECT_BOOKING_PLATFORM_NOTE
        platform.name = DIRECT_BOOKING_PLATFORM_NAME

        ensure_setting('default_iva', str(DEFAULT_IVA))
        ensure_setting('default_stamp_duty', str(DEFAULT_STAMP_DUTY))
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    init_db()

