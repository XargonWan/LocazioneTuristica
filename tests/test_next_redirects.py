import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Apartment, Attachment, Cleaning, Company, Expense, Income, Settings, User
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


def test_attachment_upload_attaches_to_expense():
    db = SessionLocal()
    attachment = None
    expense = None
    file_path = None
    try:
        create_admin(db)
        expense = Expense(date='2025-01-01', gross_amount=10.0, net_amount=10.0, vat_percent=0.0, notes=f'attachment-expense-{uuid.uuid4().hex[:8]}')
        db.add(expense)
        db.commit()
        db.refresh(expense)

        client = TestClient(app)
        login_admin(client)

        filename = f'expense-attachment-{uuid.uuid4().hex[:8]}.pdf'
        response = client.post(
            '/attachments/upload',
            data={'next': f'/money/expenses/{expense.id}/edit', 'expense_id': str(expense.id)},
            files={'file': (filename, b'%PDF-1.4\n%expense-test\n', 'application/pdf')},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers['location'] == f'/money/expenses/{expense.id}/edit'
        attachment = db.query(Attachment).filter(Attachment.filename == filename).order_by(Attachment.id.desc()).first()
        assert attachment is not None
        assert attachment.expense_id == expense.id
        assert attachment.income_id is None
        file_path = attachment.disk_path
        assert os.path.exists(file_path)
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete()
        db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_attachment_upload_attaches_to_income():
    db = SessionLocal()
    attachment = None
    income = None
    file_path = None
    try:
        create_admin(db)
        income = Income(date='2025-01-01', gross_amount=10.0, net_amount=10.0, vat_percent=0.0, notes=f'attachment-income-{uuid.uuid4().hex[:8]}')
        db.add(income)
        db.commit()
        db.refresh(income)

        client = TestClient(app)
        login_admin(client)

        filename = f'income-attachment-{uuid.uuid4().hex[:8]}.pdf'
        response = client.post(
            '/attachments/upload',
            data={'next': f'/money/incomes/{income.id}/edit', 'income_id': str(income.id)},
            files={'file': (filename, b'%PDF-1.4\n%income-test\n', 'application/pdf')},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers['location'] == f'/money/incomes/{income.id}/edit'
        attachment = db.query(Attachment).filter(Attachment.filename == filename).order_by(Attachment.id.desc()).first()
        assert attachment is not None
        assert attachment.income_id == income.id
        assert attachment.expense_id is None
        file_path = attachment.disk_path
        assert os.path.exists(file_path)
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
        if income is not None:
            db.query(Income).filter(Income.id == income.id).delete()
        db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_expenses_create_mode_hides_history_and_filters_linked_attachments():
    db = SessionLocal()
    expense = None
    unattached = None
    linked = None
    try:
        create_admin(db)
        expense = Expense(date='2025-01-01', gross_amount=25.0, net_amount=25.0, vat_percent=0.0, notes=f'create-mode-expense-{uuid.uuid4().hex[:8]}')
        db.add(expense)
        db.commit()
        db.refresh(expense)

        linked = Attachment(
            filename=f'linked-{uuid.uuid4().hex[:8]}.pdf',
            disk_path=f'/tmp/linked-{uuid.uuid4().hex[:8]}.pdf',
            mimetype='application/pdf',
            size=10,
            expense_id=expense.id,
        )
        unattached = Attachment(
            filename=f'unattached-{uuid.uuid4().hex[:8]}.pdf',
            disk_path=f'/tmp/unattached-{uuid.uuid4().hex[:8]}.pdf',
            mimetype='application/pdf',
            size=10,
        )
        db.add_all([linked, unattached])
        db.commit()
        db.refresh(linked)
        db.refresh(unattached)

        client = TestClient(app)
        login_admin(client)

        response = client.get('/money/expenses?mode=create&next=/overview?year=2025')

        assert response.status_code == 200
        assert 'Aggiungi spesa' in response.text
        assert expense.notes not in response.text
        assert linked.filename not in response.text
        assert unattached.filename in response.text
        assert 'Documenti nel menu' in response.text
    finally:
        if linked is not None:
            db.query(Attachment).filter(Attachment.id == linked.id).delete()
        if unattached is not None:
            db.query(Attachment).filter(Attachment.id == unattached.id).delete()
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete()
        db.commit()
        db.close()


def test_attachment_view_renders_pdf_preview_and_inline_content():
    db = SessionLocal()
    attachment = None
    file_path = None
    try:
        create_admin(db)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f'view-attachment-{uuid.uuid4().hex[:8]}.pdf'
        file_path = os.path.join(UPLOAD_DIR, filename)
        with open(file_path, 'wb') as handle:
            handle.write(b'%PDF-1.4\n%viewer-test\n')

        attachment = Attachment(
            filename=filename,
            disk_path=file_path,
            mimetype='application/pdf',
            size=os.path.getsize(file_path),
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)

        client = TestClient(app)
        login_admin(client)

        response = client.get(f'/attachments/{attachment.id}/view?next=/overview?year=2025')

        assert response.status_code == 200
        assert 'href="/overview?year=2025"' in response.text
        assert f'/attachments/{attachment.id}/inline' in response.text
        assert f'/attachments/download/{attachment.id}' in response.text
        assert f'/attachments/{attachment.id}/delete' in response.text
        assert 'title="Elimina"' in response.text
        assert '🗑️' in response.text

        fragment_response = client.get(f'/attachments/{attachment.id}/view?next=/overview?year=2025&fragment=1')
        assert fragment_response.status_code == 200
        assert 'attachment-preview-panel' in fragment_response.text
        assert '<!doctype html>' not in fragment_response.text.lower()

        inline_response = client.get(f'/attachments/{attachment.id}/inline', follow_redirects=False)
        assert inline_response.status_code == 200
        assert inline_response.headers['content-type'].startswith('application/pdf')
        assert 'inline' in inline_response.headers.get('content-disposition', '')
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
            db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_attachment_delete_uses_attachment_context_and_removes_file():
    db = SessionLocal()
    attachment = None
    expense = None
    file_path = None
    try:
        create_admin(db)
        expense = Expense(date='2025-01-01', gross_amount=10.0, net_amount=10.0, vat_percent=0.0, notes=f'delete-attachment-expense-{uuid.uuid4().hex[:8]}')
        db.add(expense)
        db.commit()
        db.refresh(expense)

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f'delete-attachment-{uuid.uuid4().hex[:8]}.pdf'
        file_path = os.path.join(UPLOAD_DIR, filename)
        with open(file_path, 'wb') as handle:
            handle.write(b'%PDF-1.4\n%delete-test\n')

        attachment = Attachment(
            filename=filename,
            disk_path=file_path,
            mimetype='application/pdf',
            size=os.path.getsize(file_path),
            expense_id=expense.id,
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)

        client = TestClient(app)
        login_admin(client)

        response = client.post(f'/attachments/{attachment.id}/delete', follow_redirects=False)

        assert response.status_code == 303
        assert response.headers['location'] == f'/money/expenses/{expense.id}/edit'
        assert db.query(Attachment).filter(Attachment.id == attachment.id).first() is None
        assert not os.path.exists(file_path)
    finally:
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete()
        db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_add_expense_uploads_new_files_on_submit():
    db = SessionLocal()
    expense = None
    attachment = None
    file_path = None
    try:
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        filename = f'add-expense-submit-{uuid.uuid4().hex[:8]}.pdf'
        response = client.post(
            '/money/expenses/add',
            data={
                'date': '2025-04-01',
                'gross_amount': '80.00',
                'vat_percent': '22.0',
                'notes': f'add-expense-submit-{uuid.uuid4().hex[:8]}',
                'next': '/overview?year=2025',
            },
            files={'file': (filename, b'%PDF-1.4\n%expense-submit\n', 'application/pdf')},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'
        expense = db.query(Expense).filter(Expense.notes.like('add-expense-submit-%')).order_by(Expense.id.desc()).first()
        assert expense is not None
        attachment = db.query(Attachment).filter(Attachment.filename == filename).order_by(Attachment.id.desc()).first()
        assert attachment is not None
        assert attachment.expense_id == expense.id
        file_path = attachment.disk_path
        assert os.path.exists(file_path)
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete()
        db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_edit_expense_uploads_new_files_on_submit():
    db = SessionLocal()
    expense = None
    attachment = None
    file_path = None
    try:
        create_admin(db)
        expense = Expense(date='2025-05-01', gross_amount=10.0, net_amount=7.8, vat_percent=22.0, notes=f'edit-expense-submit-{uuid.uuid4().hex[:8]}')
        db.add(expense)
        db.commit()
        db.refresh(expense)

        client = TestClient(app)
        login_admin(client)

        filename = f'edit-expense-submit-{uuid.uuid4().hex[:8]}.pdf'
        response = client.post(
            f'/money/expenses/{expense.id}/edit',
            data={
                'date': '2025-05-02',
                'gross_amount': '19.22',
                'vat_percent': '22.0',
                'notes': expense.notes,
                'recurrence': 'none',
                'apply_to': 'single',
                'next': '/overview?year=2025',
            },
            files={'file': (filename, b'%PDF-1.4\n%expense-edit-submit\n', 'application/pdf')},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'
        db.refresh(expense)
        assert expense.date == '2025-05-02'
        attachment = db.query(Attachment).filter(Attachment.filename == filename).order_by(Attachment.id.desc()).first()
        assert attachment is not None
        assert attachment.expense_id == expense.id
        file_path = attachment.disk_path
        assert os.path.exists(file_path)
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete()
        db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_add_income_uploads_new_files_on_submit():
    db = SessionLocal()
    income = None
    attachment = None
    file_path = None
    try:
        create_admin(db)
        client = TestClient(app)
        login_admin(client)

        filename = f'add-income-submit-{uuid.uuid4().hex[:8]}.pdf'
        response = client.post(
            '/money/incomes/add',
            data={
                'date': '2025-06-01',
                'gross_amount': '100.00',
                'vat_percent': '22.0',
                'pm_percent': '0.0',
                'notes': f'add-income-submit-{uuid.uuid4().hex[:8]}',
                'next': '/overview?year=2025',
            },
            files={'file': (filename, b'%PDF-1.4\n%income-submit\n', 'application/pdf')},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'
        income = db.query(Income).filter(Income.notes.like('add-income-submit-%')).order_by(Income.id.desc()).first()
        assert income is not None
        attachment = db.query(Attachment).filter(Attachment.filename == filename).order_by(Attachment.id.desc()).first()
        assert attachment is not None
        assert attachment.income_id == income.id
        file_path = attachment.disk_path
        assert os.path.exists(file_path)
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
        if income is not None:
            db.query(Income).filter(Income.id == income.id).delete()
        db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_edit_income_uploads_new_files_on_submit():
    db = SessionLocal()
    income = None
    attachment = None
    file_path = None
    try:
        create_admin(db)
        income = Income(date='2025-07-01', gross_amount=50.0, net_amount=39.0, vat_percent=22.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=39.0, notes=f'edit-income-submit-{uuid.uuid4().hex[:8]}')
        db.add(income)
        db.commit()
        db.refresh(income)

        client = TestClient(app)
        login_admin(client)

        filename = f'edit-income-submit-{uuid.uuid4().hex[:8]}.pdf'
        response = client.post(
            f'/money/incomes/{income.id}/edit',
            data={
                'date': '2025-07-02',
                'gross_amount': '55.00',
                'vat_percent': '22.0',
                'pm_percent': '0.0',
                'notes': income.notes,
                'recurrence': 'none',
                'apply_to': 'single',
                'next': '/overview?year=2025',
            },
            files={'file': (filename, b'%PDF-1.4\n%income-edit-submit\n', 'application/pdf')},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers['location'] == '/overview?year=2025'
        db.refresh(income)
        assert income.date == '2025-07-02'
        attachment = db.query(Attachment).filter(Attachment.filename == filename).order_by(Attachment.id.desc()).first()
        assert attachment is not None
        assert attachment.income_id == income.id
        file_path = attachment.disk_path
        assert os.path.exists(file_path)
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
        if income is not None:
            db.query(Income).filter(Income.id == income.id).delete()
        db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()


def test_attachment_upload_accepts_multiple_files_for_expense():
    db = SessionLocal()
    expense = None
    attachments = []
    try:
        create_admin(db)
        expense = Expense(date='2025-01-01', gross_amount=10.0, net_amount=10.0, vat_percent=0.0, notes=f'multi-attachment-expense-{uuid.uuid4().hex[:8]}')
        db.add(expense)
        db.commit()
        db.refresh(expense)

        client = TestClient(app)
        login_admin(client)

        filename_one = f'multi-expense-{uuid.uuid4().hex[:8]}-1.pdf'
        filename_two = f'multi-expense-{uuid.uuid4().hex[:8]}-2.pdf'
        response = client.post(
            '/attachments/upload',
            data={'next': f'/money/expenses/{expense.id}/edit', 'expense_id': str(expense.id)},
            files=[
                ('file', (filename_one, b'%PDF-1.4\n%multi-one\n', 'application/pdf')),
                ('file', (filename_two, b'%PDF-1.4\n%multi-two\n', 'application/pdf')),
            ],
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers['location'] == f'/money/expenses/{expense.id}/edit'
        attachments = db.query(Attachment).filter(Attachment.filename.in_([filename_one, filename_two])).order_by(Attachment.id.asc()).all()
        assert len(attachments) == 2
        assert all(attachment.expense_id == expense.id for attachment in attachments)
        assert all(os.path.exists(attachment.disk_path) for attachment in attachments)
    finally:
        if attachments:
            for attachment in attachments:
                db.query(Attachment).filter(Attachment.id == attachment.id).delete()
            db.commit()
            for attachment in attachments:
                if attachment.disk_path and os.path.exists(attachment.disk_path):
                    os.remove(attachment.disk_path)
        if expense is not None:
            db.query(Expense).filter(Expense.id == expense.id).delete()
            db.commit()
        db.close()


def test_overview_shows_attachment_clip_for_linked_income():
    db = SessionLocal()
    income = None
    attachment = None
    file_path = None
    try:
        create_admin(db)
        income = Income(date='2042-01-01', gross_amount=100.0, net_amount=78.0, vat_percent=22.0, notes=f'overview-attachment-income-{uuid.uuid4().hex[:8]}')
        db.add(income)
        db.commit()
        db.refresh(income)

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f'overview-attachment-{uuid.uuid4().hex[:8]}.pdf'
        file_path = os.path.join(UPLOAD_DIR, filename)
        with open(file_path, 'wb') as handle:
            handle.write(b'%PDF-1.4\n%overview-attachment\n')

        attachment = Attachment(
            filename=filename,
            disk_path=file_path,
            mimetype='application/pdf',
            size=os.path.getsize(file_path),
            income_id=income.id,
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)

        client = TestClient(app)
        login_admin(client)

        response = client.get('/overview?year=2042')

        assert response.status_code == 200
        assert '📎' in response.text
        assert f'ovAttachmentModal-income-{income.id}' in response.text
        assert f'/attachments/{attachment.id}/view?next=' in response.text
        assert f'/attachments/download/{attachment.id}' in response.text
        assert f'/attachments/{attachment.id}/delete' not in response.text
    finally:
        if attachment is not None:
            db.query(Attachment).filter(Attachment.id == attachment.id).delete()
            db.commit()
        if income is not None:
            db.query(Income).filter(Income.id == income.id).delete()
            db.commit()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        db.close()