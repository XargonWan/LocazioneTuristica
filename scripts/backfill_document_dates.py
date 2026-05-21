#!/usr/bin/env python3
"""Set document_date for existing attachments from filesystem mtime.

Usage:
    python scripts/backfill_document_dates.py          # dry-run (no changes)
    python scripts/backfill_document_dates.py --apply   # actually update DB
    python scripts/backfill_document_dates.py --apply --dry-run  # show what would change
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import SessionLocal
from app.models import Attachment


def main():
    parser = argparse.ArgumentParser(description="Backfill document_date from disk mtime")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without applying")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        attachments = (
            db.query(Attachment)
            .filter(Attachment.document_date.is_(None))
            .order_by(Attachment.id)
            .all()
        )
    except Exception:
        attachments = db.query(Attachment).order_by(Attachment.id).all()

    updated = 0
    skipped = 0
    errors = 0

    for att in attachments:
        disk_path = att.disk_path
        if not disk_path or not os.path.exists(disk_path):
            skipped += 1
            if args.dry_run or args.apply:
                print(f"  SKIP  id={att.id}  file not found: {disk_path}")
            continue

        mtime = os.path.getmtime(disk_path)
        doc_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

        if args.dry_run or args.apply:
            old = att.document_date or "(none)"
            print(f"  {'SET' if args.apply else 'WOULD SET':6s} id={att.id:4d}  {old} -> {doc_date}  {att.filename}")

        if args.apply:
            att.document_date = doc_date

        updated += 1

    if args.apply:
        db.commit()
        print(f"\nUpdated {updated} attachments ({skipped} skipped, {errors} errors)")
    else:
        print(f"\nWould update {updated} attachments ({skipped} skipped, {errors} errors)")
        print("Pass --apply to write changes.")

    db.close()


if __name__ == "__main__":
    main()
