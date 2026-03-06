from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models import Income, Expense, User, Recurrence
from app.routers.auth import pwd_context


def create_admin(db):
    u = db.query(User).filter(User.username == 'testadmin').first()
    if u:
        return u
    u = User(username='testadmin', role='admin', must_change_password=False)
    u.password_hash = pwd_context.hash('secret')
    db.add(u)
    db.commit()
    return u


def test_income_edit_creates_recurrence():
    db = SessionLocal()
    try:
        # create admin and create single income
        admin = create_admin(db)
        import uuid
        note = f'one-off-{uuid.uuid4().hex[:8]}'
        inc = Income(apartment_id=None, platform_id=None, date='2025-12-01', gross_amount=100.0, vat_percent=22.0, net_amount=78.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=78.0, notes=note)
        db.add(inc)
        db.commit()
        client = TestClient(app)
        # login
        r = client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        assert r.status_code in (200, 303)
        # edit and set recurrence
        r = client.post(f'/money/incomes/{inc.id}/edit', data={'gross_amount': '100.0', 'vat_percent': '22.0', 'pm_percent': '0.0', 'date': '2025-12-01', 'recurrence': 'monthly', 'notes': note})
        assert r.status_code in (200, 303)
        db.refresh(inc)
        assert inc.recurrence_id is not None
        occs = db.query(Income).filter(Income.recurrence_id == inc.recurrence_id).all()
        assert len(occs) >= 2
    finally:
        db.close()


def test_expense_edit_creates_recurrence():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        import uuid
        note = f'occasional-{uuid.uuid4().hex[:8]}'
        e = Expense(apartment_id=None, date='2025-11-05', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=39.0, notes=note)
        db.add(e)
        db.commit()
        client = TestClient(app)
        r = client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        assert r.status_code in (200, 303)
        r = client.post(f'/money/expenses/{e.id}/edit', data={'gross_amount': '50.0', 'vat_percent': '22.0', 'pm_percent': '0.0', 'date': '2025-11-05', 'recurrence': 'monthly', 'notes': note})
        assert r.status_code in (200, 303)
        db.refresh(e)
        assert e.recurrence_id is not None
        occs = db.query(Expense).filter(Expense.recurrence_id == e.recurrence_id).all()
        assert len(occs) >= 2
    finally:
        db.close()
