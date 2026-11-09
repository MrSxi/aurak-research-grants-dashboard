import threading
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import false, func, inspect, select, true
from sqlalchemy.orm import Session

from app.auth import require
from app.db import get_db
from app.models import (Award, ChatLog, Faculty, Grant, GrantSource, IngestionRun, Interest, Match, Notification,
                        Publication, Request as ApprovalRequest, Student, User)
from app.openalex import lookup_publication
from app.services import refresh
from app.services.grants import TYPES, filter_grants
from app.services.matching import engine
from app.util import compute_status, norm_text, parse_date
from app.web.common import render, set_flash

router = APIRouter(prefix="/admin")
_match_lock = threading.Lock()
# What each approval request points at: an interest, a publication, or the student asking for dashboard access
REQUEST_TARGETS = {"interest": Interest, "publication": Publication, "access": Student}


def refresh_student_matches(db: Session):
    """Approved student evidence is used for matching immediately."""
    with _match_lock:
        engine.rebuild(db)
        engine.match_grants(db, person_type="student")


def refresh_all_matches_async():
    def run():
        import logging
        import time

        from sqlalchemy.exc import OperationalError

        from app.db import SessionLocal
        # A running refresh holds the database and rebuilds matches itself at the end: wait for it first.
        waited = 0
        while refresh.state.get("running") and waited < 1800:
            time.sleep(5)
            waited += 5
        for attempt in range(4):
            db = SessionLocal()
            try:
                with _match_lock:
                    engine.rebuild(db)
                    engine.match_grants(db)
                return
            except OperationalError as e:  # database briefly locked by another writer: retry
                db.rollback()
                logging.getLogger("matching").warning("Match update attempt %s failed (%s), retrying", attempt + 1, e)
                time.sleep(10 * (attempt + 1))
            finally:
                db.close()
    threading.Thread(target=run, daemon=True).start()


@router.get("")
def overview(request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    c = lambda m, *w: db.scalar(select(func.count()).select_from(m).where(*w)) or 0  # noqa: E731
    stats = {
        "faculty": c(Faculty), "students": c(Student, Student.source == "signup"), "publications": c(Publication),
        "grants": c(Grant, Grant.hidden == false()), "active": c(Grant, Grant.status.in_(["open", "forthcoming", "unknown"]),
                                                                Grant.hidden == false()),
        "pending": c(ApprovalRequest, ApprovalRequest.status == "pending"), "matches": c(Match),
        "notifications": c(Notification), "openalex": c(Faculty, Faculty.openalex_id.is_not(None)),
    }
    runs = list(db.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(12)))
    return render(request, db, user, "admin/overview.html", active="/admin", stats=stats, runs=runs, state=refresh.state)


# ---------------- approval queue ----------------
@router.get("/queue")
def queue(request: Request, show: str = "pending", db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    q = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
    if show != "all":
        q = q.where(ApprovalRequest.status == "pending")
    reqs = list(db.scalars(q.limit(300)))
    items = {}
    for r in reqs:
        items[r.id] = db.get(REQUEST_TARGETS[r.kind], r.target_id)
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_({r.student_id for r in reqs})))}
    # Interests faculty added themselves: live without approval, but an admin can remove them
    fac_interests = list(db.scalars(select(Interest).where(Interest.owner_type == "faculty", Interest.source == "faculty")
                                    .order_by(Interest.created_at.desc()).limit(200)))
    fac_names = {f.id: f.full_name for f in db.scalars(select(Faculty).where(
        Faculty.id.in_({i.owner_id for i in fac_interests})))}
    return render(request, db, user, "admin/queue.html", active="/admin/queue", reqs=reqs, items=items,
                  students=students, show=show, fac_interests=fac_interests, fac_names=fac_names)


@router.post("/interests/{iid}/delete")
def remove_faculty_interest(iid: int, request: Request, db: Session = Depends(get_db)):
    require(request, db, "admin")
    i = db.get(Interest, iid)
    if i and i.owner_type == "faculty":
        db.delete(i)
        db.commit()
        refresh_all_matches_async()
        set_flash(request, f"Removed “{i.text}”. Grant matches are being recomputed in the background.")
    return RedirectResponse(request.headers.get("referer") or "/admin/queue", 303)


@router.post("/queue/decide")
async def decide(request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    form = await request.form()
    ids = [int(x) for x in form.getlist("ids")]
    action = form.get("action", "")
    if action.startswith("approve:") or action.startswith("deny:"):  # single-row buttons
        action, one = action.split(":")
        ids = [int(one)]
    if action not in ("approve", "deny") or not ids:
        set_flash(request, "Select at least one request.", "error")
        return RedirectResponse("/admin/queue", 303)
    reason = (form.get(f"reason_{ids[0]}") if len(ids) == 1 else None) or form.get("reason") or None
    n, evidence_changed = 0, False
    for r in db.scalars(select(ApprovalRequest).where(ApprovalRequest.id.in_(ids), ApprovalRequest.status == "pending")):
        target = db.get(REQUEST_TARGETS[r.kind], r.target_id)
        status = "approved" if action == "approve" else "denied"
        r.status, r.reason, r.decided_at, r.decided_by = status, reason, datetime.utcnow(), user.id
        if target is not None and r.kind == "access":
            target.access_status = status  # dashboard access for the student
        elif target is not None:
            target.status, target.reason, target.decided_at = status, reason, datetime.utcnow()
            evidence_changed = True
        n += 1
    db.commit()
    if action == "approve" and evidence_changed:
        refresh_student_matches(db)
    set_flash(request, f"{n} request(s) {'approved and added to the database; matches updated' if action == 'approve' else 'denied'}.")
    return RedirectResponse("/admin/queue", 303)


# ---------------- manual publication ----------------
@router.get("/publications/new")
def pub_form(request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    faculty = list(db.scalars(select(Faculty).order_by(Faculty.full_name)))
    students = list(db.scalars(select(Student).where(Student.source == "signup").order_by(Student.full_name)))
    recent = list(db.scalars(select(Publication).where(Publication.source == "manual").order_by(Publication.id.desc()).limit(10)))
    return render(request, db, user, "admin/pub_new.html", active="/admin/publications/new", faculty=faculty,
                  students=students, lookup=request.session.pop("admin_lookup", None), recent=recent)


@router.post("/publications/lookup")
def pub_lookup(request: Request, owner: str = Form(...), identifier: str = Form(...), db: Session = Depends(get_db)):
    require(request, db, "admin")
    rec, source = lookup_publication(identifier)
    if rec is None:
        set_flash(request, source, "error")
    else:
        rec.update(owner=owner, lookup_source=source)
        request.session["admin_lookup"] = rec
    return RedirectResponse("/admin/publications/new", 303)


@router.post("/publications")
def pub_add(request: Request, owner: str = Form(...), title: str = Form(...), doi: str = Form(""),
            openalex_id: str = Form(""), venue: str = Form(""), year: str = Form(""), authors: str = Form(""),
            url: str = Form(""), abstract: str = Form(""), db: Session = Depends(get_db)):
    require(request, db, "admin")
    otype, oid = owner.split(":", 1)
    if otype not in ("faculty", "student") or not title.strip():
        raise HTTPException(400)
    p = Publication(owner_type=otype, owner_id=oid, title=title.strip(), title_norm=norm_text(title), doi=doi or None,
                    openalex_id=openalex_id or None, venue=venue or None, year=int(year) if year.isdigit() else None,
                    authors=authors or None, url=url or None, abstract=abstract or None, keywords=[], source="manual",
                    status="approved", decided_at=datetime.utcnow())
    db.add(p)
    db.commit()
    refresh_all_matches_async()
    set_flash(request, "Publication added to the database. Matches are being recomputed in the background.")
    return RedirectResponse("/admin/publications/new", 303)


# ---------------- grants management ----------------
@router.get("/grants")
def grants_admin(request: Request, page: int = 1, db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    args = dict(request.query_params)
    args.setdefault("status", "all")
    if args.get("hidden_only"):
        rows = list(db.scalars(select(Grant).where(Grant.hidden == true()).order_by(Grant.id.desc())))
        total = len(rows)
    else:
        rows, total = filter_grants(db, args, include_hidden=True, page=page, per_page=40)
    qs = "&".join(f"{k}={v}" for k, v in args.items() if k != "page" and v)
    return render(request, db, user, "admin/grants.html", active="/admin/grants", rows=rows, total=total, args=args,
                  page=page, pages=(total + 39) // 40, qs=qs)


@router.get("/grants/new")
def grant_new(request: Request, db: Session = Depends(get_db)):
    require(request, db, "admin")
    # Project requirement: no manual grant entry. Every grant comes from an automatic source.
    set_flash(request, "Grants are not entered by hand: every grant is read automatically from its funder's source.",
              "error")
    return RedirectResponse("/admin/grants", 303)


@router.get("/grants/{gid}/edit")
def grant_edit(gid: int, request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    return render(request, db, user, "admin/grant_form.html", active="/admin/grants", g=db.get(Grant, gid), types=TYPES)


@router.post("/grants/save")
async def grant_save(request: Request, db: Session = Depends(get_db)):
    require(request, db, "admin")
    f = await request.form()
    gid = f.get("id")
    g = db.get(Grant, int(gid)) if gid else None
    is_new = g is None
    if is_new:  # no manual grant entry (project requirement); admins can only correct or hide fetched grants
        set_flash(request, "Grants are not entered by hand: every grant is read automatically.", "error")
        return RedirectResponse("/admin/grants", 303)
    if is_new:
        g = Grant(source="manual", source_record_id=f"manual-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
                  first_seen=datetime.utcnow())
        db.add(g)
    if not (f.get("title") or "").strip() or not (f.get("source_url") or "").strip():
        set_flash(request, "Title and the funder's official page URL are required (no unsourced grants).", "error")
        return RedirectResponse(f"/admin/grants/{gid}/edit" if gid else "/admin/grants/new", 303)

    def num(k):
        try:
            return float(str(f.get(k)).replace(",", "")) if f.get(k) else None
        except ValueError:
            return None
    for k in ("title", "funder", "programme", "type", "scope", "country", "emirate", "description", "eligibility_text",
              "currency", "amount_text", "duration", "apply_url", "required_documents", "contact", "source_url",
              "deadline_text"):
        setattr(g, k, (f.get(k) or "").strip() or None)
    g.scope = g.scope or "UAE"
    g.type = g.type or "grant"
    g.amount_min, g.amount_max = num("amount_min"), num("amount_max")
    g.open_date, g.deadline = parse_date(f.get("open_date")), parse_date(f.get("deadline"))
    g.deadlines = [g.deadline.isoformat()] if g.deadline else []
    g.research_areas = [x.strip() for x in (f.get("research_areas") or "").split(",") if x.strip()]
    g.keywords = [x.strip() for x in (f.get("keywords") or "").split(",") if x.strip()]
    g.eligibility_warnings = [x.strip() for x in (f.get("eligibility_warnings") or "").splitlines() if x.strip()]
    g.eligibility_flags = {k: f.get(f"flag_{k}") for k in ("emirate", "nationality", "career_stage", "applicant_type")
                           if f.get(f"flag_{k}")}
    g.source_status = None
    g.status = compute_status(g.open_date, g.deadline)
    g.manual = True
    g.last_checked = datetime.utcnow()
    db.commit()
    with _match_lock:
        engine.rebuild(db)
        engine.match_grants(db, [g.id])
    if is_new:
        refresh.notify(db, [g.id])
    set_flash(request, "Grant saved, matched and (if new) matching faculty were notified.")
    return RedirectResponse(f"/grants/{g.id}", 303)


@router.post("/grants/{gid}/hide")
def grant_hide(gid: int, request: Request, db: Session = Depends(get_db)):
    require(request, db, "admin")
    g = db.get(Grant, gid)
    g.hidden = not g.hidden
    db.commit()
    with _match_lock:
        engine.rebuild(db)
    set_flash(request, f"Grant {'hidden from' if g.hidden else 'shown on'} the dashboard.")
    return RedirectResponse(request.headers.get("referer") or "/admin/grants", 303)


@router.post("/refresh")
def refresh_now(request: Request, db: Session = Depends(get_db)):
    require(request, db, "admin")
    if refresh.state.get("running"):
        set_flash(request, "A refresh is already running.", "error")
    else:
        refresh.refresh_in_background("manual")
        set_flash(request, "Refresh started: grant sources, OpenAlex publications, matches and notifications.")
    return RedirectResponse(request.headers.get("referer") or "/admin/sources", 303)


@router.get("/sources")
def sources(request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    refresh.sync_sources(db)
    rows = list(db.scalars(select(GrantSource).order_by(GrantSource.method, GrantSource.name)))
    runs = list(db.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(40)))
    from app.main import scheduler
    job = scheduler.get_job("refresh") if scheduler else None
    return render(request, db, user, "admin/sources.html", active="/admin/sources", rows=rows, runs=runs,
                  state=refresh.state, next_run=job.next_run_time if job else None)


# ---------------- read-only database view ----------------
TABLES = {"faculty": Faculty, "students": Student, "publications": Publication, "interests": Interest,
          "grants": Grant, "requests": ApprovalRequest, "awards": Award, "matches": Match,
          "notifications": Notification, "grant_sources": GrantSource, "ingestion_runs": IngestionRun,
          "users": User, "chat_logs": ChatLog}
HIDDEN_COLS = {"password_hash", "raw", "content_hash", "text_norm", "title_norm"}


@router.get("/db")
def db_view(request: Request, table: str = "faculty", page: int = 1, q: str = "", db: Session = Depends(get_db)):
    user = require(request, db, "admin")
    model = TABLES.get(table, Faculty)
    cols = [c.key for c in inspect(model).columns if c.key not in HIDDEN_COLS]
    query = select(model)
    if q:
        text_cols = [getattr(model, c) for c in cols if str(inspect(model).columns[c].type).startswith(("VARCHAR", "TEXT"))]
        from sqlalchemy import or_
        query = query.where(or_(*[c.ilike(f"%{q}%") for c in text_cols]))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    pk = inspect(model).primary_key[0]  # SQL Server needs an ORDER BY for paging
    rows = list(db.scalars(query.order_by(pk).offset((page - 1) * 50).limit(50)))
    counts = {k: db.scalar(select(func.count()).select_from(m)) for k, m in TABLES.items()}
    return render(request, db, user, "admin/db.html", active="/admin/db", tables=TABLES, table=table, cols=cols,
                  rows=rows, total=total, page=page, pages=(total + 49) // 50, q=q, counts=counts)
