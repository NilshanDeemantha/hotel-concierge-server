"""Puts the dishes of the à la carte menu (scripts/menu_items.json) in the `menuItems` collection:
python scripts/seed_menu.py [--dry-run]

One document per dish, named <meal>-<dish name>, so running it again never makes a copy. A dish that is already
there only gets fields it does not have yet (for example a new `course`), so changes staff made in the database
(a new price, `available: false`) are never overwritten.
Uses Application Default Credentials (gcloud auth application-default login).
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = "thambili-bay-concierge"
ITEMS = Path(__file__).with_name("menu_items.json")


def doc_id(item: dict) -> str:
    return f"{item['meal']}-{re.sub(r'[^a-z0-9]+', '-', item['name'].lower()).strip('-')}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    items = json.loads(ITEMS.read_text())
    if args.dry_run:
        print(f"{len(items)} dishes would be saved, for example {doc_id(items[0])}")
        return 0

    import firebase_admin
    from firebase_admin import firestore

    firebase_admin.initialize_app(options={"projectId": PROJECT})
    db = firestore.client()
    added = filled = kept = 0
    for item in items:
        ref = db.collection("menuItems").document(doc_id(item))
        found = ref.get()
        if found.exists:
            missing = {k: v for k, v in item.items() if k not in (found.to_dict() or {})}
            if missing:
                ref.update({**missing, "updatedAt": firestore.SERVER_TIMESTAMP})
                filled += 1
            else:
                kept += 1
            continue
        ref.set({**item, "available": True, "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP})
        added += 1
    print(f"menuItems: {added} added, {filled} given new fields, {kept} already complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
