r"""Copy the whole SQLite database (data/aurak.db) into Microsoft SQL Server.

The SQLite file is only read, never changed. Run with the dashboard stopped:

    .venv-local\Scripts\python scripts\migrate_to_mssql.py "mssql+pyodbc://@localhost\SQLEXPRESS/aurak?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes&TrustServerCertificate=yes"

The target database (here "aurak") is created if it does not exist, with a binary collation so text sorts and
compares exactly as it did in SQLite. An existing database must be empty. Afterwards, put the same URL in .env
as DATABASE_URL=... and start the dashboard as usual.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

if len(sys.argv) != 2 or not sys.argv[1].startswith("mssql"):
    sys.exit(__doc__)
TARGET = sys.argv[1]

import os  # noqa: E402

os.environ["DATABASE_URL"] = TARGET  # models are built for SQL Server (e.g. no index on long text columns)

from sqlalchemy import create_engine, func, insert, select  # noqa: E402

from app.config import DATA_DIR  # noqa: E402
from app.db import Base  # noqa: E402
from app import models  # noqa: E402,F401

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

# SQLite sorts and compares text by exact character codes (so "ATRC" sorts before "Abu"). A binary collation
# makes SQL Server do the same, so lists keep the order they had.
COLLATION = "Latin1_General_100_BIN2"
url = make_url(TARGET)
master = create_engine(url.set(database="master"), isolation_level="AUTOCOMMIT")
with master.connect() as m:
    found = m.execute(text("SELECT collation_name FROM sys.databases WHERE name = :n"), {"n": url.database}).first()
    if found is None:
        m.execute(text(f"CREATE DATABASE [{url.database}] COLLATE {COLLATION}"))
        print(f"Created database {url.database} ({COLLATION})")
    elif found[0] != COLLATION:
        sys.exit(f"Database {url.database} uses collation {found[0]}; lists would sort differently than in SQLite.\n"
                 f"Drop it (or pick a new name) and run this script again: it creates the database with {COLLATION}.")

source = create_engine(f"sqlite:///{(DATA_DIR / 'aurak.db').as_posix()}")
target = create_engine(TARGET, fast_executemany=True)

with target.connect() as conn:
    existing = [t.name for t in Base.metadata.sorted_tables if target.dialect.has_table(conn, t.name)]
    for name in existing:
        if conn.execute(select(func.count()).select_from(Base.metadata.tables[name])).scalar():
            sys.exit(f"Table {name} in the target already has rows. Use an empty database.")

Base.metadata.create_all(target)
with source.connect() as src, target.begin() as dst:
    for table in Base.metadata.sorted_tables:  # parents before children (foreign keys)
        rows = [dict(r._mapping) for r in src.execute(select(table))]
        for i in range(0, len(rows), 1000):
            # the SQL Server dialect switches IDENTITY_INSERT on when ids are given, so every id is kept
            dst.execute(insert(table), rows[i:i + 1000])
        print(f"{table.name}: {len(rows)} rows")
print("Done. Set DATABASE_URL in .env to the same URL and start the dashboard.")
