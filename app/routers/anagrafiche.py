from fastapi import APIRouter, Request, Form, Depends
from typing import List
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from fastapi.templating import Jinja2Templates
from app.db import SessionLocal
from app.auth_utils import get_current_user
from app.auth_utils import admin_required
from app.models import PropertyManager, Apartment, Company, Platform

router = APIRouter(prefix="/anagrafiche")
templates = Jinja2Templates(directory="app/templates")


@router.get("")
async def index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        pms = db.query(PropertyManager).all()
        apts = db.query(Apartment).all()
        companies = db.query(Company).all()
        platforms = db.query(Platform).all()
        return templates.TemplateResponse("anagrafiche_index.html", {"request": request, "pms": pms, "apts": apts, "companies": companies, "platforms": platforms})
    finally:
        db.close()


@router.post("/property-manager/add")
async def add_pm(request: Request, first_name: str = Form(...), last_name: str = Form(...), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        pm = PropertyManager(first_name=first_name, last_name=last_name)
        db.add(pm)
        db.commit()
        return RedirectResponse(url="/anagrafiche", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/apartment/add")
async def add_apartment(request: Request, name: str = Form(...), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        apt = Apartment(name=name)
        db.add(apt)
        db.commit()
        return RedirectResponse(url="/anagrafiche", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post('/company/add')
async def add_company(request: Request, company_name: str = Form(...), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        c = Company(company_name=company_name)
        db.add(c)
        db.commit()
        return RedirectResponse(url='/anagrafiche', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post('/platform/add')
async def add_platform(request: Request, name: str = Form(...), link: str = Form(''), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        p = Platform(name=name, link=link)
        db.add(p)
        db.commit()
        return RedirectResponse(url='/anagrafiche', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get('/property-manager/{pm_id}/edit')
async def edit_pm_get(request: Request, pm_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        pm = db.query(PropertyManager).filter(PropertyManager.id == pm_id).first()
        if not pm:
            return RedirectResponse(url='/anagrafiche')
        apartments = db.query(Apartment).all()
        return templates.TemplateResponse('pm_edit.html', {"request": request, "pm": pm, "apartments": apartments})
    finally:
        db.close()


@router.post('/property-manager/{pm_id}/edit')
@router.put('/property-manager/{pm_id}/edit')
@router.patch('/property-manager/{pm_id}/edit')
@router.api_route('/property-manager/{pm_id}/edit', methods=["PATCH"])
async def edit_pm_post(request: Request, pm_id: int, first_name: str = Form(...), last_name: str = Form(...), apartment_ids: List[int] = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        pm = db.query(PropertyManager).filter(PropertyManager.id == pm_id).first()
        if not pm:
            return RedirectResponse(url='/anagrafiche')
        pm.first_name = first_name
        pm.last_name = last_name
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
        return RedirectResponse(url='/anagrafiche')
    finally:
        db.close()


@router.post('/property-manager/{pm_id}/delete')
async def delete_pm(request: Request, pm_id: int, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        pm = db.query(PropertyManager).filter(PropertyManager.id == pm_id).first()
        if pm:
            db.delete(pm)
            db.commit()
        return RedirectResponse(url='/anagrafiche')
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
            return RedirectResponse(url='/anagrafiche')
        return templates.TemplateResponse('apartment_edit.html', {"request": request, "apt": apt})
    finally:
        db.close()


@router.post('/apartment/{apt_id}/edit')
@router.put('/apartment/{apt_id}/edit')
@router.patch('/apartment/{apt_id}/edit')
@router.api_route('/apartment/{apt_id}/edit', methods=["PATCH"])
async def edit_apartment_post(request: Request, apt_id: int, name: str = Form(...), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        apt = db.query(Apartment).filter(Apartment.id == apt_id).first()
        if not apt:
            return RedirectResponse(url='/anagrafiche')
        apt.name = name
        db.add(apt)
        db.commit()
        return RedirectResponse(url='/anagrafiche')
    finally:
        db.close()


@router.post('/apartment/{apt_id}/delete')
async def delete_apartment(request: Request, apt_id: int, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        apt = db.query(Apartment).filter(Apartment.id == apt_id).first()
        if apt:
            db.delete(apt)
            db.commit()
        return RedirectResponse(url='/anagrafiche')
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
            return RedirectResponse(url='/anagrafiche')
        return templates.TemplateResponse('company_edit.html', {"request": request, "company": c})
    finally:
        db.close()


@router.post('/company/{company_id}/edit')
@router.put('/company/{company_id}/edit')
@router.patch('/company/{company_id}/edit')
@router.api_route('/company/{company_id}/edit', methods=["PATCH"])
async def edit_company_post(request: Request, company_id: int, company_name: str = Form(...), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        c = db.query(Company).filter(Company.id == company_id).first()
        if not c:
            return RedirectResponse(url='/anagrafiche')
        c.company_name = company_name
        db.add(c)
        db.commit()
        return RedirectResponse(url='/anagrafiche')
    finally:
        db.close()


@router.post('/company/{company_id}/delete')
async def delete_company(request: Request, company_id: int, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        c = db.query(Company).filter(Company.id == company_id).first()
        if c:
            db.delete(c)
            db.commit()
        return RedirectResponse(url='/anagrafiche')
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
            return RedirectResponse(url='/anagrafiche')
        return templates.TemplateResponse('platform_edit.html', {"request": request, "platform": p})
    finally:
        db.close()


@router.post('/platform/{platform_id}/edit')
@router.put('/platform/{platform_id}/edit')
@router.patch('/platform/{platform_id}/edit')
@router.api_route('/platform/{platform_id}/edit', methods=["PATCH"])
async def edit_platform_post(request: Request, platform_id: int, name: str = Form(...), link: str = Form(''), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        p = db.query(Platform).filter(Platform.id == platform_id).first()
        if not p:
            return RedirectResponse(url='/anagrafiche')
        p.name = name
        p.link = link
        db.add(p)
        db.commit()
        return RedirectResponse(url='/anagrafiche')
    finally:
        db.close()


@router.post('/platform/{platform_id}/delete')
async def delete_platform(request: Request, platform_id: int, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        p = db.query(Platform).filter(Platform.id == platform_id).first()
        if p:
            db.delete(p)
            db.commit()
        return RedirectResponse(url='/anagrafiche')
    finally:
        db.close()
