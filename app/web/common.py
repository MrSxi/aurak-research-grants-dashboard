import json
from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from app import config, images
from app.auth import student_has_access
from app.config import ROOT
from app.models import Faculty, Notification, Request as ApprovalRequest, Student, User
from app.services.matching import COMPONENTS, label as match_label
from app.web import viewmodel

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))

NAV = {
    None: [("/login", "Log in"), ("/signup", "Student sign up")],
    # Notifications / Approvals live only in the gold quick-action bar (with the unread count), not in this menu.
    "student": [("/", "Dashboard"), ("/grants", "Grants"), ("/researchers", "Researchers"),
                ("/me", "My Interests & Publications"), ("/clusters", "Research Clusters"), ("/chat", "Assistant")],
    "student_pending": [("/me", "My Profile")],  # dashboard access not approved yet
    "faculty": [("/", "Dashboard"), ("/grants", "Grants"),
                ("/me", "My Profile"), ("/researchers", "Researchers"), ("/clusters", "Research Clusters"),
                ("/chat", "Assistant")],
    "admin": [("/admin", "Overview"), ("/admin/grants", "Grants"),
              ("/admin/publications/new", "Add Publication"), ("/admin/sources", "Source Health"),
              ("/admin/db", "Database"), ("/grants", "Browse"), ("/chat", "Assistant")],
}


def owner_of(user: User):
    if user.role == "faculty" and user.faculty_id:
        return "faculty", user.faculty_id
    if user.role == "student" and user.student_id:
        return "student", str(user.student_id)
    return None, None


def notif_count(db: Session, user: User | None) -> int:
    if not user:
        return 0
    if user.role == "admin":
        return db.scalar(select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == "pending")) or 0
    ot, oid = owner_of(user)
    if not ot:
        return 0
    return db.scalar(select(func.count()).select_from(Notification).where(
        Notification.owner_type == ot, Notification.owner_id == oid, Notification.read == false())) or 0


def set_flash(request: Request, text: str, kind: str = "ok"):
    request.session["flash"] = {"text": text, "kind": "error" if kind == "error" else ""}


def render(request: Request, db: Session, user: User | None, name: str, active: str | None = None, **ctx) -> HTMLResponse:
    flash = request.session.pop("flash", None)
    limited = user is not None and not student_has_access(db, user)
    nav_key = "student_pending" if limited else (user.role if user else None)
    ctx.update(request=request, user=user, nav=NAV[nav_key], limited=limited, active=active or request.url.path,
               notif_count=0 if limited else notif_count(db, user), flash=flash, refresh_hours=config.REFRESH_HOURS)
    if viewmodel.wants_json(request, name):  # the ASP.NET frontend renders this page itself
        return viewmodel.page_response(name, ctx)
    return templates.TemplateResponse(request, name, ctx)


def person_name(db: Session, ptype: str, pid: str) -> str:
    if ptype == "faculty":
        f = db.get(Faculty, pid)
        return f.full_name if f else pid
    s = db.get(Student, int(pid)) if str(pid).isdigit() else None
    return s.full_name if s else pid


# ---------- Jinja filters ----------
def _date(d):
    return d.strftime("%d %B %Y") if d else ""


def _days_left(d):
    if not d:
        return None
    return (d - date.today()).days


def _score(s):
    return f"{round(float(s) * 100)}"


def _missing(value, text="Not published"):
    if value in (None, "", [], {}):
        return Markup(f'<span class="missing">{escape(text)}</span>')
    return value


templates.env.filters.update(
    d=_date, days_left=_days_left, score=_score, missing=_missing, match_label=match_label,
    tojson_pretty=lambda v: json.dumps(v, indent=1, default=str, ensure_ascii=False),
)
templates.env.globals.update(COMPONENTS={k: v[0] for k, v in COMPONENTS.items()}, config=config, today=date.today,
                             CLUSTER_IMAGES=images.CLUSTER_IMAGES, PAGE_IMAGES=images.PAGE_IMAGES,
                             grant_image=images.grant_image, image_allowed=images.image_allowed)
