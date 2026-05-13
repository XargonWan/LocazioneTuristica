from fastapi import APIRouter, Request, Form, Depends, HTTPException
from starlette.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from fastapi.templating import Jinja2Templates
from app.db import SessionLocal
from app.models import User
from app.auth_utils import get_current_user, admin_required
from passlib.context import CryptContext
from fastapi.templating import Jinja2Templates
from app.auth_utils import admin_required, get_current_user

router = APIRouter(prefix="/auth")

from app.main import templates

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def get_user_by_username(db, username: str):
    return db.query(User).filter(User.username == username).first()


def get_current_user(request: Request):
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user
    finally:
        db.close()


def admin_required(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        user = get_user_by_username(db, username)
        if not user:
            print('LOGIN DEBUG: user not found', username)
            return RedirectResponse(url="/login?failed=1", status_code=HTTP_303_SEE_OTHER)
        # Debug logs for verification (temporary)
        print('LOGIN DEBUG: user found', user.username, 'hash present', bool(user.password_hash))
        try:
            ok_verify = pwd_context.verify(password, user.password_hash) if user.password_hash else False
        except Exception as e:
            ok_verify = False
            print('LOGIN DEBUG: verify exception', e)
        print('LOGIN DEBUG: verify_result', ok_verify)
        if not user.password_hash or not ok_verify:
            return RedirectResponse(url="/login?failed=1", status_code=HTTP_303_SEE_OTHER)
        # write session cookie
        request.session['user_id'] = user.id
        request.session['username'] = user.username
        request.session['role'] = user.role
        if user.must_change_password:
            return RedirectResponse(url="/auth/set-password", status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/overview", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get("/set-password")
async def set_password_get(request: Request):
    return templates.TemplateResponse(request, "set_password.html", {})


@router.post("/set-password")
async def set_password_post(request: Request, new_password: str = Form(...)):
    db = SessionLocal()
    try:
        user_id = request.session.get('user_id')
        if not user_id:
            return RedirectResponse(url="/auth/login", status_code=HTTP_303_SEE_OTHER)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return RedirectResponse(url="/auth/login", status_code=HTTP_303_SEE_OTHER)
        user.password_hash = pwd_context.hash(new_password)
        user.must_change_password = False
        db.add(user)
        db.commit()
        return RedirectResponse(url="/overview", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get('/logout')
async def logout(request: Request):
    request.session.pop('user_id', None)
    request.session.pop('username', None)
    request.session.pop('role', None)
    return RedirectResponse(url='/login')

@router.get('/users')
async def users_index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url='/login')
    if user.role != 'admin':
        return RedirectResponse(url='/')
    db = SessionLocal()
    try:
        users = db.query(User).all()
        next_url = request.query_params.get('next') or '/auth/users'
        return templates.TemplateResponse(request, 'users_index.html', {'users': users, 'next': next_url})
    finally:
        db.close()

@router.post('/users/add')
async def add_user(request: Request, username: str = Form(...), role: str = Form('readonly'), next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        new = User(username=username, role=role, must_change_password=True)
        db.add(new)
        db.commit()
        return RedirectResponse(url=(next or '/auth/users'), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()
