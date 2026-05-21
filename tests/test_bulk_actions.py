import pytest
import asyncio
from fastapi.testclient import TestClient
import subprocess, os

# ensure cleaning columns/tables exist before tests run
subprocess.call([os.getenv('PYTHON', 'python'), 'scripts/migrate_add_cleaning.py'])
# dispose engine so any cached sqlite connection is closed and will see new schema
from app.db import engine
engine.dispose()
from app.main import app
from app.db import SessionLocal
from app.models import Income, Expense, Recurrence, Company, Cleaning, CleaningService, Apartment, PropertyManager, User

pytestmark = pytest.mark.db_backup

client = TestClient(app)
client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'}, follow_redirects=False)


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


def test_bulk_edit_incomes_calculates_gross_from_imponibile():
    db = SessionLocal()
    try:
        i1 = Income(apartment_id=None, platform_id=None, date='2025-01-01', gross_amount=100.0, vat_percent=22.0, net_amount=81.97, pm_percent=0.0, pm_amount=0.0, net_after_pm=81.97, notes='bulk-inc-1')
        i2 = Income(apartment_id=None, platform_id=None, date='2025-02-01', gross_amount=30.0, vat_percent=10.0, net_amount=27.27, pm_percent=0.0, pm_amount=0.0, net_after_pm=27.27, notes='bulk-inc-2')
        db.add_all([i1, i2])
        db.commit()
        ids = f"{i1.id},{i2.id}"
        resp = client.post(
            '/money/incomes/bulk_edit',
            data={'ids': ids, 'net_amount': '78.0', 'vat_percent': '22.0'},
            follow_redirects=False,
        )
        assert resp.status_code in (200, 303)
        db.refresh(i1)
        db.refresh(i2)
        assert float(i1.net_amount) == 78.0 and float(i2.net_amount) == 78.0
        assert float(i1.gross_amount) == 95.16 and float(i2.gross_amount) == 95.16
        assert float(i1.vat_percent) == 22.0 and float(i2.vat_percent) == 22.0
    finally:
        db.query(Income).filter(Income.id.in_([i1.id, i2.id])).delete()
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


def test_incomes_focus_income_shows_linked_entry_outside_recent_limit(monkeypatch):
    from datetime import date, timedelta
    from app.routers import money as money_router

    db = SessionLocal()
    focus_income = None
    created_ids = []
    user = None
    try:
        user = db.query(User).filter(User.username == 'testadmin').first()
        assert user is not None
        focus_income = Income(apartment_id=None, platform_id=None, date='2024-01-01', gross_amount=111.0, vat_percent=22.0, net_amount=86.58, pm_percent=0.0, pm_amount=0.0, net_after_pm=86.58, notes='focus-income')
        db.add(focus_income)
        db.commit()
        db.refresh(focus_income)
        created_ids.append(focus_income.id)

        start_date = date(2025, 1, 1)
        for offset in range(50):
            curr_date = start_date + timedelta(days=offset)
            inc = Income(apartment_id=None, platform_id=None, date=curr_date.isoformat(), gross_amount=100.0 + offset, vat_percent=22.0, net_amount=78.0 + offset, pm_percent=0.0, pm_amount=0.0, net_after_pm=78.0 + offset, notes=f'recent-{offset}')
            db.add(inc)
            db.flush()
            created_ids.append(inc.id)
        db.commit()

        captured = {}

        def fake_template_response(request, name, context):
            captured['name'] = name
            captured['context'] = context
            return context

        monkeypatch.setattr(money_router.templates, 'TemplateResponse', fake_template_response)

        request = type('Req', (), {'session': {'user_id': user.id, 'role': 'admin'}, 'query_params': {'focus_income_id': str(focus_income.id)}})()
        asyncio.run(money_router.incomes_index(request))

        assert captured['name'] == 'incomes_index.html'
        assert captured['context']['focus_income_id'] == focus_income.id
        assert captured['context']['incomes'][0].id == focus_income.id
        assert any(inc.id == focus_income.id for inc in captured['context']['incomes'])
    finally:
        if created_ids:
            db.query(Income).filter(Income.id.in_(created_ids)).delete(synchronize_session=False)
            db.commit()
        db.close()


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
        assert float(e1.pm_percent) == 0.0 and float(e1.pm_amount) == 0.0
    finally:
        db.query(Expense).filter(Expense.id.in_([e1.id, e2.id])).delete()
        db.commit(); db.close()


def test_bulk_edit_expenses_calculates_gross_from_net():
    db = SessionLocal()
    try:
        e1 = Expense(apartment_id=None, date='2025-01-01', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=39.0, notes='bulk-net-1')
        e2 = Expense(apartment_id=None, date='2025-02-01', gross_amount=30.0, vat_percent=10.0, net_amount=27.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=27.0, notes='bulk-net-2')
        db.add_all([e1, e2]); db.commit()
        ids = f"{e1.id},{e2.id}"
        resp = client.post(
            '/money/expenses/bulk_edit',
            data={'ids': ids, 'net_amount': '78.0', 'vat_percent': '22.0'},
            follow_redirects=False,
        )
        assert resp.status_code in (200, 303)
        db.refresh(e1); db.refresh(e2)
        assert float(e1.net_amount) == 78.0 and float(e2.net_amount) == 78.0
        assert float(e1.gross_amount) == 95.16 and float(e2.gross_amount) == 95.16
        assert float(e1.vat_percent) == 22.0 and float(e2.vat_percent) == 22.0
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
        assert resp.headers['location'] == '/overview'
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
        inc = Income(apartment_id=None, platform_id=None, date='2031-01-01', gross_amount=100.0,
                     vat_percent=22.0, net_amount=81.97, pm_percent=10.0, pm_amount=10.0, net_after_pm=71.97,
                     notes='rent')
        # expense small enough to keep pm_due positive when subtracted
        exp = Expense(apartment_id=None, date='2031-01-01', gross_amount=5.0,
                      vat_percent=22.0, net_amount=4.10, pm_percent=0.0, pm_amount=0.0, net_after_pm=4.10,
                      associated_pm_id=pm.id,
                      notes='payment')
        db.add_all([inc, exp]); db.commit()
        resp = client.get('/overview?year=2031')
        assert resp.status_code == 200
        text = resp.text
        assert 'Gennaio - Risultato del mese: <span class="net-total net-positive">€68.77' in text
        assert 'PM ancora da versare: €4.10' in text
    finally:
        # cleanup inserted data
        db.query(Income).filter(Income.id == inc.id).delete()
        db.query(Expense).filter(Expense.id == exp.id).delete()
        db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
        db.commit()
        db.close()


def test_overview_page_annual_totals_split_real_and_virtual():
    db = SessionLocal()
    try:
        pm = PropertyManager(first_name='Annual', last_name='PM', percent=0.0)
        db.add(pm)
        db.commit()

        income = Income(
            apartment_id=None,
            platform_id=None,
            date='2032-01-10',
            gross_amount=100.0,
            vat_percent=22.0,
            net_amount=81.97,
            pm_percent=10.0,
            pm_amount=10.0,
            net_after_pm=71.97,
            notes='annual-rent',
        )
        regular_expense = Expense(
            apartment_id=None,
            date='2032-01-12',
            gross_amount=15.0,
            vat_percent=22.0,
            net_amount=12.30,
            pm_percent=0.0,
            pm_amount=0.0,
            net_after_pm=12.30,
            notes='maintenance',
        )
        pm_payment = Expense(
            apartment_id=None,
            date='2032-01-20',
            gross_amount=6.0,
            vat_percent=22.0,
            net_amount=4.92,
            pm_percent=0.0,
            pm_amount=0.0,
            net_after_pm=4.92,
            associated_pm_id=pm.id,
            notes='pm-payment',
        )
        db.add_all([income, regular_expense, pm_payment])
        db.commit()

        resp = client.get('/overview?year=2032')
        assert resp.status_code == 200
        text = resp.text
        assert 'Entrate dopo IVA e bollo:</strong> <span>€81.97</span>' in text
        assert 'Spese:</strong> <span>€15.00</span>' in text
        assert 'PM gia versato:</strong> <span>€4.92</span>' in text
        assert 'PM ancora da versare:</strong> <span>€3.28</span>' in text
        assert 'Gran totale reale:</strong> <span class="net-total net-positive">€62.05</span>' in text
        assert 'Gran totale virtuale:</strong> <span class="net-total net-positive">€58.77</span>' in text
        assert 'id="overview-table"' not in text
        assert '<th>Mese</th>' not in text
    finally:
        db.close()



def test_create_cleaning_creates_flagged_expense():
    db = SessionLocal()
    cl = None
    exp = None
    inc = None
    svc = None
    comp = None
    apt = None
    try:
        # create a cleaning company and service
        comp = Company(company_name='CleanCo', is_cleaning_company=True)
        db.add(comp); db.commit(); db.refresh(comp)
        svc = CleaningService(company_id=comp.id, name='Standard', default_amount=30.0, is_net=False, vat_percent=22.0)
        db.add(svc); db.commit(); db.refresh(svc)
        # create an apartment to reference
        apt = Apartment(name='A1')
        db.add(apt); db.commit(); db.refresh(apt)
        inc = Income(apartment_id=apt.id, platform_id=None, date='2025-06-01', gross_amount=100.0, vat_percent=22.0, net_amount=78.0, pm_percent=10.0, pm_amount=10.0, net_after_pm=68.0, notes='prenotazione')
        db.add(inc); db.commit(); db.refresh(inc)
        # call cleaning add endpoint
        resp = client.post('/cleaning/add', data={'date':'2025-06-01','apartment_id':apt.id,'income_id':inc.id,'company_id':comp.id,'service_id':svc.id})
        assert resp.status_code in (200, 303)
        # verify cleaning record created
        cl = db.query(Cleaning).filter(Cleaning.apartment_id == apt.id, Cleaning.company_id == comp.id).first()
        assert cl is not None
        assert cl.income_id == inc.id
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
        if inc:
            db.delete(inc)
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
        stats_year = 2037
        inc = Income(apartment_id=None, platform_id=None, date=f'{stats_year}-02-01', gross_amount=200.0,
                     vat_percent=22.0, net_amount=160.0, pm_percent=5.0, pm_amount=8.0, net_after_pm=152.0,
                     notes='room')
        exp = Expense(apartment_id=None, date=f'{stats_year}-02-15', gross_amount=50.0,
                      vat_percent=22.0, net_amount=41.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=41.0,
                      notes='clean', is_cleaning=True)
        db.add_all([inc, exp]); db.commit()
        resp = client.get(f'/api/stats/monthly?year={stats_year}')
        assert resp.status_code == 200
        payload = resp.json()
        data = payload['data']
        feb = next((m for m in data if m['month'] == 2), None)
        assert feb is not None
        # should equal net_after_pm income (152) and gross expense (50)
        assert feb['income'] == 152.0
        assert feb['expense'] == 50.0
        assert payload['totals']['pm_paid'] == 0.0
        assert payload['totals']['pm_due'] == 8.0
        assert payload['totals']['grand_total_real'] == 110.0
        assert payload['totals']['grand_total_virtual'] == 102.0
    finally:
        db.query(Income).filter(Income.id == inc.id).delete()
        db.query(Expense).filter(Expense.id == exp.id).delete()
        db.commit()
        db.close()


def test_api_stats_monthly_all_years_aggregates_same_month_and_pm_due():
    db = SessionLocal()
    try:
        pm = PropertyManager(first_name='All', last_name='Years', percent=10.0)
        db.add(pm)
        db.commit()
        db.refresh(pm)
        inc1 = Income(apartment_id=None, platform_id=None, date='2037-02-01', gross_amount=100.0,
                      vat_percent=0.0, net_amount=100.0, pm_percent=10.0, pm_amount=10.0, net_after_pm=90.0,
                      associated_pm_id=pm.id, notes='year-one')
        inc2 = Income(apartment_id=None, platform_id=None, date='2038-02-01', gross_amount=200.0,
                      vat_percent=0.0, net_amount=200.0, pm_percent=10.0, pm_amount=20.0, net_after_pm=180.0,
                      associated_pm_id=pm.id, notes='year-two')
        exp = Expense(apartment_id=None, date='2037-02-15', gross_amount=30.0,
                      vat_percent=0.0, net_amount=30.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=30.0,
                      notes='ops')
        pm_payment = Expense(apartment_id=None, date='2038-02-20', gross_amount=15.0,
                             vat_percent=22.0, net_amount=12.3, associated_pm_id=pm.id,
                             notes='pm payment')
        db.add_all([inc1, inc2, exp, pm_payment])
        db.commit()

        resp = client.get(f'/api/stats/monthly?year=0&pm_id={pm.id}')
        assert resp.status_code == 200
        payload = resp.json()
        feb = next((month for month in payload['data'] if month['month'] == 2), None)
        assert feb is not None
        assert feb['income'] == 270.0
        assert feb['expense'] == 15.0
        assert payload['totals']['pm_paid'] == 12.3
        assert payload['totals']['pm_due'] == 17.7
        assert payload['totals']['grand_total_real'] == 285.0
        assert payload['totals']['grand_total_virtual'] == 267.3
    finally:
        db.query(Income).filter(Income.id.in_([inc1.id, inc2.id])).delete()
        db.query(Expense).filter(Expense.id.in_([exp.id, pm_payment.id])).delete()
        db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
        db.commit()
        db.close()


def test_overview_and_stats_keep_negative_pm_due_as_credit():
    db = SessionLocal()
    try:
        stats_year = 2039
        pm = PropertyManager(first_name='Credit', last_name='PM', percent=10.0)
        db.add(pm)
        db.commit()
        db.refresh(pm)

        inc = Income(apartment_id=None, platform_id=None, date=f'{stats_year}-03-01', gross_amount=100.0,
                     vat_percent=0.0, net_amount=100.0, pm_percent=10.0, pm_amount=10.0, net_after_pm=90.0,
                     associated_pm_id=pm.id, notes='credit-income')
        pm_payment = Expense(apartment_id=None, date=f'{stats_year}-03-02', gross_amount=20.0,
                             vat_percent=0.0, net_amount=20.0, associated_pm_id=pm.id, notes='credit-payment')
        db.add_all([inc, pm_payment])
        db.commit()

        overview_resp = client.get(f'/overview?year={stats_year}')
        assert overview_resp.status_code == 200
        assert '€-10.00' in overview_resp.text
        assert '(in credito)' in overview_resp.text

        stats_resp = client.get(f'/api/stats/monthly?year={stats_year}&pm_id={pm.id}')
        assert stats_resp.status_code == 200
        payload = stats_resp.json()
        assert payload['totals']['pm_due'] == -10.0
        assert payload['totals']['grand_total_virtual'] == 90.0
    finally:
        db.query(Income).filter(Income.id == inc.id).delete()
        db.query(Expense).filter(Expense.id == pm_payment.id).delete()
        db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
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
                      vat_percent=22.0, net_amount=16.39, associated_pm_id=pm.id)
        db.add_all([inc, exp]); db.commit()
        # call pm edit view which reports pm_total
        resp = client.get(f'/anagrafiche/property-manager/{pm.id}/edit?year=2025')
        assert resp.status_code == 200
        text = resp.text
        assert '€-6.39' in text
        # if we also check stats endpoint, the pm_totals entry should reflect same
        resp2 = client.get('/stats?year=2025')
        assert resp2.status_code == 200
        assert '€-6.39' in resp2.text
        resp3 = client.get('/anagrafiche?year=2025')
        assert resp3.status_code == 200
        assert 'Residuo PM da pagare (anno):' in resp3.text
        assert '€-6.39' in resp3.text
    finally:
        db.query(Income).filter(Income.id == inc.id).delete()
        db.query(Expense).filter(Expense.id == exp.id).delete()
        db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
        db.commit()
        db.close()
