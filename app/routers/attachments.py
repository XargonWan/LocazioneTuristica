from fastapi import APIRouter, UploadFile, File, Request, Depends
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from fastapi.templating import Jinja2Templates
import os
from app.db import SessionLocal
from app.auth_utils import admin_required, get_current_user
from app.models import Attachment
from app.utils import get_setting

router = APIRouter(prefix="/attachments")
from app.main import templates

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/attachments")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("")
async def index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        attachments = db.query(Attachment).order_by(Attachment.created_at.desc()).limit(50).all()
        return templates.TemplateResponse(request, "attachments_index.html", {"attachments": attachments})
    finally:
        db.close()


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), user=Depends(admin_required)):
    # Basic validation
    filename = file.filename
    content = await file.read()
    max_size = int(get_setting('max_upload_size', str(10 * 1024 * 1024)))
    allowed = ['application/pdf', 'image/jpeg', 'image/png', 'application/vnd.oasis.opendocument.text', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']
    if file.content_type not in allowed:
        return RedirectResponse(url="/attachments?error=invalid_type", status_code=HTTP_303_SEE_OTHER)
    if len(content) > max_size:
        return RedirectResponse(url="/attachments?error=oversize", status_code=HTTP_303_SEE_OTHER)
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(content)
    db = SessionLocal()
    try:
        attachment = Attachment(filename=filename, disk_path=path, mimetype=file.content_type, size=len(content))
        db.add(attachment)
        db.commit()
        return RedirectResponse(url="/attachments", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get('/download/{attachment_id}')
async def download_attachment(request: Request, attachment_id: int):
    db = SessionLocal()
    try:
        a = db.query(Attachment).filter(Attachment.id == attachment_id).first()
        if not a:
            return RedirectResponse(url='/attachments')
        return FileResponse(path=a.disk_path, filename=a.filename, media_type=a.mimetype)
    finally:
        db.close()

