#!/usr/bin/env python3
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import PropertyManager, Apartment, Expense

init_db()
print('Starting expense assignment check...')
db = SessionLocal()
try:
    pm = PropertyManager(first_name='Test', last_name='PM2', percent=15.0)
    db.add(pm); db.commit()
    apt = Apartment(name='Test Apt 2', property_manager_id=pm.id)
    db.add(apt); db.commit()
    # Create expense without associated_pm_id and without pm_percent
    e = Expense(apartment_id=apt.id, date='2025-12-15', gross_amount=30.0, vat_percent=22.0, net_amount=round(30.0*(1-0.22), 2), notes='test expense')
    db.add(e); db.commit()
    print('Expense created id', e.id, 'assoc pm', e.associated_pm_id)
    # Emulate add_expense behavior default
    # The router should set associated_pm_id if not provided. In this test, it won't since we directly created the Expense. Let's mimic the router behavior:
    if not e.associated_pm_id and e.apartment and e.apartment.property_manager_id:
        e.associated_pm_id = e.apartment.property_manager_id
        # emulate pm_percent default
        pm_obj = db.query(PropertyManager).filter(PropertyManager.id == e.associated_pm_id).first()
        if pm_obj:
            e.pm_percent = float(pm_obj.percent or 0.0)
            e.pm_amount = round(float(e.gross_amount or 0.0) * (e.pm_percent / 100.0), 2)
            e.net_after_pm = round(float(e.net_amount or 0.0) - float(e.pm_amount or 0.0), 2)
        db.add(e); db.commit()
    e2 = db.query(Expense).filter(Expense.id==e.id).first()
    print('Post-default assoc pm', e2.associated_pm_id, 'pm_percent', float(e2.pm_percent), 'pm_amount', float(e2.pm_amount), 'net_after_pm', float(e2.net_after_pm))
finally:
    db.close()
    print('Done.')
