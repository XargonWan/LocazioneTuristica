#!/usr/bin/env python3
"""Create a recurring income via DB-layer simulation and check that occurrences are materialized."""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import Income, Recurrence
from datetime import datetime

init_db()
db = SessionLocal()
try:
    db.query(Income).filter(Income.notes.like('rec_inc_test%')).delete()
    db.query(Recurrence).filter(Recurrence.notes.like('rec_inc_test%')).delete()
    db.commit()
    r = Recurrence(type='monthly', start_date='2025-06-05', notes='rec_inc_test')
    db.add(r); db.commit()
    inc = Income(apartment_id=None, platform_id=None, date='2025-06-05', gross_amount=200.0, vat_percent=22.0, net_amount=200.0*(1-0.22), pm_percent=0.0, pm_amount=0.0, net_after_pm=200.0*(1-0.22), recurrence_id=r.id, notes='rec_inc_test')
    db.add(inc); db.commit()
    start = datetime.strptime(r.start_date, '%Y-%m-%d')
    def add_months(dt, months):
        y = dt.year + (dt.month - 1 + months) // 12
        m = (dt.month - 1 + months) % 12 + 1
        d = min(dt.day, 28)
        return datetime(y, m, d)
    for i in range(1, 12):
        nd = add_months(start, i).strftime('%Y-%m-%d')
        new_i = Income(apartment_id=None, platform_id=None, date=nd, gross_amount=200.0, vat_percent=22.0, net_amount=200.0*(1-0.22), pm_percent=0.0, pm_amount=0.0, net_after_pm=200.0*(1-0.22), recurrence_id=r.id, notes='rec_inc_test')
        db.add(new_i)
    db.commit()
    items = db.query(Income).filter(Income.recurrence_id==r.id).order_by(Income.date).all()
    print('Recurrence created with id', r.id, 'total occurrences:', len(items))
    for it in items:
        print(' -', it.date)
finally:
    db.close()
