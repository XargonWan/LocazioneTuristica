from fastapi import APIRouter, Request, Depends, Form
from starlette.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.db import SessionLocal
from app.models import Settings
from app.auth_utils import admin_required
from app.auth_utils import get_current_user
from fastapi.responses import JSONResponse
from app.models import Income, Expense
from datetime import datetime
from starlette.status import HTTP_303_SEE_OTHER

router = APIRouter()
from app.main import templates


@router.get('/stats')
async def stats_view(request: Request, year: int = None):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        # Build per-anagrafica totals for the stats page
        from app.models import PropertyManager, Company, Platform, Income, Expense
        from datetime import datetime
        # determine which years have data
        years_with_data = set()
        for inc in db.query(Income.date).all():
            try:
                y = int(inc[0][:4])
                years_with_data.add(y)
            except Exception:
                pass
        for exp in db.query(Expense.date).all():
            try:
                y = int(exp[0][:4])
                years_with_data.add(y)
            except Exception:
                pass
        available_years = sorted(years_with_data)
        now = datetime.now().year
        if year is None or year not in available_years:
            # prefer current year if it has data, otherwise fall back to latest available year
            if now in available_years:
                year = now
            elif available_years:
                year = available_years[-1]
            else:
                year = now
        pms = db.query(PropertyManager).all()
        companies = db.query(Company).all()
        platforms = db.query(Platform).all()
        # pm totals similar to anagrafiche logic
        pm_totals = {}
        incomes = db.query(Income).all()
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year != year:
                continue
            pm_id = inc.associated_pm_id or (inc.apartment.property_manager_id if inc.apartment else None)
            if not pm_id:
                continue
            pm_amount = float(inc.pm_amount or 0.0)
            if pm_amount == 0.0:
                pm = next((p for p in pms if p.id == pm_id), None)
                if pm:
                    pm_amount = float(inc.gross_amount or 0.0) * (float(pm.percent or 0.0) / 100.0)
            pm_totals[pm_id] = pm_totals.get(pm_id, 0.0) + pm_amount
        # subtract any expense payments made to PMs
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year != year:
                continue
            if exp.associated_pm_id:
                pm_totals[exp.associated_pm_id] = pm_totals.get(exp.associated_pm_id, 0.0) - float(exp.gross_amount or 0.0)
        # company totals (expenses)
        company_totals = {}
        expenses = db.query(Expense).all()
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year != year:
                continue
            if exp.associated_company_id:
                company_totals[exp.associated_company_id] = company_totals.get(exp.associated_company_id, 0.0) + float(exp.gross_amount or 0.0)
        # platform totals (incomes)
        platform_totals = {}
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year != year:
                continue
            if inc.platform_id:
                platform_totals[inc.platform_id] = platform_totals.get(inc.platform_id, 0.0) + float(inc.gross_amount or 0.0)
        return templates.TemplateResponse(request, 'stats.html', {"pms": pms, "companies": companies, "platforms": platforms, "pm_totals": pm_totals, "company_totals": company_totals, "platform_totals": platform_totals, "year": year, "now": now, "available_years": available_years})
    finally:
        db.close()

@router.get('/api/stats/monthly')
async def api_stats_monthly(year: int = None, request: Request = None, pm_id: int = None, company_id: int = None, platform_id: int = None):
    if request and not get_current_user(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    db = SessionLocal()
    try:
        if not year:
            year = datetime.now().year
        incomes = db.query(Income).all()
        expenses = db.query(Expense).all()
        # note: income values are now reported after VAT and PM deductions
        months = {m: {'income': 0.0, 'expense': 0.0} for m in range(1, 13)}
        total_income = 0.0
        total_pm_paid = 0.0
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                # Apply optional filters
                if pm_id:
                    inc_pm_id = inc.associated_pm_id or (inc.apartment.property_manager_id if inc.apartment else None)
                    if inc_pm_id != pm_id:
                        continue
                if platform_id and inc.platform_id != platform_id:
                    continue
                # Use net_after_pm when available, otherwise compute from net - pm
                amt = float(getattr(inc, 'net_after_pm', None) or 0.0)
                if not amt:
                    amt = float(getattr(inc, 'net_amount', 0.0) or 0.0) - float(getattr(inc, 'pm_amount', 0.0) or 0.0)
                months[d.month]['income'] += amt
                total_income += amt
                total_pm_paid += float(getattr(inc, 'pm_amount', 0.0) or 0.0)
        total_expense = 0.0
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                if company_id and exp.associated_company_id != company_id:
                    continue
                amt = float(exp.gross_amount)
                months[d.month]['expense'] += amt
                total_expense += amt
        month_names = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
        result = [{'month': m, 'month_name': month_names[m], 'income': months[m]['income'], 'expense': months[m]['expense']} for m in sorted(months.keys())]
        totals = {'income': total_income, 'expense': total_expense, 'pm_paid': total_pm_paid}
        if total_income:
            totals['pm_percent'] = round((total_pm_paid / total_income) * 100, 2)
        else:
            totals['pm_percent'] = 0.0
        return JSONResponse(content={'year': year, 'data': result, 'totals': totals})
    finally:
        db.close()


@router.get('/settings')
async def settings_view(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        settings = db.query(Settings).all()
    except Exception:
        settings = []
    return templates.TemplateResponse(request, 'settings.html', {"settings": settings})


@router.post('/settings/update')
async def settings_update(request: Request, key: str = Form(...), value: str = Form(...), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        s = db.query(Settings).filter(Settings.key == key).first()
        if not s:
            s = Settings(key=key, value=value)
            db.add(s)
        else:
            s.value = value
            db.add(s)
        db.commit()
        return RedirectResponse(url='/settings', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()

