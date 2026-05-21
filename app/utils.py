from datetime import date, datetime

from app.db import SessionLocal
from app.models import Expense, Income, Recurrence, Settings


DATE_FORMAT = "%Y-%m-%d"


def parse_date_value(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw_value = str(value).strip()
    if len(raw_value) == 4 and raw_value.isdigit():
        raw_value = f"{raw_value}-01-01"
    elif len(raw_value) == 7:
        raw_value = f"{raw_value}-01"
    return datetime.strptime(raw_value, DATE_FORMAT).date()


def format_date_value(value):
    parsed_value = parse_date_value(value)
    return parsed_value.strftime(DATE_FORMAT) if parsed_value else None


def get_expense_net_amount(entry):
    gross_amount = float(getattr(entry, 'gross_amount', 0.0) or 0.0)
    vat_percent = float(getattr(entry, 'vat_percent', 0.0) or 0.0)
    if gross_amount:
        vat_factor = 1 + (vat_percent / 100.0)
        if vat_factor > 0:
            return round(gross_amount / vat_factor, 2)
    raw_net_amount = getattr(entry, 'net_amount', None)
    if raw_net_amount not in (None, ''):
        return round(float(raw_net_amount or 0.0), 2)
    return 0.0


def get_income_stamp_duty_amount(entry):
    raw_amount = getattr(entry, 'stamp_duty_amount', None)
    stamp_duty_amount = round(float(raw_amount or 0.0), 2)
    has_stamp_duty = bool(getattr(entry, 'has_stamp_duty', False))
    if not has_stamp_duty and stamp_duty_amount == 0.0:
        return 0.0
    return stamp_duty_amount


def get_income_pm_base_amount(entry):
    net_amount = round(float(getattr(entry, 'net_amount', 0.0) or 0.0), 2)
    return round(net_amount - get_income_stamp_duty_amount(entry), 2)


def get_income_pm_amount(entry, pm_percent: float | None = None):
    resolved_pm_percent = pm_percent
    if resolved_pm_percent is None:
        resolved_pm_percent = float(getattr(entry, 'pm_percent', 0.0) or 0.0)
    else:
        resolved_pm_percent = float(resolved_pm_percent or 0.0)
    return round(get_income_pm_base_amount(entry) * (resolved_pm_percent / 100.0), 2)


def get_income_effective_amount(entry, pm_percent: float | None = None):
    pm_base_amount = get_income_pm_base_amount(entry)
    return round(pm_base_amount - get_income_pm_amount(entry, pm_percent=pm_percent), 2)


def get_pm_payment_settlement_amount(entry):
    if not getattr(entry, 'associated_pm_id', None):
        return 0.0
    return get_expense_net_amount(entry)


def get_pm_payment_cash_amount(entry):
    if not getattr(entry, 'associated_pm_id', None):
        return 0.0
    return round(float(getattr(entry, 'gross_amount', 0.0) or 0.0), 2)


def normalize_recurrence_date(value):
    return format_date_value(value)


def advance_recurrence_date(current_date, recurrence_type, steps=1):
    current = parse_date_value(current_date)
    if not current:
        return None
    if recurrence_type == "monthly":
        year = current.year + (current.month - 1 + steps) // 12
        month = (current.month - 1 + steps) % 12 + 1
        day = min(current.day, 28)
        return date(year, month, day)
    if recurrence_type == "yearly":
        try:
            return current.replace(year=current.year + steps)
        except ValueError:
            return current.replace(year=current.year + steps, day=min(current.day, 28))
    raise ValueError(f"Unsupported recurrence type: {recurrence_type}")


def recurrence_effective_end_date(recurrence, current_year=None):
    if recurrence.end_date:
        return parse_date_value(recurrence.end_date)
    target_year = current_year or datetime.now().year
    return date(target_year, 12, 31)


def build_recurrence_entry(model_cls, source_entry, recurrence_id, entry_date):
    entry_date_value = format_date_value(entry_date)
    if model_cls is Expense:
        return Expense(
            apartment_id=source_entry.apartment_id,
            date=entry_date_value,
            gross_amount=source_entry.gross_amount,
            vat_percent=source_entry.vat_percent,
            net_amount=source_entry.net_amount,
            pm_percent=source_entry.pm_percent,
            pm_amount=source_entry.pm_amount,
            net_after_pm=source_entry.net_after_pm,
            category=source_entry.category,
            is_cleaning=source_entry.is_cleaning,
            associated_pm_id=source_entry.associated_pm_id,
            associated_company_id=source_entry.associated_company_id,
            recurrence_id=recurrence_id,
            notes=source_entry.notes,
            created_by=source_entry.created_by,
        )
    if model_cls is Income:
        return Income(
            apartment_id=source_entry.apartment_id,
            platform_id=source_entry.platform_id,
            date=entry_date_value,
            gross_amount=source_entry.gross_amount,
            vat_percent=source_entry.vat_percent,
            net_amount=source_entry.net_amount,
            has_stamp_duty=source_entry.has_stamp_duty,
            stamp_duty_amount=source_entry.stamp_duty_amount,
            pm_percent=source_entry.pm_percent,
            pm_amount=source_entry.pm_amount,
            net_after_pm=source_entry.net_after_pm,
            recurrence_id=recurrence_id,
            associated_pm_id=source_entry.associated_pm_id,
            notes=source_entry.notes,
            created_by=source_entry.created_by,
        )
    raise ValueError(f"Unsupported series model: {model_cls}")


def next_recurrence_cursor(db, model_cls, recurrence, start_date=None):
    cursor = parse_date_value(recurrence.next_date)
    if cursor:
        return cursor
    last_entry = (
        db.query(model_cls)
        .filter(model_cls.recurrence_id == recurrence.id)
        .order_by(model_cls.date.desc())
        .first()
    )
    if last_entry and last_entry.date:
        return advance_recurrence_date(last_entry.date, recurrence.type)
    return parse_date_value(start_date or recurrence.start_date)


def prune_duplicate_recurrence_entries(db, model_cls, recurrence_id, preferred_entry_id=None):
    entries = (
        db.query(model_cls)
        .filter(model_cls.recurrence_id == recurrence_id)
        .order_by(model_cls.date.asc(), model_cls.id.asc())
        .all()
    )
    kept_by_date = {}
    removed = 0
    for entry in entries:
        existing = kept_by_date.get(entry.date)
        if existing is None:
            kept_by_date[entry.date] = entry
            continue
        if preferred_entry_id and entry.id == preferred_entry_id:
            db.delete(existing)
            kept_by_date[entry.date] = entry
        else:
            db.delete(entry)
        removed += 1
    return removed


def sync_recurrence_entries(db, model_cls, recurrence, source_entry=None, current_year=None, reset=False):
    if not recurrence or recurrence.type not in ("monthly", "yearly") or not recurrence.start_date:
        return 0, 0

    start_date = parse_date_value(recurrence.start_date)
    end_date = recurrence_effective_end_date(recurrence, current_year=current_year)
    if not start_date or not end_date or start_date > end_date:
        recurrence.next_date = format_date_value(start_date)
        db.add(recurrence)
        return 0, 0

    removed_duplicates = prune_duplicate_recurrence_entries(
        db,
        model_cls,
        recurrence.id,
        preferred_entry_id=(getattr(source_entry, "id", None) if source_entry else None),
    )

    template_entry = source_entry
    if not template_entry:
        template_entry = (
            db.query(model_cls)
            .filter(model_cls.recurrence_id == recurrence.id)
            .order_by(model_cls.date.desc())
            .first()
        )
    if not template_entry:
        recurrence.next_date = format_date_value(start_date)
        db.add(recurrence)
        return 0, removed_duplicates

    cursor = start_date if reset else next_recurrence_cursor(db, model_cls, recurrence, start_date=start_date)
    inserted = 0
    while cursor and cursor <= end_date:
        cursor_value = format_date_value(cursor)
        exists = (
            db.query(model_cls)
            .filter(model_cls.recurrence_id == recurrence.id, model_cls.date == cursor_value)
            .first()
        )
        if not exists:
            db.add(build_recurrence_entry(model_cls, template_entry, recurrence.id, cursor))
            inserted += 1
        cursor = advance_recurrence_date(cursor, recurrence.type)

    recurrence.next_date = format_date_value(cursor)
    db.add(recurrence)
    return inserted, removed_duplicates


def expand_open_recurrences_to_current_year(db, current_year=None):
    target_year = current_year or datetime.now().year
    inserted = 0
    changed = False
    for model_cls in (Expense, Income):
        recurrence_ids = [
            recurrence_id
            for (recurrence_id,) in db.query(model_cls.recurrence_id)
            .filter(model_cls.recurrence_id.isnot(None))
            .distinct()
            .all()
            if recurrence_id
        ]
        if not recurrence_ids:
            continue
        recurrences = (
            db.query(Recurrence)
            .filter(Recurrence.id.in_(recurrence_ids), Recurrence.end_date.is_(None))
            .all()
        )
        for recurrence in recurrences:
            new_rows, removed_rows = sync_recurrence_entries(db, model_cls, recurrence, current_year=target_year, reset=False)
            inserted += new_rows
            changed = changed or bool(new_rows or removed_rows)
    if changed:
        db.commit()
    return inserted


def get_setting(key: str, default=None):
    db = SessionLocal()
    try:
        s = db.query(Settings).filter(Settings.key == key).first()
        if s:
            return s.value
        return default
    finally:
        db.close()


def get_setting_int(key: str, default: int, minimum: int | None = None):
    raw_value = get_setting(key, str(default))
    try:
        parsed_value = int(raw_value)
    except (TypeError, ValueError):
        parsed_value = default

    if minimum is not None and parsed_value < minimum:
        return default
    return parsed_value


def get_setting_float(key: str, default: float, minimum: float | None = None):
    raw_value = get_setting(key, str(default))
    try:
        parsed_value = float(raw_value)
    except (TypeError, ValueError):
        parsed_value = default

    if minimum is not None and parsed_value < minimum:
        return default
    return parsed_value
