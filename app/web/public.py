from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import case, false, func, select, true
from sqlalchemy.orm import Session

from app import config
from app.auth import current_user, hash_password, require, student_has_access, verify_password
from app.db import get_db
from app.models import Faculty, Grant, Interest, Request as ApprovalRequest, Student, User
from app.services.matching import engine
from app.util import norm_text
from app.web.common import owner_of, render, set_flash

router = APIRouter()
SCHOOLS = ["School of Engineering and Computing", "School of Business", "School of Arts & Sciences"]


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    return render(request, db, current_user(request, db), "login.html", demo=config)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    login_id = email.strip()
    user = db.scalar(select(User).where(func.lower(User.email) == login_id.lower()))
    if user is None and login_id.isdigit():  # students can also log in with their AURAK student ID
        st = db.scalar(select(Student).where(Student.student_number == login_id))
        user = db.scalar(select(User).where(User.student_id == st.id)) if st else None
    if not user or not verify_password(password, user.password_hash):
        set_flash(request, "Email / student ID or password is incorrect.", "error")
        return RedirectResponse("/login", 303)
    request.session["uid"] = user.id
    if user.role == "admin":
        return RedirectResponse("/admin", 303)
    return RedirectResponse("/" if student_has_access(db, user) else "/me", 303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/signup")
def signup_page(request: Request, db: Session = Depends(get_db)):
    return render(request, db, current_user(request, db), "signup.html", schools=SCHOOLS)


@router.post("/signup")
def signup(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...),
           student_number: str = Form(...), school: str = Form(""), major: str = Form(""), interests: str = Form(""),
           db: Session = Depends(get_db)):
    email = email.strip().lower()
    student_number = student_number.strip()
    if len(password) < 6:
        set_flash(request, "Choose a password of at least 6 characters.", "error")
        return RedirectResponse("/signup", 303)
    if not student_number.isdigit() or not 6 <= len(student_number) <= 12:
        set_flash(request, f"Enter your AURAK student ID (digits only, e.g. {config.STUDENT_ID_EXAMPLE}).", "error")
        return RedirectResponse("/signup", 303)
    if db.scalar(select(User).where(func.lower(User.email) == email)) or \
            db.scalar(select(Student).where(Student.student_number == student_number)):
        set_flash(request, "An account with this email or student ID already exists. Please log in.", "error")
        return RedirectResponse("/login", 303)
    st = Student(full_name=name.strip(), email=email, student_number=student_number, access_status="none",
                 school=school or None, major=major.strip() or None)
    db.add(st)
    db.flush()
    user = User(email=email, name=name.strip(), password_hash=hash_password(password), role="student", student_id=st.id)
    db.add(user)
    for text in {t.strip() for t in interests.split("|") if t.strip()}:
        add_student_interest(db, st, text)
    db.commit()
    request.session["uid"] = user.id
    set_flash(request, "Welcome! Request full dashboard access below. An administrator will review it.")
    return RedirectResponse("/me", 303)


def add_student_interest(db: Session, st: Student, text: str):
    text = text.strip()[:120]
    tn = norm_text(text)
    if not tn:
        return None
    existing = db.scalar(select(Interest).where(Interest.owner_type == "student", Interest.owner_id == str(st.id),
                                                Interest.text_norm == tn))
    if existing:
        return None
    i = Interest(owner_type="student", owner_id=str(st.id), text=text, text_norm=tn, source="student", status="pending")
    db.add(i)
    db.flush()
    db.add(ApprovalRequest(kind="interest", student_id=st.id, target_id=i.id))
    return i


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", 303)
    if user.role == "admin":
        return RedirectResponse("/admin", 303)
    if not student_has_access(db, user):
        return RedirectResponse("/me", 303)
    active = Grant.status.in_(["open", "forthcoming", "unknown"])
    visible = Grant.hidden == false()
    count = lambda *w: db.scalar(select(func.count()).select_from(Grant).where(visible, active, *w)) or 0  # noqa: E731
    stats = {
        "active": count(),
        "uae": count(Grant.scope == "UAE"),
        "intl": count(Grant.scope == "international"),
        "fellow": count(Grant.type.in_(["fellowship", "scholarship"])),
        "closing": count(Grant.deadline.is_not(None), Grant.deadline <= date.today() + timedelta(days=30)),
        "faculty": db.scalar(select(func.count()).select_from(Faculty).where(Faculty.visible == true())),
        "funders": db.scalar(select(func.count(func.distinct(Grant.funder))).where(visible, active)),
    }
    newest = list(db.scalars(select(Grant).where(visible, active).order_by(Grant.first_seen.desc(),
                                                                            case((Grant.deadline.is_(None), 1), else_=0), Grant.deadline).limit(4)))
    uae_first = list(db.scalars(select(Grant).where(visible, active, Grant.scope == "UAE").order_by(
        Grant.first_seen.desc()).limit(1)))
    if uae_first and uae_first[0] not in newest:
        newest = uae_first + newest[:3]
    closing = list(db.scalars(select(Grant).where(visible, Grant.status == "open", Grant.deadline >= date.today())
                              .order_by(Grant.deadline).limit(2)))
    ot, oid = owner_of(user)
    mine = []
    if ot:
        for gid, score, ev in engine.grants_for_person(db, ot, oid, limit=6):
            g = db.get(Grant, gid)
            if g and g.status in ("open", "forthcoming", "unknown") and not g.hidden:
                mine.append((g, score))
            if len(mine) == 3:
                break
    return render(request, db, user, "home.html", active="/", stats=stats, newest=newest, closing=closing, mine=mine,
                  index_ready=engine.ready)


@router.get("/healthz")
def healthz():
    return {"ok": True, "index_ready": engine.ready}


def guard(request, db, *roles):
    return require(request, db, *roles)
