#!/usr/bin/env python3
"""Quick script to check PM default assignment and pm_amount calculation on new income."""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import PropertyManager, Apartment, Income

init_db()
print('Starting PM assignment check...')
db = SessionLocal()
try:
    # Create PM
    pm = PropertyManager(first_name='Test', last_name='PM', percent=12.5)
    db.add(pm); db.commit()
    pm_id = pm.id
    # Create apartment assigned to PM
    apt = Apartment(name='Test Apt', property_manager_id=pm_id)
    db.add(apt); db.commit()
    apt_id = apt.id
    # Add an income via code mimicking handler (without associated_pm_id or pm_percent)
    gross = 200.0
    vat = 22.0
    net_amount = round(gross * (1 - (vat/100.0)), 2)
    pm_percent = 0.0
    # emulate handler defaulting behavior
    associated_pm_id = None
    if not associated_pm_id and apt_id:
        a = db.query(Apartment).filter(Apartment.id == apt_id).first()
        if a and a.property_manager_id:
            associated_pm_id = a.property_manager_id
            pm_obj = db.query(PropertyManager).filter(PropertyManager.id == associated_pm_id).first()
            if pm_obj and (pm_percent is None or float(pm_percent) == 0.0):
                pm_percent = float(pm_obj.percent or 0.0)
    pm_amount = round(gross * (pm_percent / 100.0), 2)
    net_after_pm = round(net_amount - pm_amount, 2)
    inc = Income(apartment_id=apt_id, date='2025-12-01', gross_amount=gross, vat_percent=vat, net_amount=net_amount, pm_percent=pm_percent, pm_amount=pm_amount, net_after_pm=net_after_pm, associated_pm_id=associated_pm_id, notes='test')
    db.add(inc); db.commit()
    print('Created income id', inc.id, 'pm_amount', float(inc.pm_amount), 'associated_pm_id', inc.associated_pm_id)
    # Calculate pm_total for the PM in 2025 via query, similar to code in router
    from datetime import datetime
    incomes = db.query(Income).all()
    pm_total = 0.0
    for income in incomes:
        try:
            d = datetime.strptime(income.date, '%Y-%m-%d')
        except Exception:
            continue
        if d.year != 2025:
            continue
        pm_id_for_income = income.associated_pm_id or (income.apartment.property_manager_id if income.apartment else None)
        if pm_id_for_income == pm_id:
            pm_amt = float(income.pm_amount or 0.0)
            if pm_amt == 0.0:
                pm_amt = float(income.gross_amount or 0.0) * (float(pm.percent or 0.0) / 100.0)
            pm_total += pm_amt
    print('pm_total computed:', pm_total)
finally:
    db.close()

print('Done.')
