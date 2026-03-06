import pytest

from app.main import templates


class R:
    def __init__(self):
        self.session = {"username": "tester", "role": "admin"}
        self.path = "/"
        self.cookies = {}


def test_incomes_template_shows_pm_and_delete_modal():
    fake_inc = type("X", (), {
        "id": 1,
        "date": "2025-12-01",
        "gross_amount": 100,
        "net_amount": 78.0,
        "vat_percent": 22.0,
        "pm_percent": 10.0,
        "pm_amount": 10.0,
        "associated_pm_name": "PM One",
        "notes": "Incasso pulito",
    })
    rendered = templates.env.get_template("incomes_index.html").render(request=R(), incomes=[fake_inc], apartments=[], platforms=[], attachments=[], attachments_by_income={}, pms=[], default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, next="/money/incomes")
    assert "PM One" in rendered
    # inline collapse delete confirmation should be present
    assert "delInlineIncome-1" in rendered
    # ensure select-all label id exists
    assert 'selectAllIncomesLabel' in rendered


def test_expenses_template_title_and_delete_modal():
    fake_e = type("X", (), {
        "id": 9,
        "date": "2025-12-01",
        "gross_amount": 100,
        "net_amount": 78.0,
        "vat_percent": 22.0,
        "pm_percent": 15.0,
        "pm_amount": 15.0,
        "net_after_pm": 63.0,
        "associated_pm_name": "PM Two",
        "notes": "Riparazione caldaia",
    })
    rendered = templates.env.get_template("expenses_index.html").render(request=R(), expenses=[fake_e], apartments=[], attachments=[], pms=[], attachments_by_expense={}, default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, apt_pm_map={}, next="/money/expenses")
    assert "Dettagli Spesa Riparazione caldaia" in rendered
    assert "deleteConfirmExpense-9" in rendered
    # inline collapse delete confirmation should be present
    assert "delInlineExp-9" in rendered
    # ensure select-all labels are present (hidden by default)
    assert 'selectAllExpensesLabel' in rendered


def test_overview_has_table_borders():
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}, {"month": 2, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: [], 2: []}, year=2025, current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "table table-sm" in rendered


def test_overview_income_shows_net_and_inline_delete():
    # Provide an income entry in entries_by_month with net_amount
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: [{'type': 'income', 'date': '2025-01-01', 'gross_amount': 100.0, 'net_amount': 78.0, 'notes': 'Rent', 'id': 1}]}, year=2025, current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "Importo netto" in rendered
    assert "delInlineOvInc-1" in rendered


def test_overview_year_navigation_links():
    # header should show the year and have previous/next links
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: []}, year=2025, current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "Rendiconto 2025" in rendered
    # previous arrow link should decrement year
    assert "/overview?year=2024" in rendered
    # forward arrow should not be disabled when current_year equals year (uses span disabled)
    assert "aria-disabled" in rendered

def test_overview_bulk_delete_capture_is_async():
    # previous bug was using a non-async listener which caused a syntax error
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: []}, year=2025, current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "async function captureBulkDeleteOv" in rendered
