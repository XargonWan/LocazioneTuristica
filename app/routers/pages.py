from fastapi import APIRouter, Request, Depends, Form
from starlette.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.db import SessionLocal
from app.models import Settings
from app.auth_utils import admin_required
from fastapi import Form
from app.auth_utils import get_current_user
from fastapi.responses import JSONResponse
from app.models import Income, Expense
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get('/stats')
async def stats_view(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        # Placeholder for stats
        return templates.TemplateResponse('stats.html', {"request": request})
    finally:
        db.close()

@router.get('/api/stats/monthly')
async def api_stats_monthly(year: int = None, request: Request = None):
    if request and not get_current_user(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    db = SessionLocal()
    try:
        if not year:
            year = datetime.now().year
        incomes = db.query(Income).all()
        expenses = db.query(Expense).all()
        months = {m: {'income': 0.0, 'expense': 0.0} for m in range(1, 13)}
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                months[d.month]['income'] += float(inc.gross_amount)
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                months[d.month]['expense'] += float(exp.gross_amount)
        month_names = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
        result = [{'month': m, 'month_name': month_names[m], 'income': months[m]['income'], 'expense': months[m]['expense']} for m in sorted(months.keys())]
        return JSONResponse(content={'year': year, 'data': result})
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
    return templates.TemplateResponse('settings.html', {"request": request, "settings": settings})


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

