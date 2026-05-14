from fastapi import APIRouter, UploadFile, File, Form, Request, Depends
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from fastapi.templating import Jinja2Templates
import os
from app.db import SessionLocal
from app.auth_utils import admin_required, get_current_user
from app.models import Attachment, Expense, Income
from app.utils import get_setting

router = APIRouter(prefix="/attachments")
from app.main import templates

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/attachments")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

PREVIEW_IMAGE_TYPES = {"image/jpeg", "image/png"}


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


@router.get("")
async def index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        from sqlalchemy.orm import joinedload

        attachments = (
            db.query(Attachment)
            .options(
                joinedload(Attachment.expense).joinedload(Expense.apartment),
                joinedload(Attachment.income).joinedload(Income.apartment),
            )
            .order_by(Attachment.created_at.desc())
            .all()
        )
        next_url = request.query_params.get('next') or '/attachments'
        return templates.TemplateResponse(request, "attachments_index.html", {"attachments": attachments, "next": next_url})
    finally:
        db.close()


@router.post("/upload")
async def upload(
    request: Request,
    files: list[UploadFile] = File(..., alias="file"),
    next: str = Form(None),
    expense_id: int = Form(None),
    income_id: int = Form(None),
    user=Depends(admin_required),
):
    max_size = int(get_setting('max_upload_size', str(10 * 1024 * 1024)))
    allowed = ['application/pdf', 'image/jpeg', 'image/png', 'application/vnd.oasis.opendocument.text', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']
    uploaded_files = []
    for uploaded_file in files:
        if not uploaded_file.filename:
            continue
        content = await uploaded_file.read()
        if uploaded_file.content_type not in allowed:
            return RedirectResponse(url="/attachments?error=invalid_type", status_code=HTTP_303_SEE_OTHER)
        if len(content) > max_size:
            return RedirectResponse(url="/attachments?error=oversize", status_code=HTTP_303_SEE_OTHER)
        uploaded_files.append((uploaded_file.filename, content, uploaded_file.content_type))
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
        for filename, content, content_type in uploaded_files:
            path = os.path.join(UPLOAD_DIR, filename)
            with open(path, "wb") as f:
                f.write(content)
            attachment = Attachment(
                filename=filename,
                disk_path=path,
                mimetype=content_type,
                size=len(content),
                expense_id=target_expense_id,
                income_id=target_income_id,
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
        attachment = _get_attachment(db, attachment_id)
        if not attachment:
            return RedirectResponse(url='/attachments', status_code=HTTP_303_SEE_OTHER)
        file_exists = bool(attachment.disk_path and os.path.exists(attachment.disk_path))
        preview_kind = _attachment_preview_kind(attachment)
        preview_available = bool(file_exists and preview_kind is not None)
        next_url = _attachment_request_next(request, attachment)
        return templates.TemplateResponse(
            request,
            'attachment_view.html',
            {
                "attachment": attachment,
                "next_url": next_url,
                "file_exists": file_exists,
                "preview_kind": preview_kind,
                "preview_available": preview_available,
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

