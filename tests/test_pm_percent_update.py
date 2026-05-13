import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models import PropertyManager, Income, Expense

pytestmark = pytest.mark.db_backup

client = TestClient(app)
client.post('/auth/login', data={'username': 'testadmin', 'password': 'secret'})


def test_pm_percent_change_prompts_update_and_applies():
    db = SessionLocal()
    try:
        # create PM and associated entries
        pm = PropertyManager(first_name='Alice', last_name='PM', percent=20.0)
        db.add(pm)
        db.commit()
        # must refresh to get id
        db.refresh(pm)
        inc = Income(
            apartment_id=None,
            platform_id=None,
            date='2025-01-01',
            gross_amount=100.0,
            vat_percent=22.0,
            net_amount=78.0,
            pm_percent=20.0,
            pm_amount=20.0,
            net_after_pm=58.0,
            associated_pm_id=pm.id,
            notes='income with pm'
        )
        exp = Expense(
            apartment_id=None,
            date='2025-01-02',
            gross_amount=50.0,
            vat_percent=22.0,
            net_amount=39.0,
            pm_percent=20.0,
            pm_amount=10.0,
            net_after_pm=29.0,
            associated_pm_id=pm.id,
            notes='expense with pm'
        )
        db.add_all([inc, exp])
        db.commit()
        db.refresh(inc); db.refresh(exp)

        # first POST should render confirmation because entries exist with old percent
        resp = client.post(
            f"/anagrafiche/property-manager/{pm.id}/edit",
            data={
                'first_name': pm.first_name,
                'last_name': pm.last_name,
                'percent': '30.0',
                'old_percent': '20.0'
            }
        )
        assert resp.status_code == 200
        text = resp.text.lower()
        assert 'conferma aggiornamento' in text or 'vuoi aggiornare' in text

        # now confirm and apply change
        resp2 = client.post(
            f"/anagrafiche/property-manager/{pm.id}/edit",
            data={
                'first_name': pm.first_name,
                'last_name': pm.last_name,
                'percent': '30.0',
                'old_percent': '20.0',
                'confirm_update': '1'
            }
        )
        assert resp2.status_code in (200, 303)
        db.refresh(inc); db.refresh(exp)
        assert float(inc.pm_percent) == 30.0
        # expense percent should be cleared when PM percent changes
        assert float(exp.pm_percent) == 0.0
    finally:
        # cleanup
        try:
            db.query(Income).filter(Income.id == inc.id).delete()
        except Exception:
            pass
        try:
            db.query(Expense).filter(Expense.id == exp.id).delete()
        except Exception:
            pass
        try:
            db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
        except Exception:
            pass
        db.commit()
        db.close()


def test_pm_percent_change_without_matching_entries_updates_immediately():
    db = SessionLocal()
    try:
        pm = PropertyManager(first_name='Bob', last_name='PM2', percent=10.0)
        db.add(pm); db.commit(); db.refresh(pm)
        # no incomes/expenses attached
        resp = client.post(
            f"/anagrafiche/property-manager/{pm.id}/edit",
            data={
                'first_name': pm.first_name,
                'last_name': pm.last_name,
                'percent': '15.0',
                'old_percent': '10.0',
                'next': '/overview?year=2025'
            },
            follow_redirects=False
        )
        # should redirect without confirmation and preserve the requested next URL
        assert resp.status_code == 303
        assert resp.headers['location'] == '/overview?year=2025'
        # check pm updated
        db.refresh(pm)
        assert float(pm.percent) == 15.0
    finally:
        db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
        db.commit(); db.close()


def test_add_property_manager_redirects_to_next():
    db = SessionLocal()
    pm = None
    try:
        resp = client.post(
            '/anagrafiche/property-manager/add',
            data={
                'first_name': 'Next',
                'last_name': 'PM',
                'percent': '5.0',
                'next': '/overview?year=2025'
            },
            follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers['location'] == '/overview?year=2025'
        pm = db.query(PropertyManager).filter(PropertyManager.first_name == 'Next', PropertyManager.last_name == 'PM').order_by(PropertyManager.id.desc()).first()
        assert pm is not None
    finally:
        if pm is not None:
            db.query(PropertyManager).filter(PropertyManager.id == pm.id).delete()
            db.commit()
        db.close()
