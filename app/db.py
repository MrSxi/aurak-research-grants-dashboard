from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def init_db():
    from app import models  # noqa: F401  (register tables)
    Base.metadata.create_all(engine)
    _add_missing_columns()


# Columns added after the first import. create_all() does not alter existing tables, so add them in place.
_NEW_COLUMNS = {
    "students": {"student_number": "VARCHAR(30)", "access_status": "VARCHAR(12) NOT NULL DEFAULT 'none'"},
}


def _add_missing_columns():
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _NEW_COLUMNS.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    add = "ADD" if engine.dialect.name == "mssql" else "ADD COLUMN"  # T-SQL has no COLUMN keyword
                    conn.execute(text(f"ALTER TABLE {table} {add} {name} {ddl}"))
