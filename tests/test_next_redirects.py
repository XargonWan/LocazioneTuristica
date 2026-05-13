import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Apartment, Attachment, Cleaning, Company, Expense, Settings, User
from app.routers.attachments import UPLOAD_DIR
from app.routers.auth import pwd_context

pytestmark = pytest.mark.db_backup


def create_admin(db):
    user = db.query(User).filter(User.username == 'testadmin').first()
    if user:
        return user
    user = User(username='testadmin', role='admin', must_change_password=False)
    user.password_hash = pwd_context.hash('secret')
    db.add(user)
    db.commit()
    return user


def login_admin(client):
    response = client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
    assert response.status_code in (200, 303)


def test_add_cleaning_redirects_to_next():
    db = SessionLocal()
    cleaning = None
    expense = None
    apartment = None
    company = None
    try:
        create_admin(db)
        apartment = Apartment(name=f'APT-{uuid.uuid4().hex[:8]}')
        company = Company(company_name=f'CleanCo-{uuid.uuid4().hex[:8]}', is_cleaning_company=True)
        db.add_all([apartment, company])
        db.commit()
        db.refresh(apartment)
        db.refresh(company)

        note = f'cleaning-next-{uuid.uuid4().hex[:8]}'
        client = TestClient(app)
        login_admin(client)

        response = client.post(
            '/cleaning/add',
            data={
                'date': '2025-01-01',
                'apartment_id': str(apartment.id),
                'company_id': str(company.id),
                'gross_amount': '45.00',
                'vat_percent': '22.0',
                'notes': note,
                'next': '/overview?year=2025'
            },
            follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'

        cleaning = db.query(Cleaning).filter(Cleaning.notes == note).order_by(Cleaning.id.desc()).first()
        assert cleaning is not None
        if cleaning.expense_id:
            expense = db.query(Expense).filter(Expense.id == cleaning.expense_id).first()
    finally:
        if cleaning is not None:
            db.query(Cleaning).filter(Cleaning.id == cleaning.id).delete()
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete()
        if apartment is not None:
            db.query(Apartment).filter(Apartment.id == apartment.id).delete()
        if company is not None:
            db.query(Company).filter(Company.id == company.id).delete()
        db.commit()
        db.close()


def test_settings_update_redirects_to_next():
    db = SessionLocal()
    try:
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        key = f'unit_test_{uuid.uuid4().hex[:8]}'
        response = client.post(
            '/settings/update',
            data={'key': key, 'value': '123', 'next': '/overview?year=2025'},
            follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'
        setting = db.query(Settings).filter(Settings.key == key).first()
        assert setting is not None
        assert setting.value == '123'
    finally:
        db.query(Settings).filter(Settings.key.like('unit_test_%')).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_add_user_redirects_to_next():
    db = SessionLocal()
    created_user = None
    try:
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        username = f'user_{uuid.uuid4().hex[:8]}'
        response = client.post(
            '/auth/users/add',
            data={'username': username, 'role': 'readonly', 'next': '/overview?year=2025'},
            follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'
        created_user = db.query(User).filter(User.username == username).first()
        assert created_user is not None
    finally:
        if created_user is not None:
            db.query(User).filter(User.id == created_user.id).delete()
            db.commit()
        db.close()


def test_attachment_upload_redirects_to_next():
    db = SessionLocal()
    attachment = None
    file_path = None
    try:
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        filename = f'test-{uuid.uuid4().hex[:8]}.pdf'
        response = client.post(
            '/attachments/upload',
            data={'next': '/overview?year=2025'},
            files={'file': (filename, b'%PDF-1.4\n%test\n', 'application/pdf')},
            follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'
        attachment = db.query(Attachment).filter(Attachment.filename == filename).order_by(Attachment.id.desc()).first()
        assert attachment is not None
        file_path = attachment.disk_path
        assert os.path.exists(file_path)
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
            db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()