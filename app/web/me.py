from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import require
from app.db import get_db
from app.models import Faculty, Grant, Interest, Notification, Publication, Request as ApprovalRequest, Student
from app.openalex import lookup_publication
from app.util import clean_doi, norm_text, norm_name_tokens, openalex_work_id
from app.web.browse import faculty_context, reverse_matches
from app.web.common import owner_of, render, set_flash
from app.web.public import add_student_interest


def refresh_all_matches_async():
    from app.web.admin import refresh_all_matches_async as run  # avoid a circular import at load time
    run()

router = APIRouter()


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "student", "faculty")
    if user.role == "faculty":
        f = db.get(Faculty, user.faculty_id)
        return render(request, db, user, "person.html", active="/me", own=True, **faculty_context(db, f))
    st = db.get(Student, user.student_id)
    if st.access_status != "approved":
        # Not approved yet: only the profile (name + student ID) and the access request
        last = db.scalar(select(ApprovalRequest).where(ApprovalRequest.kind == "access",
                                                       ApprovalRequest.student_id == st.id)
                         .order_by(ApprovalRequest.created_at.desc()))
        return render(request, db, user, "student_pending.html", active="/me", st=st, last=last)
    interests = list(db.scalars(select(Interest).where(Interest.owner_type == "student",
                                                       Interest.owner_id == str(st.id)).order_by(Interest.created_at)))
    pubs = list(db.scalars(select(Publication).where(Publication.owner_type == "student",
                                                     Publication.owner_id == str(st.id)).order_by(Publication.created_at.desc())))
    return render(request, db, user, "student_me.html", active="/me", st=st, interests=interests, pubs=pubs,
                  matched=reverse_matches(db, "student", str(st.id)), lookup=request.session.pop("lookup", None))


@router.post("/me/request-access")
def request_access(request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "student")
    st = db.get(Student, user.student_id)
    if st.access_status == "approved":
        return RedirectResponse("/", 303)
    pending = db.scalar(select(ApprovalRequest).where(ApprovalRequest.kind == "access", ApprovalRequest.student_id == st.id,
                                                      ApprovalRequest.status == "pending"))
    if pending is None:
        db.add(ApprovalRequest(kind="access", student_id=st.id, target_id=st.id))
    st.access_status = "requested"
    db.commit()
    set_flash(request, "Access requested. An administrator will review it.")
    return RedirectResponse("/me", 303)


@router.post("/me/interests")
def add_interest(request: Request, text: str = Form(...), db: Session = Depends(get_db)):
    user = require(request, db, "student", "faculty")
    if user.role == "faculty":
        # Faculty interests go live immediately (no approval); an admin can remove them later.
        added = 0
        for t in text.split("|"):
            t = t.strip()[:120]
            tn = norm_text(t)
            if tn and not db.scalar(select(Interest).where(Interest.owner_type == "faculty",
                                                          Interest.owner_id == user.faculty_id, Interest.text_norm == tn)):
                db.add(Interest(owner_type="faculty", owner_id=user.faculty_id, text=t, text_norm=tn,
                                source="faculty", status="approved", decided_at=datetime.utcnow()))
                added += 1
        db.commit()
        if added:
            refresh_all_matches_async()
        set_flash(request, f"{added} interest(s) added to your profile. Your grant matches are being updated."
                  if added else "That interest is already on your profile.", "ok" if added else "error")
        return RedirectResponse("/me", 303)
    st = db.get(Student, user.student_id)
    added = 0
    for t in text.split("|"):
        if add_student_interest(db, st, t):
            added += 1
    db.commit()
    set_flash(request, f"{added} interest(s) sent for approval." if added else "That interest is already on your profile.",
              "ok" if added else "error")
    return RedirectResponse("/me", 303)


@router.post("/me/interests/{iid}/delete")
def delete_interest(iid: int, request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "student", "faculty")
    i = db.get(Interest, iid)
    if user.role == "faculty":
        # faculty can remove any interest on their own profile, no approval needed
        if i and i.owner_type == "faculty" and i.owner_id == user.faculty_id:
            db.delete(i)
            db.commit()
            refresh_all_matches_async()
            set_flash(request, "Interest removed. Your grant matches are being updated.")
        return RedirectResponse("/me", 303)
    if i and i.owner_type == "student" and i.owner_id == str(user.student_id):
        for r in db.scalars(select(ApprovalRequest).where(ApprovalRequest.kind == "interest", ApprovalRequest.target_id == iid)):
            db.delete(r)
        was_approved = i.status == "approved"
        db.delete(i)
        db.commit()
        if was_approved:
            from app.web.admin import refresh_student_matches
            refresh_student_matches(db)
    return RedirectResponse("/me", 303)


@router.post("/me/publications/lookup")
def lookup(request: Request, identifier: str = Form(...), db: Session = Depends(get_db)):
    user = require(request, db, "student")
    rec, source = lookup_publication(identifier)
    if rec is None:
        set_flash(request, source, "error")
        return RedirectResponse("/me#publications", 303)
    st = db.get(Student, user.student_id)
    ours = set(norm_name_tokens(st.full_name))
    rec["name_in_authors"] = bool(ours and any(len(ours & set(norm_name_tokens(a))) >= min(2, len(ours))
                                                for a in (rec.get("authors") or "").split(";")))
    rec["lookup_source"] = source
    request.session["lookup"] = rec
    return RedirectResponse("/me#publications", 303)


@router.post("/me/publications")
def submit_publication(request: Request, doi: str = Form(""), openalex_id: str = Form(""), db: Session = Depends(get_db)):
    """Submit the looked-up publication. Metadata is taken from the lookup, never from free text."""
    user = require(request, db, "student")
    ident = clean_doi(doi) or openalex_work_id(openalex_id)
    if not ident:
        set_flash(request, "A publication needs a valid DOI or OpenAlex ID as proof.", "error")
        return RedirectResponse("/me#publications", 303)
    rec, source = lookup_publication(ident)
    if rec is None:
        set_flash(request, source, "error")
        return RedirectResponse("/me#publications", 303)
    sid = str(user.student_id)
    dup = db.scalar(select(Publication).where(Publication.owner_type == "student", Publication.owner_id == sid,
                                              ((Publication.doi == rec["doi"]) if rec["doi"] else
                                               (Publication.openalex_id == rec["openalex_id"]))))
    if dup:
        set_flash(request, "You already submitted this publication.", "error")
        return RedirectResponse("/me#publications", 303)
    p = Publication(owner_type="student", owner_id=sid, title_norm=norm_text(rec["title"] or ""), source=source,
                    status="pending", **{k: rec[k] for k in ("openalex_id", "doi", "title", "venue", "year", "authors",
                                                             "abstract", "keywords", "url")})
    db.add(p)
    db.flush()
    db.add(ApprovalRequest(kind="publication", student_id=user.student_id, target_id=p.id))
    db.commit()
    set_flash(request, "Publication submitted. An administrator will review it.")
    return RedirectResponse("/me#publications", 303)


@router.get("/notifications")
def notifications(request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "student", "faculty")
    ot, oid = owner_of(user)
    rows = list(db.scalars(select(Notification).where(Notification.owner_type == ot, Notification.owner_id == oid)
                           .order_by(Notification.read, Notification.created_at.desc(), Notification.score.desc()).limit(200)))
    grants = {g.id: g for g in db.scalars(select(Grant).where(Grant.id.in_([n.grant_id for n in rows])))}
    return render(request, db, user, "notifications.html", active="/notifications", rows=rows, grants=grants)


@router.post("/notifications/read")
def mark_read(request: Request, nid: int | None = Form(None), db: Session = Depends(get_db)):
    user = require(request, db, "student", "faculty")
    ot, oid = owner_of(user)
    q = update(Notification).where(Notification.owner_type == ot, Notification.owner_id == oid)
    if nid:
        q = q.where(Notification.id == nid)
    db.execute(q.values(read=True))
    db.commit()
    return RedirectResponse("/notifications", 303)


@router.get("/notifications/{nid}")
def open_notification(nid: int, request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "student", "faculty")
    ot, oid = owner_of(user)
    n = db.get(Notification, nid)
    if n and n.owner_type == ot and n.owner_id == oid:
        n.read = True
        db.commit()
        return RedirectResponse(f"/grants/{n.grant_id}", 303)
    return RedirectResponse("/notifications", 303)


@router.get("/api/notifications/count")
def notif_count_api(request: Request, db: Session = Depends(get_db)):
    from app.web.common import notif_count
    return JSONResponse({"count": notif_count(db, require(request, db))})
