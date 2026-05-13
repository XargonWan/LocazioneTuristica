import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.db import DATABASE_URL, engine


DB_BACKUP_MARKER = "db_backup"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "db_backup: backup and restore the sqlite database around tests that mutate persistent data",
    )


def _resolve_sqlite_db_files():
    url = make_url(DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database:
        return []

    db_path = Path(url.database)
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()

    return [
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-journal"),
    ]


def _copy_or_remove(source: Path, backup: Path):
    if backup.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, source)
        return

    source.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def backup_sqlite_db_for_marked_tests(request):
    if request.node.get_closest_marker(DB_BACKUP_MARKER) is None:
        yield
        return

    db_files = _resolve_sqlite_db_files()
    if not db_files:
        yield
        return

    backup_dir = Path(tempfile.mkdtemp(prefix="pytest-db-backup-"))
    backup_files = {db_file: backup_dir / db_file.name for db_file in db_files}

    engine.dispose()
    for db_file, backup_file in backup_files.items():
        if db_file.exists():
            shutil.copy2(db_file, backup_file)

    try:
        yield
    finally:
        engine.dispose()
        for db_file, backup_file in backup_files.items():
            _copy_or_remove(db_file, backup_file)
        engine.dispose()
        shutil.rmtree(backup_dir, ignore_errors=True)