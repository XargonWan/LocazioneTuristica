from fastapi import APIRouter, Request, Form, Depends
from typing import List
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from app.db import SessionLocal
from app.models import Cleaning, CleaningService, Company, Apartment, Expense, Income
from app.auth_utils import admin_required, get_current_user
from app.debug import log_request_form

router = APIRouter(prefix="/cleaning")
from app.main import templates


def _redirect_to_next(next_url, fallback):
    return RedirectResponse(url=(next_url or fallback), status_code=HTTP_303_SEE_OTHER)


@router.get("")
async def cleanings_index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        default_income_id = request.query_params.get('income_id')
        default_apartment_id = request.query_params.get('apartment_id')
        default_date = request.query_params.get('date') or ''
        next_url = request.query_params.get('next') or '/cleaning'
        linked_income = None
        if default_income_id:
            try:
                linked_income = db.query(Income).filter(Income.id == int(default_income_id)).first()
            except Exception:
                linked_income = None
        if linked_income:
            if not default_apartment_id and linked_income.apartment_id:
                default_apartment_id = linked_income.apartment_id
            if not default_date and linked_income.date:
                default_date = linked_income.date
        cleanings = db.query(Cleaning).order_by(Cleaning.date.desc()).limit(50).all()
        apartments = db.query(Apartment).all()
        companies = db.query(Company).filter(Company.is_cleaning_company == True).all()
        services = db.query(CleaningService).all()
        return templates.TemplateResponse(request, "cleanings_index.html", {"cleanings": cleanings, "apartments": apartments, "companies": companies, "services": services, "default_income_id": default_income_id, "default_apartment_id": default_apartment_id, "default_date": default_date, "next": next_url, "linked_income": linked_income})
    finally:
        db.close()


@router.post("/add")
async def add_cleaning(request: Request,
                       date: str = Form(...),
                       apartment_id: int = Form(...),
                       income_id: int = Form(None),
                       company_id: int = Form(...),
                       service_id: int = Form(None),
                       gross_amount: float = Form(None),
                       net_amount: float = Form(None),
                       vat_percent: float = Form(22.0),
                       is_net: str = Form('0'),
                       notes: str = Form(''),
                       next: str = Form(None),
                       user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        if income_id:
            linked_income = db.query(Income).filter(Income.id == income_id).first()
            if linked_income and linked_income.apartment_id:
                apartment_id = linked_income.apartment_id
        # ensure vat_percent is float for arithmetic
        vat_percent = float(vat_percent or 0.0)
        # determine amounts based on provided info
        use_net = is_net == '1'
        # if service specified, use defaults unless overrides provided
        if service_id:
            svc = db.query(CleaningService).filter(CleaningService.id == service_id).first()
            if svc:
                vat_percent = float(svc.vat_percent or vat_percent)
                if gross_amount is None and net_amount is None:
                    base = float(svc.default_amount or 0.0)
                    use_net = svc.is_net
                    if use_net:
                        net_amount = base
                    else:
                        gross_amount = base
        # compute missing amount
        if use_net:
            net_amount = float(net_amount or 0.0)
            gross_amount = round(net_amount * (1 + vat_percent / 100.0), 2)
        else:
            gross_amount = float(gross_amount or 0.0)
            net_amount = round(gross_amount * (1 - vat_percent / 100.0), 2)
        # create cleaning record
        c = Cleaning(date=date, apartment_id=apartment_id, income_id=income_id, company_id=company_id,
                     service_id=service_id, gross_amount=gross_amount,
                     net_amount=net_amount, vat_percent=vat_percent,
                     is_net=use_net, notes=notes)
        db.add(c)
        db.commit()
        # Cleaning expenses never generate PM shares.
        e = Expense(apartment_id=apartment_id, date=date, gross_amount=gross_amount,
                    vat_percent=vat_percent, net_amount=net_amount,
                    associated_company_id=company_id, is_cleaning=True, notes=notes)
        db.add(e)
        db.commit()
        c.expense_id = e.id
        db.add(c)
        db.commit()
        if next:
            return RedirectResponse(url=next, status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/cleaning", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get("/{cleaning_id}/edit")
async def edit_cleaning_get(request: Request, cleaning_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        c = db.query(Cleaning).filter(Cleaning.id == cleaning_id).first()
        next_url = request.query_params.get('next') or '/cleaning'
        if not c:
            return RedirectResponse(url=next_url)
        apartments = db.query(Apartment).all()
        companies = db.query(Company).filter(Company.is_cleaning_company == True).all()
        services = db.query(CleaningService).filter(CleaningService.company_id == c.company_id).all()
        linked_income = db.query(Income).filter(Income.id == c.income_id).first() if c.income_id else None
        return templates.TemplateResponse(request, 'cleaning_edit.html', {"cleaning": c, "apartments": apartments, "companies": companies, "services": services, "linked_income": linked_income, "next": next_url})
    finally:
        db.close()


@router.api_route("/{cleaning_id}/edit", methods=["POST", "PUT", "PATCH"])
async def edit_cleaning_post(request: Request,
                             cleaning_id: int,
                             date: str = Form(...),
                             apartment_id: int = Form(...),
                             income_id: int = Form(None),
                             company_id: int = Form(...),
                             service_id: int = Form(None),
                             gross_amount: float = Form(None),
                             net_amount: float = Form(None),
                             vat_percent: float = Form(22.0),
                             is_net: str = Form('0'),
                             notes: str = Form(''),
                             next: str = Form(None),
                             user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        c = db.query(Cleaning).filter(Cleaning.id == cleaning_id).first()
        if not c:
            return RedirectResponse(url='/cleaning')
        if income_id:
            linked_income = db.query(Income).filter(Income.id == income_id).first()
            if linked_income and linked_income.apartment_id:
                apartment_id = linked_income.apartment_id
        use_net = is_net == '1'
        vat_percent = float(vat_percent or 0.0)
        # service defaults if chosen
        if service_id:
            svc = db.query(CleaningService).filter(CleaningService.id == service_id).first()
            if svc:
                vat_percent = float(svc.vat_percent or vat_percent)
                if gross_amount is None and net_amount is None:
                    base = float(svc.default_amount or 0.0)
                    use_net = svc.is_net
                    if use_net:
                        net_amount = base
                    else:
                        gross_amount = base
        if use_net:
            net_amount = float(net_amount or c.net_amount or 0.0)
            gross_amount = round(net_amount * (1 + vat_percent / 100.0), 2)
        else:
            gross_amount = float(gross_amount or c.gross_amount or 0.0)
            net_amount = round(gross_amount * (1 - vat_percent / 100.0), 2)
        # update cleaning
        c.date = date
        c.apartment_id = apartment_id
        c.income_id = income_id
        c.company_id = company_id
        c.service_id = service_id
        c.gross_amount = gross_amount
        c.net_amount = net_amount
        c.vat_percent = vat_percent
        c.is_net = use_net
        c.notes = notes
        db.add(c)
        db.commit()
        # update linked expense if exists
        if c.expense_id:
            e = db.query(Expense).filter(Expense.id == c.expense_id).first()
            if e:
                e.date = date
                e.apartment_id = apartment_id
                e.gross_amount = gross_amount
                e.vat_percent = vat_percent
                e.net_amount = net_amount
                e.associated_company_id = company_id
                e.notes = notes
                db.add(e)
                db.commit()
        if next:
            return RedirectResponse(url=next, status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/cleaning", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/{cleaning_id}/delete")
async def delete_cleaning(request: Request, cleaning_id: int, next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        c = db.query(Cleaning).filter(Cleaning.id == cleaning_id).first()
        if c:
            # delete associated expense if present
            if c.expense_id:
                e = db.query(Expense).filter(Expense.id == c.expense_id).first()
                if e:
                    db.delete(e)
            db.delete(c)
            db.commit()
        return _redirect_to_next(next, '/cleaning')
    finally:
        db.close()

# service management

@router.get("/service")
async def services_index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        services = db.query(CleaningService).all()
        companies = db.query(Company).filter(Company.is_cleaning_company == True).all()
        next_url = request.query_params.get('next') or '/cleaning/service'
        return templates.TemplateResponse(request, "cleaning_services.html", {"services": services, "companies": companies, "next": next_url})
    finally:
        db.close()


@router.post("/service/add")
async def add_service(request: Request,
                      company_id: int = Form(...),
                      name: str = Form(...),
                      default_amount: float = Form(0.0),
                      is_net: str = Form('0'),
                      vat_percent: float = Form(22.0),
                      next: str = Form(None),
                      user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        svc = CleaningService(company_id=company_id, name=name,
                              default_amount=default_amount,
                              is_net=(is_net == '1'),
                              vat_percent=vat_percent)
        db.add(svc)
        db.commit()
        return _redirect_to_next(next, '/cleaning/service')
    finally:
        db.close()


@router.get("/service/{service_id}/edit")
async def edit_service_get(request: Request, service_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        svc = db.query(CleaningService).filter(CleaningService.id == service_id).first()
        next_url = request.query_params.get('next') or '/cleaning/service'
        if not svc:
            return RedirectResponse(url=next_url)
        companies = db.query(Company).filter(Company.is_cleaning_company == True).all()
        return templates.TemplateResponse(request, 'cleaning_service_edit.html', {"service": svc, "companies": companies, "next": next_url})
    finally:
        db.close()


@router.api_route("/service/{service_id}/edit", methods=["POST", "PUT", "PATCH"])
async def edit_service_post(request: Request,
                            service_id: int,
                            company_id: int = Form(...),
                            name: str = Form(...),
                            default_amount: float = Form(0.0),
                            is_net: str = Form('0'),
                            vat_percent: float = Form(22.0),
                            next: str = Form(None),
                            user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        svc = db.query(CleaningService).filter(CleaningService.id == service_id).first()
        if svc:
            svc.company_id = company_id
            svc.name = name
            svc.default_amount = default_amount
            svc.is_net = (is_net == '1')
            svc.vat_percent = vat_percent
            db.add(svc)
            db.commit()
        return _redirect_to_next(next, '/cleaning/service')
    finally:
        db.close()


@router.post("/service/{service_id}/delete")
async def delete_service(request: Request, service_id: int, next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        svc = db.query(CleaningService).filter(CleaningService.id == service_id).first()
        if svc:
            db.delete(svc)
            db.commit()
        return _redirect_to_next(next, '/cleaning/service')
    finally:
        db.close()
