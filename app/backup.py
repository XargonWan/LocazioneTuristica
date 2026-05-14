import contextvars
import os
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from .models import Attachment


DEFAULT_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/db.sqlite3")
DEFAULT_ATTACHMENTS_DIR = os.getenv("UPLOAD_DIR", "./data/attachments")
DEFAULT_BACKUP_ROOT = os.getenv("BACKUP_DIR", "./data/backups")


@dataclass
class BackupRequestState:
    db_changed: bool = False
    attachments_changed: bool = False
    backup_created: bool = False


class BackupError(RuntimeError):
    pass


_backup_state: contextvars.ContextVar[BackupRequestState | None] = contextvars.ContextVar(
    "backup_request_state",
    default=None,
)


def start_request_backup_tracking():
    return _backup_state.set(BackupRequestState())


def finish_request_backup_tracking(token):
    _backup_state.reset(token)


def get_request_backup_state():
    return _backup_state.get()


def record_session_commit(changed_objects):
    state = get_request_backup_state()
    if state is None or not changed_objects:
        return

    state.db_changed = True
    if any(isinstance(obj, Attachment) for obj in changed_objects):
        state.attachments_changed = True


def create_backup(kind: str, include_attachments: bool, apply_rotation: bool, retention: int | None = None) -> Path:
    if kind not in {"auto", "manual"}:
        raise BackupError(f"Unsupported backup kind: {kind}")

    backup_dir = _resolve_backup_root() / kind
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    scope = "full" if include_attachments else "db"
    archive_path = backup_dir / f"{timestamp}-{scope}.tar.gz"

    temp_dir = Path(tempfile.mkdtemp(prefix=f"locazione-backup-{kind}-"))
    try:
        db_snapshot = _create_sqlite_snapshot(temp_dir)
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(db_snapshot, arcname="db.sqlite3")
            if include_attachments:
                attachments_dir = _resolve_attachments_dir()
                if attachments_dir.exists():
                    archive.add(attachments_dir, arcname="attachments")

        if apply_rotation and kind == "auto":
            prune_backups(kind="auto", keep=retention)
        return archive_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def prune_backups(kind: str, keep: int | None):
    if kind != "auto":
        return
    if keep is None or keep < 1:
        raise BackupError("Automatic backup retention must be at least 1")

    backup_dir = _resolve_backup_root() / kind
    if not backup_dir.exists():
        return

    archives = sorted(
        [path for path in backup_dir.iterdir() if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for archive in archives[keep:]:
        archive.unlink(missing_ok=True)


def _create_sqlite_snapshot(target_dir: Path) -> Path:
    source_path = _resolve_sqlite_db_path()
    if not source_path.exists():
        raise BackupError(f"SQLite database not found at {source_path}")

    snapshot_path = target_dir / "db.sqlite3"
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    destination = sqlite3.connect(snapshot_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    return snapshot_path


def _resolve_sqlite_db_path() -> Path:
    url = make_url(DEFAULT_DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise BackupError("Automatic backups currently support only sqlite databases")

    db_path = Path(url.database)
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    return db_path


def _resolve_attachments_dir() -> Path:
    attachments_dir = Path(DEFAULT_ATTACHMENTS_DIR)
    if not attachments_dir.is_absolute():
        attachments_dir = (Path.cwd() / attachments_dir).resolve()
    return attachments_dir


def _resolve_backup_root() -> Path:
    backup_root = Path(DEFAULT_BACKUP_ROOT)
    if not backup_root.is_absolute():
        backup_root = (Path.cwd() / backup_root).resolve()
    return backup_root