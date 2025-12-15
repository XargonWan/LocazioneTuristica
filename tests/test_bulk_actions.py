from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models import Income, Expense, Recurrence

client = TestClient(app)
client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})


def test_bulk_edit_incomes():
    db = SessionLocal()
    try:
        # create incomes
        i1 = Income(apartment_id=None, platform_id=None, date='2025-01-01', gross_amount=100.0, vat_percent=22.0, net_amount=78.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=78.0, notes='a')
        i2 = Income(apartment_id=None, platform_id=None, date='2025-02-01', gross_amount=200.0, vat_percent=22.0, net_amount=156.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=156.0, notes='b')
        i3 = Income(apartment_id=None, platform_id=None, date='2025-03-01', gross_amount=300.0, vat_percent=22.0, net_amount=234.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=234.0, notes='c')
        db.add_all([i1, i2, i3])
        db.commit()
        ids = f"{i1.id},{i2.id},{i3.id}"
        resp = client.post('/money/incomes/bulk_edit', data={'ids': ids, 'notes': 'bulk-note', 'pm_percent': '10.0'})
        assert resp.status_code in (200, 303)
        db.refresh(i1); db.refresh(i2); db.refresh(i3)
        assert i1.notes == 'bulk-note' and i2.notes == 'bulk-note' and i3.notes == 'bulk-note'
        assert float(i1.pm_percent) == 10.0
    finally:
        # cleanup
        db.query(Income).filter(Income.id.in_([i1.id, i2.id, i3.id])).delete()
        db.commit()
        db.close()


def test_bulk_delete_incomes_series():
    db = SessionLocal()
    try:
        # create recurrence and incomes
        r = Recurrence(type='monthly', start_date='2025-01-01')
        db.add(r); db.commit()
        i1 = Income(apartment_id=None, platform_id=None, date='2025-01-01', gross_amount=100.0, vat_percent=22.0, net_amount=78.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=78.0, notes='x', recurrence_id=r.id)
        i2 = Income(apartment_id=None, platform_id=None, date='2025-02-01', gross_amount=100.0, vat_percent=22.0, net_amount=78.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=78.0, notes='y', recurrence_id=r.id)
        db.add_all([i1, i2]); db.commit()
        # delete series via bulk_delete
        rid = r.id
        resp = client.post('/money/incomes/bulk_delete', data={'ids': str(i1.id), 'delete_series_if_present': 'on'})
        assert resp.status_code in (200, 303)
        remaining = db.query(Income).filter(Income.recurrence_id == rid).all()
        assert len(remaining) == 0
        # recurrence removed
        rec = db.query(Recurrence).filter(Recurrence.id == rid).first()
        assert rec is None
    finally:
        db.commit(); db.close()


def test_bulk_edit_expenses():
    db = SessionLocal()
    try:
        e1 = Expense(apartment_id=None, date='2025-01-01', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=39.0, notes='a')
        e2 = Expense(apartment_id=None, date='2025-02-01', gross_amount=60.0, vat_percent=22.0, net_amount=46.8, pm_percent=0.0, pm_amount=0.0, net_after_pm=46.8, notes='b')
        db.add_all([e1, e2]); db.commit()
        ids = f"{e1.id},{e2.id}"
        resp = client.post('/money/expenses/bulk_edit', data={'ids': ids, 'notes': 'bulk-exp', 'pm_percent': '5.0'})
        assert resp.status_code in (200, 303)
        db.refresh(e1); db.refresh(e2)
        assert e1.notes == 'bulk-exp' and e2.notes == 'bulk-exp'
        assert float(e1.pm_percent) == 5.0
    finally:
        db.query(Expense).filter(Expense.id.in_([e1.id, e2.id])).delete()
        db.commit(); db.close()


def test_bulk_delete_expenses():
    db = SessionLocal()
    try:
        e1 = Expense(apartment_id=None, date='2025-03-01', gross_amount=70.0, vat_percent=22.0, net_amount=54.6, pm_percent=0.0, pm_amount=0.0, net_after_pm=54.6, notes='x')
        e2 = Expense(apartment_id=None, date='2025-04-01', gross_amount=80.0, vat_percent=22.0, net_amount=62.4, pm_percent=0.0, pm_amount=0.0, net_after_pm=62.4, notes='y')
        db.add_all([e1, e2]); db.commit()
        resp = client.post('/money/expenses/bulk_delete', data={'ids': f"{e1.id},{e2.id}"})
        assert resp.status_code in (200, 303)
        rem = db.query(Expense).filter(Expense.id.in_([e1.id, e2.id])).all()
        assert len(rem) == 0
    finally:
        db.commit(); db.close()


def test_bulk_delete_expenses_redirects_to_next():
    db = SessionLocal()
    try:
        e1 = Expense(apartment_id=None, date='2025-05-01', gross_amount=10.0, vat_percent=22.0, net_amount=7.8, pm_percent=0.0, pm_amount=0.0, net_after_pm=7.8, notes='n1')
        db.add(e1); db.commit()
        resp = client.post('/money/expenses/bulk_delete', data={'ids': str(e1.id), 'next': '/overview'})
        assert resp.status_code in (200, 303)
        # FastAPI TestClient follows redirects by default; ensure final path is /overview
        assert resp.request.url.path == '/overview'
        rem = db.query(Expense).filter(Expense.id == e1.id).first()
        assert rem is None
    finally:
        db.commit(); db.close()
