import os
from fastapi import Request

DEV_DEBUG_LOG_REQUESTS = os.getenv("DEV_DEBUG_LOG_REQUESTS", "0") == "1"
REDACT_KEYS = {"password", "password_hash", "new_password", "confirm_password"}


def _redact_value(k, v):
    if k.lower() in REDACT_KEYS:
        return "<REDACTED>"
    try:
        return v if isinstance(v, str) else str(v)
    except Exception:
        return str(v)


async def log_request_form(request: Request):
    if not DEV_DEBUG_LOG_REQUESTS:
        return
    try:
        form = await request.form()
        data = {k: _redact_value(k, v) for k, v in form.items()}
        print("DEBUG REQUEST FORM:", data)
    except Exception as e:
        print("DEBUG REQUEST FORM error: ", e)


async def log_request_json(request: Request):
    if not DEV_DEBUG_LOG_REQUESTS:
        return
    try:
        json = await request.json()
        redacted = {k: ("<REDACTED>" if k.lower() in REDACT_KEYS else v) for k, v in json.items()} if isinstance(json, dict) else json
        print("DEBUG REQUEST JSON:", redacted)
    except Exception as e:
        print("DEBUG REQUEST JSON error: ", e)
