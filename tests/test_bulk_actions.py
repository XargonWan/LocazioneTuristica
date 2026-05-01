from fastapi.testclient import TestClient
import subprocess, os

# ensure cleaning columns/tables exist before tests run
subprocess.call([os.getenv('PYTHON', 'python'), 'scripts/migrate_add_cleaning.py'])
# dispose engine so any cached sqlite connection is closed and will see new schema
from app.db import engine
engine.dispose()
from app.main import app
from app.db import SessionLocal
from app.models import Income, Expense, Recurrence, Company, Cleaning, CleaningService, Apartment

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
        resp = client.post('/money/expenses/bulk_edit', data={'ids': ids, 'notes': 'bulk-exp'}, follow_redirects=False)
        assert resp.status_code in (200, 303)
        db.refresh(e1); db.refresh(e2)
        assert e1.notes == 'bulk-exp' and e2.notes == 'bulk-exp'
        assert float(e1.pm_percent) == 5.0
    finally:
        db.query(Expense).filter(Expense.id.in_([e1.id, e2.id])).delete()
        db.commit(); db.close()


def test_bulk_mark_cleaning_expenses():
    db = SessionLocal()
    try:
        e1 = Expense(apartment_id=None, date='2025-07-01', gross_amount=20.0, vat_percent=22.0, net_amount=15.6, pm_percent=0.0, pm_amount=0.0, net_after_pm=15.6, notes='foo')
        e2 = Expense(apartment_id=None, date='2025-07-02', gross_amount=30.0, vat_percent=22.0, net_amount=23.4, pm_percent=0.0, pm_amount=0.0, net_after_pm=23.4, notes='bar')
        db.add_all([e1, e2]); db.commit()
        ids = f"{e1.id},{e2.id}"
        resp = client.post('/money/expenses/bulk_edit', data={'ids': ids, 'is_cleaning': '1'})
        assert resp.status_code in (200, 303)
        db.refresh(e1); db.refresh(e2)
        assert e1.is_cleaning and e2.is_cleaning
    finally:
        db.query(Expense).filter(Expense.id.in_([e1.id, e2.id])).delete()
        db.commit(); db.close()


def test_bulk_delete_expenses():
    db = SessionLocal()
    try:
        e1 = Expense(apartment_id=None, date='2025-03-01', gross_amount=70.0, vat_percent=22.0, net_amount=54.6, pm_percent=0.0, pm_amount=0.0, net_after_pm=54.6, notes='x')
        e2 = Expense(apartment_id=None, date='2025-04-01', gross_amount=80.0, vat_percent=22.0, net_amount=62.4, pm_percent=0.0, pm_amount=0.0, net_after_pm=62.4, notes='y')
        db.add_all([e1, e2]); db.commit()
        resp = client.post('/money/expenses/bulk_delete', data={'ids': f"{e1.id},{e2.id}"}, follow_redirects=False)
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
        resp = client.post('/money/expenses/bulk_delete', data={'ids': str(e1.id), 'next': '/overview'}, follow_redirects=False)
        assert resp.status_code in (200, 303)
        # FastAPI TestClient follows redirects by default; ensure final path is /overview
        assert resp.request.url.path == '/overview'
        rem = db.query(Expense).filter(Expense.id == e1.id).first()
        assert rem is None
    finally:
        db.commit(); db.close()


def test_overview_page_net_calculation():
    # create a simple income and an expense paid to a PM to exercise overview compute logic
    db = SessionLocal()
    try:
        # make a property manager to associate with the expense
        pm = PropertyManager(first_name='Foo', last_name='Bar', percent=0.0)
        db.add(pm)
        db.commit()
        inc = Income(apartment_id=None, platform_id=None, date='2025-01-01', gross_amount=100.0,
                     vat_percent=22.0, net_amount=80.0, pm_percent=10.0, pm_amount=10.0, net_after_pm=70.0,
                     notes='rent')
        # expense small enough to keep pm_due positive when subtracted
        exp = Expense(apartment_id=None, date='2025-01-01', gross_amount=5.0,
                      vat_percent=22.0, net_amount=3.9, pm_percent=0.0, pm_amount=0.0, net_after_pm=3.9,
                      associated_pm_id=pm.id,
                      notes='payment')
        db.add_all([inc, exp]); db.commit()
        resp = client.get('/overview?year=2025')
        assert resp.status_code == 200
        text = resp.text
        # the month total should be net_after_pm income (70) minus gross expense (5) = 65
        assert 'Gennaio - <span class="net-total net-positive">€65.00' in text
        # pm_due initially 10 from income, minus 5 payment = 5
        assert 'PM dovuto: €5.00' in text
    finally:
        # cleanup inserted data
        db.query(Income).filter(Income.id == inc.id).delete()
        db.query(Expense).filter(Expense.id == exp.id).delete()
        db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
        db.commit()
        db.close()



def test_create_cleaning_creates_flagged_expense():
    db = SessionLocal()
    try:
        # create a cleaning company and service
        comp = Company(company_name='CleanCo', is_cleaning_company=True)
        db.add(comp); db.commit(); db.refresh(comp)
        svc = CleaningService(company_id=comp.id, name='Standard', default_amount=30.0, is_net=False, vat_percent=22.0)
        db.add(svc); db.commit(); db.refresh(svc)
        # create an apartment to reference
        apt = Apartment(name='A1')
        db.add(apt); db.commit(); db.refresh(apt)
        # call cleaning add endpoint
        resp = client.post('/cleaning/add', data={'date':'2025-06-01','apartment_id':apt.id,'company_id':comp.id,'service_id':svc.id})
        assert resp.status_code in (200, 303)
        # verify cleaning record created
        cl = db.query(Cleaning).filter(Cleaning.apartment_id == apt.id, Cleaning.company_id == comp.id).first()
        assert cl is not None
        # expense should exist and be flagged
        exp = db.query(Expense).filter(Expense.id == cl.expense_id).first()
        assert exp is not None
        assert exp.is_cleaning
        assert float(exp.gross_amount) == 30.0
    finally:
        # cleanup everything
        if cl:
            if cl.expense_id:
                db.query(Expense).filter(Expense.id == cl.expense_id).delete()
            db.delete(cl)
        if svc:
            db.delete(svc)
        if comp:
            db.delete(comp)
        if apt:
            db.delete(apt)
        db.commit()
        db.close()


def test_cleaning_service_crud():
    db = SessionLocal()
    try:
        # create cleaning company
        comp = Company(company_name='SvcCo', is_cleaning_company=True)
        db.add(comp); db.commit(); db.refresh(comp)
        # add service via endpoint
        resp = client.post('/cleaning/service/add', data={'company_id': comp.id, 'name': 'Deep', 'default_amount': 100.0, 'vat_percent': 22.0})
        assert resp.status_code in (200, 303)
        svc = db.query(CleaningService).filter(CleaningService.company_id == comp.id, CleaningService.name=='Deep').first()
        assert svc is not None
        # edit service
        resp2 = client.post(f'/cleaning/service/{svc.id}/edit', data={'company_id': comp.id, 'name': 'Deep Clean', 'default_amount': 120.0, 'vat_percent': 22.0})
        assert resp2.status_code in (200, 303)
        db.refresh(svc)
        assert svc.name == 'Deep Clean'
        # delete service
        resp3 = client.post(f'/cleaning/service/{svc.id}/delete')
        assert resp3.status_code in (200, 303)
        svc2 = db.query(CleaningService).filter(CleaningService.id == svc.id).first()
        assert svc2 is None
    finally:
        if comp:
            db.delete(comp)
            db.commit()
        db.close()


def test_api_stats_monthly_includes_net_and_expense():
    db = SessionLocal()
    try:
        inc = Income(apartment_id=None, platform_id=None, date='2025-02-01', gross_amount=200.0,
                     vat_percent=22.0, net_amount=160.0, pm_percent=5.0, pm_amount=8.0, net_after_pm=152.0,
                     notes='room')
        exp = Expense(apartment_id=None, date='2025-02-15', gross_amount=50.0,
                      vat_percent=22.0, net_amount=41.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=41.0,
                      notes='clean', is_cleaning=True)
        db.add_all([inc, exp]); db.commit()
        resp = client.get('/api/stats/monthly?year=2025')
        assert resp.status_code == 200
        data = resp.json()['data']
        feb = next((m for m in data if m['month'] == 2), None)
        assert feb is not None
        # should equal net_after_pm income (152) and gross expense (50)
        assert feb['income'] == 152.0
        assert feb['expense'] == 50.0
    finally:
        db.query(Income).filter(Income.id == inc.id).delete()
        db.query(Expense).filter(Expense.id == exp.id).delete()
        db.commit()
        db.close()


def test_pm_total_subtracts_expense_payments():
    db = SessionLocal()
    try:
        # create a PM and related income and an expense payment
        pm = PropertyManager(first_name='Test', last_name='PM', percent=10.0)
        db.add(pm); db.commit(); db.refresh(pm)
        inc = Income(apartment_id=None, platform_id=None, date='2025-03-01', gross_amount=100.0,
                     vat_percent=0.0, net_amount=100.0, pm_percent=10.0, pm_amount=10.0, net_after_pm=90.0,
                     associated_pm_id=pm.id)
        exp = Expense(apartment_id=None, date='2025-03-05', gross_amount=20.0,
                      vat_percent=0.0, net_amount=20.0, associated_pm_id=pm.id)
        db.add_all([inc, exp]); db.commit()
        # call pm edit view which reports pm_total
        resp = client.get(f'/anagrafiche/property-manager/{pm.id}/edit?year=2025')
        assert resp.status_code == 200
        text = resp.text
        # pm_total should equal 10 (from income) minus 20 (expense) = -10
        assert '€-10.00' in text
        # if we also check stats endpoint, the pm_totals entry should reflect same
        resp2 = client.get('/stats?year=2025')
        assert resp2.status_code == 200
        assert f'€-10.00' in resp2.text
    finally:
        db.query(Income).filter(Income.id == inc.id).delete()
        db.query(Expense).filter(Expense.id == exp.id).delete()
        db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
        db.commit()
        db.close()
