import os
import tarfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Apartment, Attachment, Expense, Settings, User
from app.routers.attachments import UPLOAD_DIR
from app.routers.auth import pwd_context


pytestmark = pytest.mark.db_backup

BACKUP_ROOT = Path(os.getenv("BACKUP_DIR", "./data/backups")).resolve()
AUTO_BACKUP_DIR = BACKUP_ROOT / "auto"
MANUAL_BACKUP_DIR = BACKUP_ROOT / "manual"
UPLOAD_PATH = Path(UPLOAD_DIR).resolve()


def create_admin(db):
    user = db.query(User).filter(User.username == "backup-admin").first()
    if user:
        return user
    user = User(username="backup-admin", role="admin", must_change_password=False)
    user.password_hash = pwd_context.hash("secret")
    db.add(user)
    db.commit()
    return user


def login_admin(client):
    response = client.post("/auth/login", data={"username": "backup-admin", "password": "secret"})
    assert response.status_code in (200, 303)


def clear_backup_dirs():
    for directory in (AUTO_BACKUP_DIR, MANUAL_BACKUP_DIR):
        if directory.exists():
            for archive in directory.iterdir():
                if archive.is_file():
                    archive.unlink()


def list_archives(directory: Path):
    if not directory.exists():
        return []
    return sorted(directory.glob("*.tar.gz"), key=lambda path: path.name)


def archive_members(archive_path: Path):
    with tarfile.open(archive_path, "r:gz") as archive:
        return archive.getnames()


def test_settings_update_creates_db_only_automatic_backup():
    db = SessionLocal()
    try:
        clear_backup_dirs()
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        key = f"backup_setting_{uuid.uuid4().hex[:8]}"
        response = client.post(
            "/settings/update",
            data={"key": key, "value": "123", "next": "/settings"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        archives = list_archives(AUTO_BACKUP_DIR)
        assert len(archives) == 1
        names = archive_members(archives[0])
        assert "db.sqlite3" in names
        assert not any(name == "attachments" or name.startswith("attachments/") for name in names)
    finally:
        db.query(Settings).filter(Settings.key.like("backup_setting_%")).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_attachment_upload_creates_full_automatic_backup():
    db = SessionLocal()
    filename = f"backup-upload-{uuid.uuid4().hex[:8]}.pdf"
    file_path = UPLOAD_PATH / filename
    try:
        clear_backup_dirs()
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        response = client.post(
            "/attachments/upload",
            data={"next": "/attachments"},
            files={"file": (filename, b"%PDF-1.4\n%backup-test\n", "application/pdf")},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert file_path.exists()
        archives = list_archives(AUTO_BACKUP_DIR)
        assert len(archives) == 1
        names = archive_members(archives[0])
        assert "db.sqlite3" in names
        assert f"attachments/{filename}" in names
    finally:
        db.query(Attachment).filter(Attachment.filename == filename).delete(synchronize_session=False)
        db.commit()
        if file_path.exists():
            file_path.unlink()
        db.close()


def test_attachment_linking_creates_full_automatic_backup():
    db = SessionLocal()
    filename = f"backup-linked-{uuid.uuid4().hex[:8]}.pdf"
    file_path = UPLOAD_PATH / filename
    apartment = None
    attachment = None
    try:
        clear_backup_dirs()
        create_admin(db)
        UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"%PDF-1.4\n%linked-backup-test\n")
        attachment = Attachment(
            filename=filename,
            disk_path=str(file_path),
            mimetype="application/pdf",
            size=file_path.stat().st_size,
        )
        apartment = Apartment(name=f"Backup Apt {uuid.uuid4().hex[:8]}")
        db.add_all([attachment, apartment])
        db.commit()

        client = TestClient(app)
        login_admin(client)

        response = client.post(
            "/money/expenses/add",
            data={
                "gross_amount": "80.00",
                "date": "2025-01-01",
                "apartment_id": str(apartment.id),
                "notes": f"expense-with-attachment-{uuid.uuid4().hex[:8]}",
                "attachment_ids": str(attachment.id),
                "next": "/overview?year=2025",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        db.expire_all()
        linked_attachment = db.query(Attachment).filter(Attachment.id == attachment.id).first()
        assert linked_attachment is not None
        assert linked_attachment.expense_id is not None
        archives = list_archives(AUTO_BACKUP_DIR)
        assert len(archives) == 1
        names = archive_members(archives[0])
        assert f"attachments/{filename}" in names
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete(synchronize_session=False)
        if apartment is not None:
            db.query(Apartment).filter(Apartment.id == apartment.id).delete(synchronize_session=False)
        db.commit()
        if file_path.exists():
            file_path.unlink()
        db.close()
def test_add_expense_calculates_gross_from_net():
    db = SessionLocal()
    expense = None
    try:
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        note = f"expense-from-net-{uuid.uuid4().hex[:8]}"
        response = client.post(
            "/money/expenses/add",
            data={
                "net_amount": "78.00",
                "vat_percent": "22.0",
                "date": "2025-01-01",
                "notes": note,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        db.expire_all()
        expense = db.query(Expense).filter(Expense.notes == note).first()
        assert expense is not None
        assert float(expense.net_amount) == 78.0
        assert float(expense.gross_amount) == 100.0
    finally:
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete(synchronize_session=False)
            db.commit()
        db.close()


def test_manual_backup_is_not_pruned_by_automatic_rotation():
    db = SessionLocal()
    try:
        clear_backup_dirs()
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        response = client.post(
            "/settings/update",
            data={"key": "backup_auto_retention", "value": "1", "next": "/settings"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert len(list_archives(AUTO_BACKUP_DIR)) == 1

        response = client.post(
            "/settings/backup/manual",
            data={"next": "/settings"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "backup=manual_success" in response.headers["location"]

        response = client.post(
            "/settings/backup/manual",
            data={"next": "/settings"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert len(list_archives(MANUAL_BACKUP_DIR)) == 2

        response = client.post(
            "/settings/update",
            data={"key": f"rotation_check_{uuid.uuid4().hex[:8]}", "value": "ok", "next": "/settings"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert len(list_archives(AUTO_BACKUP_DIR)) == 1
        assert len(list_archives(MANUAL_BACKUP_DIR)) == 2
    finally:
        db.query(Settings).filter(
            Settings.key.in_(["backup_auto_retention"]) | Settings.key.like("rotation_check_%")
        ).delete(synchronize_session=False)
        db.commit()
        db.close()