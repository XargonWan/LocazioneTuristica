from fastapi import APIRouter, UploadFile, File, Form, Request, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from fastapi.templating import Jinja2Templates
import os
from app.db import SessionLocal
from app.auth_utils import admin_required, get_current_user
from app.models import Attachment, Expense, Income, Apartment, PropertyManager
from app.utils import get_setting

router = APIRouter(prefix="/attachments")
from app.main import templates

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/attachments")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

PREVIEW_IMAGE_TYPES = {"image/jpeg", "image/png"}
ALLOWED_ATTACHMENT_TYPES = [
    'application/pdf',
    'image/jpeg',
    'image/png',
    'application/vnd.oasis.opendocument.text',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
]

DOCUMENT_TYPES = [
    ("", "Nessuno"),
    ("fattura", "Fattura"),
    ("saldo", "Saldo"),
    ("acconto", "Acconto"),
    ("contratto", "Contratto"),
    ("ricevuta", "Ricevuta"),
    ("tassa", "Tassa"),
    ("altro", "Altro"),
]


class AttachmentUploadValidationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def attachment_upload_max_size() -> int:
    return int(get_setting('max_upload_size', str(10 * 1024 * 1024)))


async def collect_uploaded_attachment_payloads(files: list[UploadFile] | None):
    uploaded_files = []
    for uploaded_file in files or []:
        if not uploaded_file.filename:
            continue
        content = await uploaded_file.read()
        if uploaded_file.content_type not in ALLOWED_ATTACHMENT_TYPES:
            raise AttachmentUploadValidationError(
                'invalid_type',
                f"Tipo file non valido per {uploaded_file.filename}",
            )
        if len(content) > attachment_upload_max_size():
            raise AttachmentUploadValidationError(
                'oversize',
                f"File troppo grande: {uploaded_file.filename}",
            )
        uploaded_files.append((uploaded_file.filename, content, uploaded_file.content_type))
    return uploaded_files


def persist_uploaded_attachments(db, uploaded_files, expense_id: int = None, income_id: int = None):
    created = []
    for filename, content, content_type in uploaded_files:
        path = os.path.join(UPLOAD_DIR, filename)
        with open(path, "wb") as file_handle:
            file_handle.write(content)
        attachment = Attachment(
            filename=filename,
            disk_path=path,
            mimetype=content_type,
            size=len(content),
            expense_id=expense_id,
            income_id=income_id,
        )
        db.add(attachment)
        created.append(attachment)
    return created


def _get_attachment(db, attachment_id: int):
    return db.query(Attachment).filter(Attachment.id == attachment_id).first()


def _attachment_default_next(attachment: Attachment) -> str:
    if attachment.expense_id:
        return f"/money/expenses/{attachment.expense_id}/edit"
    if attachment.income_id:
        return f"/money/incomes/{attachment.income_id}/edit"
    return "/attachments"


def _attachment_request_next(request: Request, attachment: Attachment) -> str:
    return request.query_params.get('next') or _attachment_default_next(attachment)


def _attachment_form_next(form, attachment: Attachment) -> str:
    return (form.get('next') if form else None) or _attachment_default_next(attachment)


def _attachment_preview_kind(attachment: Attachment):
    if attachment.mimetype == "application/pdf":
        return "pdf"
    if attachment.mimetype in PREVIEW_IMAGE_TYPES:
        return "image"
    return None


def _get_attachment_siblings(db, attachment: Attachment):
    """Return ordered list of sibling attachments (same expense or income group) for prev/next navigation."""
    siblings = []
    if attachment.expense_id:
        siblings = db.query(Attachment).filter(Attachment.expense_id == attachment.expense_id).order_by(Attachment.id).all()
    elif attachment.income_id:
        siblings = db.query(Attachment).filter(Attachment.income_id == attachment.income_id).order_by(Attachment.id).all()
    return siblings


def _get_attachment_nav_info(db, attachment: Attachment):
    siblings = _get_attachment_siblings(db, attachment)
    current_index = None
    for i, sib in enumerate(siblings):
        if sib.id == attachment.id:
            current_index = i
            break
    return {
        "siblings": siblings,
        "current_index": current_index,
        "total": len(siblings),
    }


@router.get("")
async def index(
    request: Request,
    year: str = None,
    type_filter: str = None,
    doc_type: str = None,
):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        from sqlalchemy.orm import joinedload
        from datetime import datetime

        q = request.query_params.get('q', '').strip()
        year_str = year or request.query_params.get('year', '').strip()
        type_filter = type_filter or request.query_params.get('type', '').strip()
        doc_type = doc_type or request.query_params.get('doc_type', '').strip()

        # Build base query
        query = db.query(Attachment).options(
            joinedload(Attachment.expense).joinedload(Expense.apartment),
            joinedload(Attachment.income).joinedload(Income.apartment),
            joinedload(Attachment.apartment),
            joinedload(Attachment.property_manager),
        )

        # Apply filters
        if type_filter == 'expense':
            query = query.filter(Attachment.expense_id != None, Attachment.income_id == None)
        elif type_filter == 'income':
            query = query.filter(Attachment.income_id != None, Attachment.expense_id == None)
        elif type_filter == 'none':
            query = query.filter(Attachment.expense_id == None, Attachment.income_id == None)

        if doc_type:
            query = query.filter(Attachment.document_type == doc_type)

        if q:
            query = query.filter(Attachment.filename.ilike(f'%{q}%'))

        # Get all attachments matching filters
        attachments = query.order_by(Attachment.created_at.desc()).all()

        # Filter by year
        if year_str and year_str != 'all':
            try:
                year_int = int(year_str)
            except (TypeError, ValueError):
                year_int = None
            if year_int is not None:
                filtered = []
                for a in attachments:
                    entry = a.expense or a.income
                    if entry and entry.date:
                        try:
                            d = datetime.strptime(entry.date, '%Y-%m-%d')
                            if d.year == year_int:
                                filtered.append(a)
                                continue
                        except Exception:
                            pass
                    if a.created_at and a.created_at.year == year_int:
                        filtered.append(a)
                attachments = filtered
        elif year_str == 'all':
            pass  # show all

        filter_all_years = year_str == 'all'

        # Compute stats
        total_count = len(attachments)
        years_count = {}
        for a in attachments:
            entry = a.expense or a.income
            yr = None
            if entry and entry.date:
                try:
                    yr = datetime.strptime(entry.date, '%Y-%m-%d').year
                except Exception:
                    pass
            if yr is None and a.created_at:
                yr = a.created_at.year
            if yr:
                years_count[yr] = years_count.get(yr, 0) + 1

        # Available years for filter
        all_attachments = db.query(Attachment).all()
        available_years = set()
        for a in all_attachments:
            entry = a.expense or a.income
            if entry and entry.date:
                try:
                    available_years.add(datetime.strptime(entry.date, '%Y-%m-%d').year)
                except Exception:
                    pass
            if a.created_at:
                available_years.add(a.created_at.year)
        available_years = sorted(available_years)

        current_year = datetime.now().year

        next_url = request.query_params.get('next') or '/attachments'
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        return templates.TemplateResponse(request, "attachments_index.html", {
            "attachments": attachments,
            "next": next_url,
            "available_years": available_years,
            "current_year": current_year,
            "total_count": total_count,
            "years_count": years_count,
            "filter_all_years": filter_all_years,
            "year": year_str or '',
            "filter_q": q,
            "type_filter": type_filter,
            "doc_type": doc_type,
            "document_types": DOCUMENT_TYPES,
            "apartments": apartments,
            "pms": pms,
        })
    finally:
        db.close()


def _parse_document_date(value):
    if not value:
        return None
    from datetime import datetime
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except Exception:
        return None


@router.post("/upload")
async def upload(
    request: Request,
    files: list[UploadFile] = File(..., alias="file"),
    next: str = Form(None),
    expense_id: int = Form(None),
    income_id: int = Form(None),
    apartment_id: int = Form(None),
    property_manager_id: int = Form(None),
    document_type: str = Form(None),
    notes: str = Form(''),
    document_date: str = Form(None),
    deduction_year: int = Form(None),
    user=Depends(admin_required),
):
    try:
        uploaded_files = await collect_uploaded_attachment_payloads(files)
    except AttachmentUploadValidationError as exc:
        return RedirectResponse(url=f"/attachments?error={exc.code}", status_code=HTTP_303_SEE_OTHER)
    if not uploaded_files:
        return RedirectResponse(url=(next or "/attachments"), status_code=HTTP_303_SEE_OTHER)
    db = SessionLocal()
    try:
        target_expense_id = None
        target_income_id = None
        if expense_id and db.query(Expense.id).filter(Expense.id == expense_id).first():
            target_expense_id = expense_id
        if income_id and db.query(Income.id).filter(Income.id == income_id).first():
            target_income_id = income_id
        target_apartment_id = apartment_id
        target_pm_id = property_manager_id
        parsed_doc_date = _parse_document_date(document_date)

        for filename, content, content_type in uploaded_files:
            path = os.path.join(UPLOAD_DIR, filename)
            with open(path, "wb") as file_handle:
                file_handle.write(content)
            attachment = Attachment(
                filename=filename,
                disk_path=path,
                mimetype=content_type,
                size=len(content),
                expense_id=target_expense_id,
                income_id=target_income_id,
                apartment_id=target_apartment_id,
                property_manager_id=target_pm_id,
                document_type=document_type or None,
                notes=notes or None,
                document_date=parsed_doc_date,
                deduction_year=deduction_year,
            )
            db.add(attachment)
        db.commit()
        return RedirectResponse(url=(next or "/attachments"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/upload_simple")
async def upload_simple(
    request: Request,
    files: list[UploadFile] = File(..., alias="file"),
    next: str = Form(None),
    document_date: str = Form(None),
    deduction_year: int = Form(None),
    user=Depends(admin_required),
):
    """Simplified upload for use from other pages (expense/income edit)."""
    try:
        uploaded_files = await collect_uploaded_attachment_payloads(files)
    except AttachmentUploadValidationError as exc:
        return RedirectResponse(url=f"/attachments?error={exc.code}", status_code=HTTP_303_SEE_OTHER)
    if not uploaded_files:
        return RedirectResponse(url=(next or "/attachments"), status_code=HTTP_303_SEE_OTHER)
    db = SessionLocal()
    try:
        parsed_doc_date = _parse_document_date(document_date)
        for filename, content, content_type in uploaded_files:
            path = os.path.join(UPLOAD_DIR, filename)
            with open(path, "wb") as file_handle:
                file_handle.write(content)
            attachment = Attachment(
                filename=filename,
                disk_path=path,
                mimetype=content_type,
                size=len(content),
                document_date=parsed_doc_date,
                deduction_year=deduction_year,
            )
            db.add(attachment)
        db.commit()
        return RedirectResponse(url=(next or "/attachments"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get('/{attachment_id}/view')
async def view_attachment(request: Request, attachment_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        from sqlalchemy.orm import joinedload
        attachment = db.query(Attachment).options(
            joinedload(Attachment.expense),
            joinedload(Attachment.income),
            joinedload(Attachment.apartment),
            joinedload(Attachment.property_manager),
        ).filter(Attachment.id == attachment_id).first()
        if not attachment:
            return RedirectResponse(url='/attachments', status_code=HTTP_303_SEE_OTHER)
        file_exists = bool(attachment.disk_path and os.path.exists(attachment.disk_path))
        preview_kind = _attachment_preview_kind(attachment)
        preview_available = bool(file_exists and preview_kind is not None)
        next_url = _attachment_request_next(request, attachment)
        nav_info = _get_attachment_nav_info(db, attachment)
        template_name = 'attachment_view_fragment.html' if request.query_params.get('fragment') == '1' else 'attachment_view.html'
        return templates.TemplateResponse(
            request,
            template_name,
            {
                "attachment": attachment,
                "next_url": next_url,
                "file_exists": file_exists,
                "preview_kind": preview_kind,
                "preview_available": preview_available,
                "nav_info": nav_info,
                "siblings": nav_info["siblings"],
                "current_index": nav_info["current_index"],
                "total_siblings": nav_info["total"],
            },
        )
    finally:
        db.close()


@router.get('/{attachment_id}/inline')
async def inline_attachment(request: Request, attachment_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        attachment = _get_attachment(db, attachment_id)
        if not attachment:
            return RedirectResponse(url='/attachments', status_code=HTTP_303_SEE_OTHER)
        preview_kind = _attachment_preview_kind(attachment)
        if preview_kind not in ("image", "pdf") or not attachment.disk_path or not os.path.exists(attachment.disk_path):
            return RedirectResponse(url=_attachment_request_next(request, attachment), status_code=HTTP_303_SEE_OTHER)
        return FileResponse(
            path=attachment.disk_path,
            media_type=attachment.mimetype,
            headers={"Content-Disposition": "inline"},
        )
    finally:
        db.close()


@router.get('/download/{attachment_id}')
async def download_attachment(request: Request, attachment_id: int):
    db = SessionLocal()
    try:
        a = _get_attachment(db, attachment_id)
        if not a:
            return RedirectResponse(url='/attachments')
        return FileResponse(path=a.disk_path, filename=a.filename, media_type=a.mimetype)
    finally:
        db.close()


@router.post('/{attachment_id}/delete')
async def delete_attachment(request: Request, attachment_id: int, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        form = await request.form()
        attachment = _get_attachment(db, attachment_id)
        if not attachment:
            return RedirectResponse(url=(form.get('next') if form else '/attachments') or '/attachments', status_code=HTTP_303_SEE_OTHER)
        next_url = _attachment_form_next(form, attachment)
        disk_path = attachment.disk_path
        db.delete(attachment)
        db.commit()
        if disk_path:
            try:
                os.remove(disk_path)
            except FileNotFoundError:
                pass
        return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post('/{attachment_id}/rename')
async def rename_attachment(
    request: Request,
    attachment_id: int,
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        form = await request.form()
        attachment = _get_attachment(db, attachment_id)
        if not attachment:
            return RedirectResponse(url='/attachments', status_code=HTTP_303_SEE_OTHER)
        new_filename = form.get('filename', '').strip()
        if new_filename:
            old_path = attachment.disk_path
            if old_path and os.path.exists(old_path):
                dir_name = os.path.dirname(old_path)
                new_path = os.path.join(dir_name, new_filename)
                try:
                    os.rename(old_path, new_path)
                    attachment.disk_path = new_path
                except OSError:
                    pass
            attachment.filename = new_filename
        attachment.notes = form.get('notes', '') or None
        attachment.document_type = form.get('document_type', '') or None
        attachment.is_deduction = form.get('is_deduction') == '1'
        doc_date = _parse_document_date(form.get('document_date'))
        if doc_date:
            attachment.document_date = doc_date
        ded_year = form.get('deduction_year')
        if ded_year is not None and ded_year != '':
            attachment.deduction_year = int(ded_year)
        else:
            attachment.deduction_year = None
        apartment_id = form.get('apartment_id')
        pm_id = form.get('property_manager_id')
        if apartment_id is not None and apartment_id != '':
            attachment.apartment_id = int(apartment_id)
        else:
            attachment.apartment_id = None
        if pm_id is not None and pm_id != '':
            attachment.property_manager_id = int(pm_id)
        else:
            attachment.property_manager_id = None
        db.add(attachment)
        db.commit()
        next_url = form.get('next') or _attachment_default_next(attachment)
        return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post('/{attachment_id}/update_fields')
async def update_attachment_fields(
    request: Request,
    attachment_id: int,
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        form = await request.form()
        attachment = _get_attachment(db, attachment_id)
        if not attachment:
            return RedirectResponse(url='/attachments', status_code=HTTP_303_SEE_OTHER)
        apartment_id = form.get('apartment_id')
        pm_id = form.get('property_manager_id')
        doc_type = form.get('document_type', '') or None
        notes = form.get('notes', '') or None
        if apartment_id:
            attachment.apartment_id = int(apartment_id) if apartment_id else None
        if pm_id is not None:
            attachment.property_manager_id = int(pm_id) if pm_id else None
        attachment.document_type = doc_type
        attachment.notes = notes
        db.add(attachment)
        db.commit()
        next_url = form.get('next') or '/attachments'
        return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


def _get_attachments_for_entity(db, entity_type: str, entity_id: int):
    """Return all attachments associated with an entity (PM, company, platform) either directly or via expense/income."""
    from sqlalchemy.orm import joinedload

    # Direct assignments
    direct_query = db.query(Attachment).options(
        joinedload(Attachment.expense).joinedload(Expense.apartment),
        joinedload(Attachment.income).joinedload(Income.apartment),
        joinedload(Attachment.apartment),
        joinedload(Attachment.property_manager),
    )

    if entity_type == 'pm':
        direct = direct_query.filter(Attachment.property_manager_id == entity_id).all()
        # Via expense/income associated with this PM
        expense_ids = [e.id for e in db.query(Expense.id).filter(Expense.associated_pm_id == entity_id).all()]
        income_ids = [e.id for e in db.query(Income.id).filter(Income.associated_pm_id == entity_id).all()]
    elif entity_type == 'company':
        direct = []
        expense_ids = [e.id for e in db.query(Expense.id).filter(Expense.associated_company_id == entity_id).all()]
        income_ids = []
    elif entity_type == 'platform':
        direct = []
        expense_ids = []
        income_ids = [e.id for e in db.query(Income.id).filter(Income.platform_id == entity_id).all()]
    else:
        return []

    via_entries = []
    if expense_ids:
        via_entries.extend(db.query(Attachment).options(
            joinedload(Attachment.expense).joinedload(Expense.apartment),
        ).filter(Attachment.expense_id.in_(expense_ids)).all())
    if income_ids:
        via_entries.extend(db.query(Attachment).options(
            joinedload(Attachment.income).joinedload(Income.apartment),
        ).filter(Attachment.income_id.in_(income_ids)).all())

    seen = set()
    result = []
    for a in direct + via_entries:
        if a.id not in seen:
            seen.add(a.id)
            result.append(a)
    return result


@router.get('/by-entity/{entity_type}/{entity_id}')
async def attachments_by_entity(request: Request, entity_type: str, entity_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        from app.models import PropertyManager, Company, Platform
        entity_name = ''
        if entity_type == 'pm':
            pm = db.query(PropertyManager).filter(PropertyManager.id == entity_id).first()
            entity_name = f"{pm.first_name} {pm.last_name}" if pm else 'PM'
        elif entity_type == 'company':
            c = db.query(Company).filter(Company.id == entity_id).first()
            entity_name = c.company_name if c else 'Azienda'
        elif entity_type == 'platform':
            p = db.query(Platform).filter(Platform.id == entity_id).first()
            entity_name = p.name if p else 'Piattaforma'
        else:
            return RedirectResponse(url='/attachments')

        attachments = _get_attachments_for_entity(db, entity_type, entity_id)
        next_url = request.query_params.get('next') or '/attachments'
        return templates.TemplateResponse(request, "attachments_by_entity.html", {
            "attachments": attachments,
            "entity_name": entity_name,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "next": next_url,
        })
    finally:
        db.close()


@router.get('/api/lists')
async def api_lists(request: Request):
    if not get_current_user(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    db = SessionLocal()
    try:
        apartments = [{"id": a.id, "name": a.name} for a in db.query(Apartment).all()]
        pms = [{"id": pm.id, "name": f"{pm.first_name} {pm.last_name}"} for pm in db.query(PropertyManager).all()]
        return JSONResponse(content={"apartments": apartments, "pms": pms})
    finally:
        db.close()


@router.get('/api/stats')
async def api_attachment_stats(request: Request):
    if not get_current_user(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    db = SessionLocal()
    try:
        from datetime import datetime
        total = db.query(Attachment).count()
        by_year = {}
        for a in db.query(Attachment).all():
            entry = a.expense or a.income
            yr = None
            if entry and entry.date:
                try:
                    yr = datetime.strptime(entry.date, '%Y-%m-%d').year
                except Exception:
                    pass
            if yr is None and a.created_at:
                yr = a.created_at.year
            if yr:
                by_year[yr] = by_year.get(yr, 0) + 1
        by_type = {}
        for a in db.query(Attachment).all():
            if a.expense_id:
                by_type['expense'] = by_type.get('expense', 0) + 1
            elif a.income_id:
                by_type['income'] = by_type.get('income', 0) + 1
            else:
                by_type['none'] = by_type.get('none', 0) + 1
        return JSONResponse(content={
            "total": total,
            "by_year": {str(k): v for k, v in sorted(by_year.items())},
            "by_type": by_type,
        })
    finally:
        db.close()
