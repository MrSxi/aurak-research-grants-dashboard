"""Build the database: import the dataset ZIP, enrich faculty from OpenAlex, fetch grants, compute matches.

Usage (from the project folder):
    .venv\\Scripts\\python scripts\\import_data.py               # full import
    .venv\\Scripts\\python scripts\\import_data.py --skip-grants # dataset + OpenAlex only
    .venv\\Scripts\\python scripts\\import_data.py --zip path\\to\\new.zip
Re-running is safe: every step updates records instead of duplicating them.
"""
import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import openalex  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.importer import import_dataset  # noqa: E402
from app.services import refresh  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=None)
    ap.add_argument("--skip-openalex", action="store_true")
    ap.add_argument("--skip-grants", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    init_db()
    db = SessionLocal()
    counts = import_dataset(db, args.zip)
    db.commit()
    print("Dataset imported:", counts)
    if not args.skip_openalex:
        with openalex.client() as c:
            methods = openalex.match_faculty(db, c)
            db.commit()
            print("OpenAlex author matching:", methods)
            for f in db.query(openalex.Faculty).filter(openalex.Faculty.openalex_match_method.like("%mismatch%")):
                print(f"  REVIEW: {f.full_name}: ORCID points to a different OpenAlex name; not linked")
    db.close()
    refresh.sync_sources(SessionLocal())
    if args.skip_grants:
        db = SessionLocal()
        from app.services.matching import engine
        engine.rebuild(db)
        engine.match_grants(db)
        db.close()
    else:
        print("Refreshing grant sources, OpenAlex works, matches and notifications (takes a few minutes)...")
        print(refresh.refresh_all(trigger="import", include_openalex=not args.skip_openalex))


if __name__ == "__main__":
    main()
