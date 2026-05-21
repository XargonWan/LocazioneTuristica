#!/usr/bin/env python3
import argparse
import os
import sqlite3


def resolve_db_path(raw_value):
    db_value = raw_value or os.getenv("DATABASE_URL", "data/db.sqlite3")
    if db_value.startswith("sqlite:///"):
        return db_value.replace("sqlite:///", "", 1)
    return db_value


def round2(value):
    return round(float(value or 0.0), 2)


def compute_taxable_amount(gross_amount, vat_percent):
    vat_factor = 1 + (float(vat_percent or 0.0) / 100.0)
    if vat_factor <= 0:
        raise ValueError("vat_percent must be greater than -100")
    return round(round2(gross_amount) / vat_factor, 2)


def has_changed(old_value, new_value):
    return round2(old_value) != round2(new_value)


def sample_rows(rows, limit):
    return rows[:limit]


def collect_expense_updates(cur):
    cur.execute(
        """
        SELECT id, gross_amount, vat_percent, net_amount, net_after_pm
        FROM expense
        ORDER BY id
        """
    )
    updates = []
    skipped = []
    for row in cur.fetchall():
        try:
            new_net_amount = compute_taxable_amount(row["gross_amount"], row["vat_percent"])
        except ValueError as exc:
            skipped.append((row["id"], str(exc)))
            continue
        new_net_after_pm = new_net_amount
        if not has_changed(row["net_amount"], new_net_amount) and not has_changed(row["net_after_pm"], new_net_after_pm):
            continue
        updates.append(
            {
                "id": row["id"],
                "gross_amount": round2(row["gross_amount"]),
                "vat_percent": round2(row["vat_percent"]),
                "old_net_amount": round2(row["net_amount"]),
                "new_net_amount": new_net_amount,
                "old_net_after_pm": round2(row["net_after_pm"]),
                "new_net_after_pm": new_net_after_pm,
            }
        )
    return updates, skipped


def collect_income_updates(cur):
    cur.execute(
        """
        SELECT id, gross_amount, vat_percent, net_amount, pm_amount, pm_percent, net_after_pm
        FROM income
        ORDER BY id
        """
    )
    updates = []
    skipped = []
    for row in cur.fetchall():
        try:
            new_net_amount = compute_taxable_amount(row["gross_amount"], row["vat_percent"])
        except ValueError as exc:
            skipped.append((row["id"], str(exc)))
            continue
        pm_amount = row["pm_amount"]
        if pm_amount is None:
            pm_amount = round(round2(row["gross_amount"]) * (round2(row["pm_percent"]) / 100.0), 2)
        new_net_after_pm = round(new_net_amount - round2(pm_amount), 2)
        if not has_changed(row["net_amount"], new_net_amount) and not has_changed(row["net_after_pm"], new_net_after_pm):
            continue
        updates.append(
            {
                "id": row["id"],
                "gross_amount": round2(row["gross_amount"]),
                "vat_percent": round2(row["vat_percent"]),
                "old_net_amount": round2(row["net_amount"]),
                "new_net_amount": new_net_amount,
                "pm_amount": round2(pm_amount),
                "old_net_after_pm": round2(row["net_after_pm"]),
                "new_net_after_pm": new_net_after_pm,
            }
        )
    return updates, skipped


def collect_cleaning_updates(cur):
    cur.execute(
        """
        SELECT id, gross_amount, vat_percent, net_amount, expense_id
        FROM cleaning
        ORDER BY id
        """
    )
    updates = []
    skipped = []
    for row in cur.fetchall():
        try:
            new_net_amount = compute_taxable_amount(row["gross_amount"], row["vat_percent"])
        except ValueError as exc:
            skipped.append((row["id"], str(exc)))
            continue
        if not has_changed(row["net_amount"], new_net_amount):
            continue
        updates.append(
            {
                "id": row["id"],
                "gross_amount": round2(row["gross_amount"]),
                "vat_percent": round2(row["vat_percent"]),
                "old_net_amount": round2(row["net_amount"]),
                "new_net_amount": new_net_amount,
                "expense_id": row["expense_id"],
            }
        )
    return updates, skipped


def print_section(title, updates, skipped, sample_limit):
    print(f"{title}: {len(updates)} rows to update")
    for row in sample_rows(updates, sample_limit):
        if "new_net_after_pm" in row:
            print(
                "  id={id} gross={gross_amount:.2f} vat={vat_percent:.2f}% "
                "net {old_net_amount:.2f}->{new_net_amount:.2f} "
                "net_after_pm {old_net_after_pm:.2f}->{new_net_after_pm:.2f}".format(**row)
            )
        else:
            print(
                "  id={id} gross={gross_amount:.2f} vat={vat_percent:.2f}% "
                "net {old_net_amount:.2f}->{new_net_amount:.2f}".format(**row)
            )
    if len(updates) > sample_limit:
        print(f"  ... {len(updates) - sample_limit} more")
    if skipped:
        print(f"  skipped: {len(skipped)} rows with invalid VAT")
        for row_id, reason in sample_rows(skipped, sample_limit):
            print(f"    id={row_id}: {reason}")


def main():
    parser = argparse.ArgumentParser(description="Recompute imponibile-based amounts from stored gross amounts.")
    parser.add_argument("--db", default=None, help="SQLite path or DATABASE_URL-style sqlite:/// path")
    parser.add_argument("--apply", action="store_true", help="Write the recalculated values back to the database")
    parser.add_argument("--sample-limit", type=int, default=5, help="How many sample rows to print per table")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"DB file: {db_path}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        expense_updates, expense_skipped = collect_expense_updates(cur)
        income_updates, income_skipped = collect_income_updates(cur)
        cleaning_updates, cleaning_skipped = collect_cleaning_updates(cur)

        cur.execute("SELECT expense_id FROM cleaning WHERE expense_id IS NOT NULL")
        cleaning_expense_ids = {row[0] for row in cur.fetchall()}
        linked_cleaning_expense_updates = [row for row in expense_updates if row["id"] in cleaning_expense_ids]

        cur.execute("SELECT COUNT(*) FROM company WHERE default_net_amount IS NOT NULL")
        company_default_net_count = cur.fetchone()[0]

        print_section("Expense", expense_updates, expense_skipped, args.sample_limit)
        print_section("Income", income_updates, income_skipped, args.sample_limit)
        print_section("Cleaning", cleaning_updates, cleaning_skipped, args.sample_limit)
        print(f"Cleaning-linked expense rows to update: {len(linked_cleaning_expense_updates)}")
        if company_default_net_count:
            print(
                "Company.default_net_amount rows left untouched for manual review: "
                f"{company_default_net_count}"
            )

        if args.apply:
            cur.executemany(
                "UPDATE expense SET net_amount = ?, net_after_pm = ? WHERE id = ?",
                [(row["new_net_amount"], row["new_net_after_pm"], row["id"]) for row in expense_updates],
            )
            cur.executemany(
                "UPDATE income SET net_amount = ?, net_after_pm = ? WHERE id = ?",
                [(row["new_net_amount"], row["new_net_after_pm"], row["id"]) for row in income_updates],
            )
            cur.executemany(
                "UPDATE cleaning SET net_amount = ? WHERE id = ?",
                [(row["new_net_amount"], row["id"]) for row in cleaning_updates],
            )
            conn.commit()
            print(
                "Applied updates: "
                f"expenses={len(expense_updates)}, incomes={len(income_updates)}, cleanings={len(cleaning_updates)}"
            )
        else:
            conn.rollback()
            print("Dry run completed. No changes written.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()