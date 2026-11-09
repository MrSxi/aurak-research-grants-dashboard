"""Single database schema. SQLite for the prototype; PostgreSQL later is a DATABASE_URL change."""
from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, Unicode, UnicodeText,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy.dialects.mssql import DATETIME2

from app.config import DATABASE_URL
from app.db import Base


# SQL Server's DATETIME keeps ~3 ms; DATETIME2 keeps microseconds like SQLite does
DT = DateTime().with_variant(DATETIME2(), "mssql")


def now():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Unicode(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(Unicode(200))
    password_hash: Mapped[str] = mapped_column(Unicode(300))
    role: Mapped[str] = mapped_column(Unicode(20))  # student / faculty / admin
    faculty_id: Mapped[str | None] = mapped_column(ForeignKey("faculty.id"), nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=now)


class Faculty(Base):
    __tablename__ = "faculty"
    id: Mapped[str] = mapped_column(Unicode(20), primary_key=True)  # FAC_0001 from the dataset
    full_name: Mapped[str] = mapped_column(Unicode(200), index=True)
    title: Mapped[str | None] = mapped_column(Unicode(300))
    school: Mapped[str | None] = mapped_column(Unicode(200))
    department: Mapped[str | None] = mapped_column(Unicode(200))
    email: Mapped[str | None] = mapped_column(Unicode(200))
    profile_url: Mapped[str | None] = mapped_column(Unicode(500))
    image_url: Mapped[str | None] = mapped_column(Unicode(500))
    biography: Mapped[str | None] = mapped_column(UnicodeText)
    education: Mapped[str | None] = mapped_column(UnicodeText)
    google_scholar_url: Mapped[str | None] = mapped_column(Unicode(500))
    orcid_url: Mapped[str | None] = mapped_column(Unicode(300))
    scopus_urls: Mapped[list] = mapped_column(JSON, default=list)
    research_keywords: Mapped[list] = mapped_column(JSON, default=list)
    research_interests_status: Mapped[str | None] = mapped_column(Unicode(100))
    openalex_id: Mapped[str | None] = mapped_column(Unicode(100))
    openalex_match_method: Mapped[str | None] = mapped_column(Unicode(100))
    openalex_topics: Mapped[list] = mapped_column(JSON, default=list)
    works_count: Mapped[int | None] = mapped_column(Integer)
    cited_by_count: Mapped[int | None] = mapped_column(Integer)
    openalex_checked_at: Mapped[datetime | None] = mapped_column(DT)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)  # future opt-out
    source_url: Mapped[str | None] = mapped_column(Unicode(500))


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(Unicode(200))
    email: Mapped[str | None] = mapped_column(Unicode(200))
    student_number: Mapped[str | None] = mapped_column(Unicode(30), index=True)  # AURAK student ID (digits)
    # Dashboard access for signed-up students: none / requested / approved / denied.
    # Until an admin approves, a student only sees their own profile and can request access (Research Assistants).
    access_status: Mapped[str] = mapped_column(Unicode(12), default="none")
    school: Mapped[str | None] = mapped_column(Unicode(200))
    major: Mapped[str | None] = mapped_column(Unicode(200))
    source: Mapped[str] = mapped_column(Unicode(50), default="signup")  # signup / dataset
    source_url: Mapped[str | None] = mapped_column(Unicode(500))
    related_faculty_id: Mapped[str | None] = mapped_column(Unicode(20))
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DT, default=now)


class Interest(Base):
    __tablename__ = "interests"
    # SQL Server cannot put long text in a unique index; there the app's own duplicate check (done before
    # every insert) is the guard. Other databases keep the constraint as well.
    __table_args__ = () if DATABASE_URL.startswith("mssql") else (UniqueConstraint("owner_type", "owner_id", "text_norm"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(Unicode(10))  # faculty / student
    owner_id: Mapped[str] = mapped_column(Unicode(20), index=True)
    text: Mapped[str] = mapped_column(UnicodeText)  # some official interest statements are 1,000+ characters
    text_norm: Mapped[str] = mapped_column(UnicodeText)
    source: Mapped[str] = mapped_column(Unicode(30))  # dataset / student / admin
    status: Mapped[str] = mapped_column(Unicode(10), default="approved")  # pending / approved / denied
    reason: Mapped[str | None] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(DT, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DT)


class Publication(Base):
    __tablename__ = "publications"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(Unicode(10))
    owner_id: Mapped[str] = mapped_column(Unicode(20), index=True)
    dataset_id: Mapped[str | None] = mapped_column(Unicode(30))
    doi: Mapped[str | None] = mapped_column(Unicode(200), index=True)
    openalex_id: Mapped[str | None] = mapped_column(Unicode(60), index=True)
    title: Mapped[str] = mapped_column(UnicodeText)
    # SQL Server cannot index NVARCHAR(MAX) columns, so this index exists on SQLite/PostgreSQL only
    title_norm: Mapped[str] = mapped_column(UnicodeText, index=not DATABASE_URL.startswith("mssql"))
    venue: Mapped[str | None] = mapped_column(Unicode(500))
    year: Mapped[int | None] = mapped_column(Integer)
    authors: Mapped[str | None] = mapped_column(UnicodeText)
    abstract: Mapped[str | None] = mapped_column(UnicodeText)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    url: Mapped[str | None] = mapped_column(Unicode(2000))
    source: Mapped[str] = mapped_column(Unicode(20))  # dataset / openalex / crossref / manual
    status: Mapped[str] = mapped_column(Unicode(10), default="approved")
    reason: Mapped[str | None] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(DT, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DT)


class Award(Base):
    """Grants already received by faculty (from the dataset). Evidence for matching."""
    __tablename__ = "awards"
    id: Mapped[str] = mapped_column(Unicode(30), primary_key=True)
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id"), index=True)
    title: Mapped[str] = mapped_column(UnicodeText)
    funder: Mapped[str | None] = mapped_column(Unicode(300))
    amount_text: Mapped[str | None] = mapped_column(Unicode(100))
    year: Mapped[str | None] = mapped_column(Unicode(20))
    status: Mapped[str | None] = mapped_column(Unicode(50))
    description: Mapped[str | None] = mapped_column(UnicodeText)
    source_url: Mapped[str | None] = mapped_column(Unicode(500))


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(Unicode(30), primary_key=True)
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id"), index=True)
    title: Mapped[str] = mapped_column(UnicodeText)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    source_url: Mapped[str | None] = mapped_column(Unicode(500))


class GrantSource(Base):
    __tablename__ = "grant_sources"
    key: Mapped[str] = mapped_column(Unicode(50), primary_key=True)
    name: Mapped[str] = mapped_column(Unicode(200))
    url: Mapped[str] = mapped_column(Unicode(500))
    method: Mapped[str] = mapped_column(Unicode(20))  # api / scraper / monitored / manual
    scope: Mapped[str] = mapped_column(Unicode(20))   # UAE / international
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DT)
    last_success_at: Mapped[datetime | None] = mapped_column(DT)
    last_status: Mapped[str | None] = mapped_column(Unicode(20))  # ok / error / monitored
    last_error: Mapped[str | None] = mapped_column(UnicodeText)
    records_count: Mapped[int] = mapped_column(Integer, default=0)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(Unicode(50), index=True)
    trigger: Mapped[str] = mapped_column(Unicode(20), default="scheduled")
    started_at: Mapped[datetime] = mapped_column(DT, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DT)
    status: Mapped[str] = mapped_column(Unicode(20), default="running")
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(UnicodeText)


class Grant(Base):
    __tablename__ = "grants"
    __table_args__ = (UniqueConstraint("source", "source_record_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Unicode(50), index=True)
    source_record_id: Mapped[str] = mapped_column(Unicode(200))
    title: Mapped[str] = mapped_column(UnicodeText)
    funder: Mapped[str | None] = mapped_column(Unicode(300))
    programme: Mapped[str | None] = mapped_column(Unicode(300))
    type: Mapped[str] = mapped_column(Unicode(20), default="grant")  # grant/fellowship/scholarship/award/challenge
    scope: Mapped[str] = mapped_column(Unicode(20))  # UAE / international
    country: Mapped[str | None] = mapped_column(Unicode(100))
    emirate: Mapped[str | None] = mapped_column(Unicode(50))
    description: Mapped[str | None] = mapped_column(UnicodeText)
    research_areas: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    eligibility_text: Mapped[str | None] = mapped_column(UnicodeText)
    eligibility_flags: Mapped[dict] = mapped_column(JSON, default=dict)  # emirate, nationality, career_stage, applicant_types
    eligibility_warnings: Mapped[list] = mapped_column(JSON, default=list)
    amount_min: Mapped[float | None] = mapped_column(Float)
    amount_max: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(Unicode(10))
    amount_text: Mapped[str | None] = mapped_column(Unicode(300))
    duration: Mapped[str | None] = mapped_column(UnicodeText)
    open_date: Mapped[date | None] = mapped_column(Date)
    deadline: Mapped[date | None] = mapped_column(Date, index=True)  # next upcoming deadline
    deadlines: Mapped[list] = mapped_column(JSON, default=list)
    deadline_text: Mapped[str | None] = mapped_column(UnicodeText)
    source_status: Mapped[str | None] = mapped_column(Unicode(30))  # what the source says
    status: Mapped[str] = mapped_column(Unicode(20), default="open", index=True)  # open / forthcoming / closed
    apply_url: Mapped[str | None] = mapped_column(Unicode(800))
    required_documents: Mapped[str | None] = mapped_column(UnicodeText)
    contact: Mapped[str | None] = mapped_column(Unicode(500))
    source_url: Mapped[str | None] = mapped_column(Unicode(800))
    first_seen: Mapped[datetime] = mapped_column(DT, default=now)
    last_checked: Mapped[datetime] = mapped_column(DT, default=now)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str | None] = mapped_column(Unicode(64))
    raw: Mapped[dict | None] = mapped_column(JSON)


class Embedding(Base):
    """Cached vectors keyed by text hash so only changed text is re-embedded."""
    __tablename__ = "embeddings"
    text_hash: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    model: Mapped[str] = mapped_column(Unicode(100))
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Match(Base):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    grant_id: Mapped[int] = mapped_column(ForeignKey("grants.id", ondelete="CASCADE"), index=True)
    person_type: Mapped[str] = mapped_column(Unicode(10))
    person_id: Mapped[str] = mapped_column(Unicode(20), index=True)
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DT, default=now)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", "grant_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(Unicode(10))
    owner_id: Mapped[str] = mapped_column(Unicode(20), index=True)
    grant_id: Mapped[int] = mapped_column(ForeignKey("grants.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(UnicodeText)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=now)


class Request(Base):
    """Approval queue for student submissions."""
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Unicode(20))  # interest / publication
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    target_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Unicode(10), default="pending")
    reason: Mapped[str | None] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(DT, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DT)
    decided_by: Mapped[int | None] = mapped_column(Integer)


class ChatLog(Base):
    __tablename__ = "chat_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(UnicodeText)
    answer: Mapped[str] = mapped_column(UnicodeText)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str | None] = mapped_column(Unicode(100))
    created_at: Mapped[datetime] = mapped_column(DT, default=now)
