#!/usr/bin/env python3
"""
Compute totals used by /api/stats/monthly endpoint for a given year.
"""
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import Income, Expense
from datetime import datetime

init_db()
db = SessionLocal()
try:
    year = 2025
    incomes = db.query(Income).all()
    expenses = db.query(Expense).all()
    total_income = 0.0
    total_expense = 0.0
    total_pm_paid = 0.0
    for inc in incomes:
        try:
            d = datetime.strptime(inc.date, '%Y-%m-%d')
        except Exception:
            continue
        if d.year == year:
            total_income += float(inc.gross_amount or 0)
            total_pm_paid += float(getattr(inc, 'pm_amount', 0.0) or 0.0)
    for exp in expenses:
        try:
            d = datetime.strptime(exp.date, '%Y-%m-%d')
        except Exception:
            continue
        if d.year == year:
            total_expense += float(exp.gross_amount or 0)
    print('Totals for', year)
    print('Total incomes:', total_income)
    print('Total expenses:', total_expense)
    print('PM paid total:', total_pm_paid)
finally:
    db.close()
