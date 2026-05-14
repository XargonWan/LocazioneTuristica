import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models import Apartment, Expense, Income, PropertyManager, Recurrence, User
from app.utils import expand_open_recurrences_to_current_year
from app.routers.auth import pwd_context

pytestmark = pytest.mark.db_backup


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
        # edit and set recurrence using defaults (no explicit range) to ensure previous behaviour
        r = client.post(
            f'/money/incomes/{inc.id}/edit',
            data={
                'gross_amount': '100.0',
                'vat_percent': '22.0',
                'pm_percent': '0.0',
                'date': '2025-12-01',
                'recurrence': 'monthly',
                'notes': note,
            }
        )
        assert r.status_code in (200, 303)
        db.refresh(inc)
        assert inc.recurrence_id is not None
        occs = db.query(Income).filter(Income.recurrence_id == inc.recurrence_id).all()
        # should at least have original + 1 more record
        assert len(occs) >= 2
    finally:
        db.close()


def test_income_edit_creates_open_recurrence_until_current_year(monkeypatch):
    db = SessionLocal()
    try:
        create_admin(db)
        inc = Income(apartment_id=None, platform_id=None, date='2025-01-01', gross_amount=100.0, vat_percent=22.0, net_amount=78.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=78.0, notes='open-income')
        db.add(inc)
        db.commit()
        from app.routers import money as money_router

        monkeypatch.setattr(money_router, '_current_year', lambda: 2026)
        client = TestClient(app)
        r = client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        assert r.status_code in (200, 303)
        r = client.post(
            f'/money/incomes/{inc.id}/edit',
            data={
                'gross_amount': '100.0',
                'vat_percent': '22.0',
                'pm_percent': '0.0',
                'date': '2025-01-01',
                'recurrence': 'monthly',
                'notes': 'open-income',
            }
        )
        assert r.status_code in (200, 303)
        db.refresh(inc)
        occs = db.query(Income).filter(Income.recurrence_id == inc.recurrence_id).order_by(Income.date).all()
        assert len(occs) == 24
        assert occs[0].date == '2025-01-01'
        assert occs[-1].date == '2026-12-01'
    finally:
        db.close()


def test_income_edit_creates_recurrence_with_range():
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
        # edit and set recurrence with explicit range
        r = client.post(
            f'/money/incomes/{inc.id}/edit',
            data={
                'gross_amount': '100.0',
                'vat_percent': '22.0',
                'pm_percent': '0.0',
                'date': '2025-12-01',
                'recurrence': 'monthly',
                'recurrence_start': '2025-09',
                'recurrence_end': '2026-02',
                'notes': note,
            }
        )
        assert r.status_code in (200, 303)
        db.refresh(inc)
        assert inc.recurrence_id is not None
        # check metadata on recurrence record
        rec = db.query(Recurrence).filter(Recurrence.id == inc.recurrence_id).first()
        assert rec.start_date.startswith('2025-09')
        assert rec.end_date.startswith('2026-02')
        # occurrences should include months from Sep 2025 through Feb 2026, skipping the original
        occs = db.query(Income).filter(Income.recurrence_id == inc.recurrence_id).all()
        dates = sorted([o.date for o in occs])
        assert '2025-09-01' in dates
        assert '2025-10-01' in dates
        assert '2025-11-01' in dates
        assert ('2025-12-01' not in dates) or len(dates) >= 5  # original is 2025-12-01, may still be the stored record
        assert '2026-01-01' in dates
        assert '2026-02-01' in dates
        assert len(dates) >= 5
    finally:
        db.close()


def test_edit_form_prefills_recurrence():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        # create recurrence metadata and a linked expense
        r = Recurrence(type='monthly', start_date='2025-01-01', end_date='2025-06-01')
        db.add(r)
        db.commit()
        e = Expense(apartment_id=None, date='2025-01-01', gross_amount=20.0, vat_percent=22.0, net_amount=15.6, pm_percent=0.0, pm_amount=0.0, net_after_pm=15.6, recurrence_id=r.id)
        db.add(e)
        db.commit()
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.get(f'/money/expenses/{e.id}/edit')
        assert resp.status_code in (200, 303)
        text = resp.text
        assert '<option value="monthly" selected>' in text
        assert 'name="recurrence_start"' in text and '2025-01' in text
        assert 'name="recurrence_end"' in text and '2025-06' in text
    finally:
        db.close()


def test_edit_form_does_not_infer_recurring_from_date_overlap():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        # create recurrence but expense without linking
        r = Recurrence(type='monthly', start_date='2025-01-01', end_date='2025-06-01')
        db.add(r)
        db.commit()
        e = Expense(apartment_id=None, date='2025-03-01', gross_amount=30.0, vat_percent=22.0, net_amount=23.4, pm_percent=0.0, pm_amount=0.0, net_after_pm=23.4)
        db.add(e)
        db.commit()
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.get(f'/money/expenses/{e.id}/edit')
        assert resp.status_code in (200, 303)
        text = resp.text
        assert 'name="orig_recurrence_id" value=""' in text
        assert '<option value="none" selected>' in text
        assert 'Falla rientrare nella serie' not in text
    finally:
        db.close()


def test_detached_expense_tracks_origin_and_can_rejoin_without_rewriting_series():
    db = SessionLocal()
    try:
        create_admin(db)
        r = Recurrence(type='monthly', start_date='2025-01-01', end_date='2025-03-01')
        db.add(r)
        db.commit()
        jan = Expense(apartment_id=None, date='2025-01-01', gross_amount=40.0, vat_percent=22.0, net_amount=31.2, pm_percent=0.0, pm_amount=0.0, net_after_pm=31.2, recurrence_id=r.id, notes='serie')
        feb = Expense(apartment_id=None, date='2025-02-01', gross_amount=40.0, vat_percent=22.0, net_amount=31.2, pm_percent=0.0, pm_amount=0.0, net_after_pm=31.2, recurrence_id=r.id, notes='serie')
        mar = Expense(apartment_id=None, date='2025-03-01', gross_amount=40.0, vat_percent=22.0, net_amount=31.2, pm_percent=0.0, pm_amount=0.0, net_after_pm=31.2, recurrence_id=r.id, notes='serie')
        db.add_all([jan, feb, mar])
        db.commit()

        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.post(
            f'/money/expenses/{feb.id}/edit',
            data={
                'gross_amount': '99.0',
                'net_amount': '89.1',
                'vat_percent': '10.0',
                'pm_percent': '0.0',
                'date': '2025-02-19',
                'recurrence': 'none',
                'apply_to': 'single',
                'notes': 'fuori-serie',
            },
        )

        assert resp.status_code in (200, 303)
        db.refresh(feb)
        assert feb.recurrence_id is None
        assert feb.orig_recurrence_id == r.id
        assert feb.date == '2025-02-19'
        assert float(feb.gross_amount) == 99.0
        assert float(feb.net_amount) == 89.1
        assert float(feb.vat_percent) == 10.0
        assert feb.notes == 'fuori-serie'

        resp = client.post(
            f'/money/expenses/{feb.id}/edit',
            data={
                'gross_amount': '99.0',
                'net_amount': '89.1',
                'vat_percent': '10.0',
                'pm_percent': '0.0',
                'date': '2025-02-19',
                'recurrence': 'none',
                'apply_to': 'single',
                'orig_recurrence_id': str(r.id),
                'rejoin_recurrence': '1',
                'notes': 'fuori-serie',
            },
        )

        assert resp.status_code in (200, 303)
        db.refresh(feb)
        assert feb.recurrence_id == r.id
        assert feb.orig_recurrence_id is None
        assert feb.date == '2025-02-01'
        assert float(feb.gross_amount) == 40.0
        assert float(feb.net_amount) == 31.2
        assert float(feb.vat_percent) == 22.0
        assert feb.notes == 'serie'

        linked = db.query(Expense).filter(Expense.recurrence_id == r.id).order_by(Expense.date).all()
        assert [entry.date for entry in linked] == ['2025-01-01', '2025-02-01', '2025-03-01']
        assert all(entry.notes == 'serie' for entry in linked)
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
        # convert to monthly recurrence and specify range that starts before the date
        r = client.post(
            f'/money/expenses/{e.id}/edit',
            data={
                'gross_amount': '50.0',
                'vat_percent': '22.0',
                'pm_percent': '0.0',
                'date': '2025-11-05',
                'recurrence': 'monthly',
                'recurrence_start': '2025-09',
                'recurrence_end': '2025-12',
                'notes': note,
            }
        )
        assert r.status_code in (200, 303)
        db.refresh(e)
        assert e.recurrence_id is not None
        rec = db.query(Recurrence).filter(Recurrence.id == e.recurrence_id).first()
        assert rec.start_date.startswith('2025-09')
        assert rec.end_date.startswith('2025-12')
        occs = db.query(Expense).filter(Expense.recurrence_id == e.recurrence_id).all()
        dates = sorted([o.date for o in occs])
        # should create sept/oct/nov/dec entries; the original may be in list or replaced
        assert '2025-09-01' in dates
        assert '2025-10-01' in dates
        assert '2025-11-05' in dates
        assert '2025-12-01' in dates
        assert len(dates) >= 4
    finally:
        db.close()


def test_expense_edit_creates_open_recurrence_until_current_year(monkeypatch):
    db = SessionLocal()
    try:
        create_admin(db)
        exp = Expense(apartment_id=None, date='2025-01-01', gross_amount=80.0, vat_percent=22.0, net_amount=62.4, pm_percent=0.0, pm_amount=0.0, net_after_pm=62.4, notes='open-expense')
        db.add(exp)
        db.commit()
        from app.routers import money as money_router

        monkeypatch.setattr(money_router, '_current_year', lambda: 2026)
        client = TestClient(app)
        r = client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        assert r.status_code in (200, 303)
        r = client.post(
            f'/money/expenses/{exp.id}/edit',
            data={
                'gross_amount': '80.0',
                'net_amount': '62.4',
                'vat_percent': '22.0',
                'pm_percent': '0.0',
                'date': '2025-01-01',
                'recurrence': 'monthly',
                'notes': 'open-expense',
            }
        )
        assert r.status_code in (200, 303)
        db.refresh(exp)
        occs = db.query(Expense).filter(Expense.recurrence_id == exp.recurrence_id).order_by(Expense.date).all()
        assert len(occs) == 24
        assert occs[0].date == '2025-01-01'
        assert occs[-1].date == '2026-12-01'
    finally:
        db.close()


def test_expense_edit_calculates_gross_from_net():
    db = SessionLocal()
    try:
        create_admin(db)
        exp = Expense(apartment_id=None, date='2025-01-01', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=39.0, notes='edit-from-net')
        db.add(exp)
        db.commit()

        client = TestClient(app)
        r = client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        assert r.status_code in (200, 303)
        r = client.post(
            f'/money/expenses/{exp.id}/edit',
            data={
                'net_amount': '78.0',
                'vat_percent': '22.0',
                'date': '2025-01-01',
                'notes': 'edit-from-net',
            },
        )

        assert r.status_code in (200, 303)
        db.refresh(exp)
        assert float(exp.net_amount) == 78.0
        assert float(exp.gross_amount) == 100.0
    finally:
        db.close()

# when editing an existing recurring expense and you shift the series start date backwards,
# previously generated occurrences should be recalculated (old dates removed).
def test_shift_expense_series_backwards():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        # initial recurrence starts May 1 with a few occurrences
        r = Recurrence(type='monthly', start_date='2025-05-01', end_date='2025-08-01')
        db.add(r)
        db.commit()
        # create original occurrences
        e1 = Expense(apartment_id=None, date='2025-05-01', gross_amount=10.0, vat_percent=0.0, net_amount=10.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=10.0, recurrence_id=r.id, notes='shift')
        e2 = Expense(apartment_id=None, date='2025-06-01', gross_amount=10.0, vat_percent=0.0, net_amount=10.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=10.0, recurrence_id=r.id, notes='shift')
        e3 = Expense(apartment_id=None, date='2025-07-01', gross_amount=10.0, vat_percent=0.0, net_amount=10.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=10.0, recurrence_id=r.id, notes='shift')
        db.add_all([e1, e2, e3])
        db.commit()
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.post(
            f'/money/expenses/{e1.id}/edit',
            data={
                'gross_amount': '10.0',
                'vat_percent': '0.0',
                'pm_percent': '0.0',
                'date': '2025-02-01',
                'recurrence': 'monthly',
                'recurrence_start': '2025-02',
                'recurrence_end': '2025-08',
                'apply_to': 'series',
                'notes': 'shift',
            }
        )
        assert resp.status_code in (200, 303)
        db.expire_all()
        occs = db.query(Expense).filter(Expense.recurrence_id == r.id).all()
        dates = sorted([o.date for o in occs])
        assert '2025-02-01' in dates
        assert '2025-03-01' in dates
        assert '2025-04-01' in dates
        assert dates.count('2025-05-01') == 1
    finally:
        db.close()


def test_expand_open_recurrence_is_idempotent():
    db = SessionLocal()
    try:
        r = Recurrence(type='monthly', start_date='2025-01-01')
        db.add(r)
        db.commit()
        for month in range(1, 13):
            exp = Expense(apartment_id=None, date=f'2025-{month:02d}-01', gross_amount=40.0, vat_percent=22.0, net_amount=31.2, pm_percent=0.0, pm_amount=0.0, net_after_pm=31.2, recurrence_id=r.id, notes='legacy-open')
            db.add(exp)
        db.add(Expense(apartment_id=None, date='2025-01-01', gross_amount=40.0, vat_percent=22.0, net_amount=31.2, pm_percent=0.0, pm_amount=0.0, net_after_pm=31.2, recurrence_id=r.id, notes='legacy-open-dup'))
        db.commit()

        inserted_first = expand_open_recurrences_to_current_year(db, current_year=2026)
        inserted_second = expand_open_recurrences_to_current_year(db, current_year=2026)

        occs = db.query(Expense).filter(Expense.recurrence_id == r.id).order_by(Expense.date).all()
        assert inserted_first == 12
        assert inserted_second == 0
        assert len(occs) == 24
        assert [o.date for o in occs].count('2025-01-01') == 1
        assert occs[-1].date == '2026-12-01'
        db.refresh(r)
        assert r.next_date == '2027-01-01'
    finally:
        db.close()


def test_expense_edit_get_backfills_open_series(monkeypatch):
    db = SessionLocal()
    try:
        create_admin(db)
        r = Recurrence(type='monthly', start_date='2025-01-01')
        db.add(r)
        db.commit()
        created = []
        for month in range(1, 13):
            exp = Expense(apartment_id=None, date=f'2025-{month:02d}-01', gross_amount=80.0, vat_percent=22.0, net_amount=62.4, pm_percent=0.0, pm_amount=0.0, net_after_pm=62.4, recurrence_id=r.id, notes='series-edit')
            db.add(exp)
            created.append(exp)
        db.commit()
        from app.routers import money as money_router

        monkeypatch.setattr(
            money_router,
            'expand_open_recurrences_to_current_year',
            lambda db_session: expand_open_recurrences_to_current_year(db_session, current_year=2026),
        )
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.get(f'/money/expenses/{created[0].id}/edit')
        assert resp.status_code in (200, 303)
        assert '2026-12-01' in resp.text
        occs = db.query(Expense).filter(Expense.recurrence_id == r.id).order_by(Expense.date).all()
        assert len(occs) == 24
        assert occs[-1].date == '2026-12-01'
    finally:
        db.close()

def test_edit_income_form_prefills_recurrence():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        r = Recurrence(type='yearly', start_date='2024-01-01', end_date='2026-01-01')
        db.add(r)
        db.commit()
        inc = Income(apartment_id=None, platform_id=None, date='2024-01-01', gross_amount=123.0, vat_percent=22.0, net_amount=95.94, pm_percent=0.0, pm_amount=0.0, net_after_pm=95.94, recurrence_id=r.id)
        db.add(inc)
        db.commit()
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.get(f'/money/incomes/{inc.id}/edit')
        assert resp.status_code in (200, 303)
        txt = resp.text
        assert '<option value="yearly" selected>' in txt
        assert 'name="recurrence_start"' in txt and '2024' in txt
        assert 'name="recurrence_end"' in txt and '2026' in txt
    finally:
        db.close()


def test_edit_income_form_does_not_infer_recurring_from_date_overlap():
    db = SessionLocal()
    try:
        create_admin(db)
        r = Recurrence(type='yearly', start_date='2024-01-01', end_date='2026-01-01')
        db.add(r)
        db.commit()
        inc = Income(apartment_id=None, platform_id=None, date='2025-01-01', gross_amount=123.0, vat_percent=22.0, net_amount=95.94, pm_percent=0.0, pm_amount=0.0, net_after_pm=95.94)
        db.add(inc)
        db.commit()
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.get(f'/money/incomes/{inc.id}/edit')
        assert resp.status_code in (200, 303)
        txt = resp.text
        assert 'name="orig_recurrence_id" value=""' in txt
        assert '<option value="none" selected>' in txt
        assert 'Falla rientrare nella serie' not in txt
    finally:
        db.close()


def test_add_expense_without_pm():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        r = client.post('/money/expenses/add', data={
            'gross_amount': '50.0',
            'vat_percent': '22.0',
            'date': '2025-10-01',
            # do not send associate_pm or uncheck, nothing sent
        })
        assert r.status_code in (200, 303)
        exp = db.query(Expense).order_by(Expense.id.desc()).first()
        assert exp is not None
        assert exp.associated_pm_id is None
    finally:
        db.close()


def test_next_param_propagates_from_overview_to_add():
    # request add page with next set = overview?year=2025
    client = TestClient(app)
    client.post('/auth/login', data={'username':'testadmin','password':'secret'})
    r = client.get('/money/expenses?next=/overview?year=2025')
    assert r.status_code == 200
    assert 'name="next"' in r.text
    assert 'overview?year=2025' in r.text


def test_date_param_prefills_form():
    client = TestClient(app)
    client.post('/auth/login', data={'username':'testadmin','password':'secret'})
    r = client.get('/money/expenses?date=2025-04-01')
    assert r.status_code == 200
    assert 'value="2025-04-01"' in r.text
    r2 = client.get('/money/incomes?date=2025-05-01')
    assert r2.status_code == 200
    assert 'value="2025-05-01"' in r2.text
    # ensure date and next parameters can coexist
    r3 = client.get('/money/expenses?date=2025-02-01&next=/overview?year=2025')
    assert r3.status_code == 200
    assert 'value="2025-02-01"' in r3.text
    assert 'overview?year=2025' in r3.text


def test_incomes_form_includes_apartment_pm_options_and_mapping():
    db = SessionLocal()
    try:
        create_admin(db)
        pm = PropertyManager(first_name='Mario', last_name='Rossi', percent=12.5)
        db.add(pm)
        db.commit()
        apt = Apartment(name='A1', property_manager_id=pm.id)
        db.add(apt)
        db.commit()

        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.get('/money/incomes')

        assert resp.status_code == 200
        assert 'id="associate_pm_checkbox"' in resp.text
        assert f'>{pm.first_name} {pm.last_name}</option>' in resp.text
        assert 'syncPmFromApartment' in resp.text
        assert f'"pm_id": {pm.id}' in resp.text
        assert '"percent": 12.5' in resp.text
    finally:
        db.close()


def test_add_income_with_apartment_pm_defaults_associated_pm_and_percent():
    db = SessionLocal()
    try:
        create_admin(db)
        pm = PropertyManager(first_name='Test', last_name='PM', percent=15.0)
        db.add(pm)
        db.commit()
        apt = Apartment(name='A1', property_manager_id=pm.id)
        db.add(apt)
        db.commit()

        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        resp = client.post('/money/incomes/add', data={
            'gross_amount': '100.0',
            'vat_percent': '22.0',
            'pm_percent': '0.0',
            'date': '2025-10-01',
            'apartment_id': str(apt.id),
            'associate_pm': 'on',
        })

        assert resp.status_code in (200, 303)
        inc = db.query(Income).order_by(Income.id.desc()).first()
        assert inc is not None
        assert inc.associated_pm_id == pm.id
        assert float(inc.pm_percent) == 15.0
        assert float(inc.pm_amount) == 15.0
        assert float(inc.net_after_pm) == 63.0
    finally:
        db.close()


def test_stats_year_dropdown_shows_data_years_only():
    # create a dedicated future expense so the stats year dropdown remains deterministic
    db = SessionLocal()
    try:
        admin = create_admin(db)
        client = TestClient(app)
        client.post('/auth/login', data={'username':'testadmin','password':'secret'})
        stats_year = 2037
        client.post('/money/expenses/add', data={
            'gross_amount': '10.0',
            'vat_percent': '22.0',
            'date': f'{stats_year}-06-15',
        })
        r = client.get('/stats')
        assert r.status_code == 200
        assert '<option value="0" selected>Tutti</option>' in r.text
        assert 'value="2037"' in r.text
        assert 'Tutti' in r.text
    finally:
        db.close()


def test_next_param_redirects_after_post():
    # if the form is submitted with next query param, response should redirect back there
    client = TestClient(app)
    client.post('/auth/login', data={'username':'testadmin','password':'secret'})
    r = client.post('/money/expenses/add?next=/overview?year=2025', data={
        'gross_amount': '70.0',
        'vat_percent': '22.0',
        'date': '2025-08-01',
    })
    assert r.status_code in (200, 303)
    if r.status_code == 303:
        assert r.headers.get('location') == '/overview?year=2025'

    # also check incomes behavior
    r2 = client.post('/money/incomes/add?next=/overview?year=2025', data={
        'gross_amount': '80.0',
        'vat_percent': '22.0',
        'date': '2025-08-01',
    })
    assert r2.status_code in (200, 303)
    if r2.status_code == 303:
        assert r2.headers.get('location') == '/overview?year=2025'


def test_add_expense_with_pm():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        pm = PropertyManager(first_name='Test', last_name='PM')
        db.add(pm); db.commit()
        apt = Apartment(name='A1', property_manager_id=pm.id)
        db.add(apt); db.commit()
        client = TestClient(app)
        client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})
        r = client.post('/money/expenses/add', data={
            'gross_amount': '60.0',
            'vat_percent': '22.0',
            'date': '2025-10-01',
            'apartment_id': str(apt.id),
            'associate_pm': 'on',
        })
        assert r.status_code in (200, 303)
        exp = db.query(Expense).order_by(Expense.id.desc()).first()
        assert exp.associated_pm_id == pm.id
    finally:
        db.close()
