from fastapi import APIRouter, Request, Form, Depends
from typing import List
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from fastapi.templating import Jinja2Templates
from app.db import SessionLocal
from app.auth_utils import get_current_user
from app.auth_utils import admin_required, auth_required
from app.models import PropertyManager, Apartment, Company, Platform
from app.models import CleaningService
from app.models import Income, Expense
from datetime import datetime
from app.debug import log_request_form
from app.utils import expand_open_recurrences_to_current_year

router = APIRouter(prefix="/anagrafiche")
from app.main import templates


def _request_path_with_query(request: Request):
    query = request.url.query
    return f"{request.url.path}?{query}" if query else request.url.path


def _anagrafiche_default_next(request: Request):
    year = request.query_params.get('year')
    if year:
        return f"/anagrafiche?year={year}"
    return "/anagrafiche"


def _redirect_to_next(next_url, fallback):
    return RedirectResponse(url=(next_url or fallback), status_code=HTTP_303_SEE_OTHER)


@router.get("")
async def index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        pms = db.query(PropertyManager).all()
        apts = db.query(Apartment).all()
        companies = db.query(Company).all()
        platforms = db.query(Platform).all()
        # Compute PM totals for current year (sum of pm_amount or computed from gross_amount if pm_amount missing)
        pm_totals = {}
        # Build PM mapping
        pm_map = {pm.id: pm for pm in pms}
        incomes = db.query(Income).all()
        year = int(request.query_params.get('year') or datetime.now().year)
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year != year:
                continue
            # Determine which PM this income applies to
            pm_id = inc.associated_pm_id
            if not pm_id and inc.apartment:
                pm_id = getattr(inc.apartment, 'property_manager_id', None)
            if not pm_id:
                continue
            # pm_amount may be stored; if not, compute via PM percent
            pm_amount = float(inc.pm_amount or 0.0)
            if pm_amount == 0.0:
                pm = pm_map.get(pm_id)
                if pm:
                    pm_pct = float(pm.percent or 0.0)
                    pm_amount = float(inc.gross_amount or 0.0) * (pm_pct / 100.0)
            pm_totals[pm_id] = pm_totals.get(pm_id, 0.0) + pm_amount
        next_url = _request_path_with_query(request)
        return templates.TemplateResponse(request, "anagrafiche_index.html", {"pms": pms, "apts": apts, "companies": companies, "platforms": platforms, "pm_totals": pm_totals, "next": next_url})
    finally:
        db.close()


@router.post("/property-manager/add")
async def add_pm(request: Request, first_name: str = Form(...), last_name: str = Form(...), percent: float = Form(0.0), next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    await log_request_form(request)
    try:
        pm = PropertyManager(first_name=first_name, last_name=last_name, percent=percent)
        db.add(pm)
        db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.post("/apartment/add")
async def add_apartment(request: Request, name: str = Form(...), property_manager_id: int = Form(None), next: str = Form(None), user=Depends(auth_required)):
    db = SessionLocal()
    await log_request_form(request)
    try:
        apt = Apartment(name=name, property_manager_id=property_manager_id)
        db.add(apt)
        db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.post('/company/add')
async def add_company(request: Request, company_name: str = Form(...), is_cleaning_company: str = Form('0'), next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    await log_request_form(request)
    try:
        c = Company(company_name=company_name, is_cleaning_company=(is_cleaning_company == '1'))
        db.add(c)
        db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.post('/platform/add')
async def add_platform(request: Request, name: str = Form(...), link: str = Form(''), next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    await log_request_form(request)
    try:
        p = Platform(name=name, link=link)
        db.add(p)
        db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.get('/property-manager/{pm_id}/edit')
async def edit_pm_get(request: Request, pm_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        pm = db.query(PropertyManager).filter(PropertyManager.id == pm_id).first()
        if not pm:
            return RedirectResponse(url=_anagrafiche_default_next(request))
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        next_url = request.query_params.get('next') or _anagrafiche_default_next(request)
        # compute PM totals for the year
        pm_total = 0.0
        from app.models import Income
        from datetime import datetime
        incomes = db.query(Income).all()
        year = int(request.query_params.get('year') or datetime.now().year)
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year != year:
                continue
            pm_id_for_income = inc.associated_pm_id or (inc.apartment.property_manager_id if inc.apartment else None)
            if pm_id_for_income == pm.id:
                pm_amount = float(inc.pm_amount or 0.0)
                if pm_amount == 0.0:
                    pm_amount = float(inc.gross_amount or 0.0) * (float(pm.percent or 0.0) / 100.0)
                pm_total += pm_amount
        # subtract any explicit expense payments to this PM
        from app.models import Expense
        expenses = db.query(Expense).all()
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year != year:
                continue
            if exp.associated_pm_id == pm.id:
                pm_total -= float(exp.gross_amount or 0.0)
        return templates.TemplateResponse(request, 'pm_edit.html', {"pm": pm, "apartments": apartments, "pms": pms, "pm_total": pm_total, "next": next_url})
    finally:
        db.close()


@router.api_route('/property-manager/{pm_id}/edit', methods=["POST", "PUT", "PATCH"])
async def edit_pm_post(request: Request, pm_id: int, first_name: str = Form(...), last_name: str = Form(...), apartment_ids: List[int] = Form(None), percent: float = Form(0.0), user=Depends(admin_required)):
    await log_request_form(request)
    # debug log to help find Method Not Allowed issues
    try:
        print('DEBUG: edit_pm_post called with method', request.method, 'pm_id', pm_id)
    except Exception:
        pass

    # read raw form data to access hidden fields like old_percent and confirm_update
    form = await request.form()
    old_percent = float(form.get('old_percent') or 0.0)
    confirm_update = form.get('confirm_update') == '1'
    next_url = form.get('next') or '/anagrafiche'

    db = SessionLocal()
    try:
        pm = db.query(PropertyManager).filter(PropertyManager.id == pm_id).first()
        if not pm:
            return _redirect_to_next(next_url, '/anagrafiche')

        # if percent changed and we haven't yet confirmed, look for affected entries
        if percent != old_percent and not confirm_update:
            # count incomes/expenses where the PM is associated and the stored percent matches the old value
            incs = db.query(Income).filter(Income.associated_pm_id == pm.id, Income.pm_percent == old_percent).all()
            exps = db.query(Expense).filter(Expense.associated_pm_id == pm.id, Expense.pm_percent == old_percent).all()
            if incs or exps:
                # render confirmation screen
                return templates.TemplateResponse(request, 'pm_update_confirm.html', {
                    'pm': pm,
                    'old_percent': old_percent,
                    'new_percent': percent,
                    'inc_count': len(incs),
                    'exp_count': len(exps),
                    'apartment_ids': apartment_ids or [],
                    'next': next_url
                })

        # apply changes to PM
        pm.first_name = first_name
        pm.last_name = last_name
        pm.percent = percent

        # if confirmation was requested and there are affected entries, update them now
        if percent != old_percent and confirm_update:
            # update incomes
            for inc in db.query(Income).filter(Income.associated_pm_id == pm.id, Income.pm_percent == old_percent).all():
                inc.pm_percent = percent
                try:
                    gross = float(inc.gross_amount or 0.0)
                    inc.pm_amount = round(gross * (percent / 100.0), 2)
                    inc.net_after_pm = round(float(inc.net_amount or 0.0) - inc.pm_amount, 2)
                except Exception:
                    pass
                db.add(inc)
            # clear any pm-related fields on existing expense records for this PM
            for exp in db.query(Expense).filter(Expense.associated_pm_id == pm.id).all():
                exp.pm_percent = 0.0
                exp.pm_amount = 0.0
                exp.net_after_pm = float(exp.net_amount or 0.0)
                db.add(exp)

        # Update apartment associations: set property_manager_id for selected apartments
        if apartment_ids is not None:
            # Clear apartments currently assigned to this PM not selected now
            current = db.query(Apartment).filter(Apartment.property_manager_id == pm.id).all()
            current_ids = [a.id for a in current]
            for a in current:
                if a.id not in apartment_ids:
                    a.property_manager_id = None
                    db.add(a)
            # Assign selected apartments to this PM
            for aid in apartment_ids:
                ap = db.query(Apartment).filter(Apartment.id == aid).first()
                if ap:
                    ap.property_manager_id = pm.id
                    db.add(ap)
            db.commit()

        db.add(pm)
        db.commit()
        return _redirect_to_next(next_url, '/anagrafiche')
    finally:
        db.close()


@router.post('/property-manager/{pm_id}/delete')
async def delete_pm(request: Request, pm_id: int, next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        pm = db.query(PropertyManager).filter(PropertyManager.id == pm_id).first()
        if pm:
            db.delete(pm)
            db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.get('/apartment/{apt_id}/edit')
async def edit_apartment_get(request: Request, apt_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        apt = db.query(Apartment).filter(Apartment.id == apt_id).first()
        if not apt:
            return RedirectResponse(url=_anagrafiche_default_next(request))
        pms = db.query(PropertyManager).all()
        cleaning_companies = db.query(Company).filter(Company.is_cleaning_company == True).all()
        next_url = request.query_params.get('next') or _anagrafiche_default_next(request)
        return templates.TemplateResponse(request, 'apartment_edit.html', {"apt": apt, "pms": pms, "cleaning_companies": cleaning_companies, "next": next_url})
    finally:
        db.close()


@router.api_route('/apartment/{apt_id}/edit', methods=["POST", "PUT", "PATCH"])
async def edit_apartment_post(request: Request, apt_id: int, name: str = Form(...), property_manager_id: int = Form(None), default_cleaning_company_id: int = Form(None), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    try:
        print('DEBUG: edit_apartment_post called with method', request.method, 'apt_id', apt_id)
    except Exception:
        pass
    db = SessionLocal()
    try:
        apt = db.query(Apartment).filter(Apartment.id == apt_id).first()
        if not apt:
            return _redirect_to_next(next, '/anagrafiche')
        apt.name = name
        apt.property_manager_id = property_manager_id
        apt.default_cleaning_company_id = default_cleaning_company_id
        db.add(apt)
        db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.post('/apartment/{apt_id}/delete')
async def delete_apartment(request: Request, apt_id: int, next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        apt = db.query(Apartment).filter(Apartment.id == apt_id).first()
        if apt:
            db.delete(apt)
            db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.post('/apartment/assign')
async def assign_apartment(request: Request, apartment_id: int = Form(...), property_manager_id: int = Form(None), next: str = Form(None), user=Depends(auth_required)):
    """Assign or unassign an existing apartment to a property manager.
    If property_manager_id is empty or None, the apartment will be unassigned (property_manager_id set to NULL).
    """
    db = SessionLocal()
    try:
        apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
        if not apt:
            return _redirect_to_next(next, '/anagrafiche')
        # allow unassign
        if not property_manager_id or str(property_manager_id).strip() == '':
            apt.property_manager_id = None
        else:
            apt.property_manager_id = int(property_manager_id)
        db.add(apt)
        db.commit()
        if next:
            return _redirect_to_next(next, '/anagrafiche')
        # Redirect back to the PM edit page if we assigned to a PM, else to anagrafiche
        if apt.property_manager_id:
            return RedirectResponse(url=f"/anagrafiche/property-manager/{apt.property_manager_id}/edit", status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url='/anagrafiche', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get('/company/{company_id}/edit')
async def edit_company_get(request: Request, company_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        c = db.query(Company).filter(Company.id == company_id).first()
        if not c:
            return RedirectResponse(url=_anagrafiche_default_next(request))
        next_url = request.query_params.get('next') or _anagrafiche_default_next(request)
        return templates.TemplateResponse(request, 'company_edit.html', {"company": c, "next": next_url})
    finally:
        db.close()


@router.api_route('/company/{company_id}/edit', methods=["POST", "PUT", "PATCH"])
async def edit_company_post(request: Request, company_id: int, company_name: str = Form(...), is_cleaning_company: str = Form('0'), default_gross_amount: float = Form(None), default_net_amount: float = Form(None), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    try:
        print('DEBUG: edit_company_post called with method', request.method, 'company_id', company_id)
    except Exception:
        pass
    db = SessionLocal()
    try:
        c = db.query(Company).filter(Company.id == company_id).first()
        if not c:
            return _redirect_to_next(next, '/anagrafiche')
        c.company_name = company_name
        c.is_cleaning_company = (is_cleaning_company == '1')
        c.default_gross_amount = default_gross_amount
        c.default_net_amount = default_net_amount
        db.add(c)
        db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.post('/company/{company_id}/delete')
async def delete_company(request: Request, company_id: int, next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        c = db.query(Company).filter(Company.id == company_id).first()
        if c:
            db.delete(c)
            db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.get('/platform/{platform_id}/edit')
async def edit_platform_get(request: Request, platform_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        p = db.query(Platform).filter(Platform.id == platform_id).first()
        if not p:
            return RedirectResponse(url=_anagrafiche_default_next(request))
        next_url = request.query_params.get('next') or _anagrafiche_default_next(request)
        return templates.TemplateResponse(request, 'platform_edit.html', {"platform": p, "next": next_url})
    finally:
        db.close()


@router.api_route('/platform/{platform_id}/edit', methods=["POST", "PUT", "PATCH"])
async def edit_platform_post(request: Request, platform_id: int, name: str = Form(...), link: str = Form(''), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    try:
        print('DEBUG: edit_platform_post called with method', request.method, 'platform_id', platform_id)
    except Exception:
        pass
    db = SessionLocal()
    try:
        p = db.query(Platform).filter(Platform.id == platform_id).first()
        if not p:
            return _redirect_to_next(next, '/anagrafiche')
        p.name = name
        p.link = link
        db.add(p)
        db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()


@router.post('/platform/{platform_id}/delete')
async def delete_platform(request: Request, platform_id: int, next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        p = db.query(Platform).filter(Platform.id == platform_id).first()
        if p:
            db.delete(p)
            db.commit()
        return _redirect_to_next(next, '/anagrafiche')
    finally:
        db.close()
