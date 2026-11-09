from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, or_, select, true
from sqlalchemy.orm import Session

from app.auth import require
from app.db import get_db
from app.models import Award, Faculty, Grant, GrantSource, Interest, Match, Project, Publication, Student
from app.services.grants import STATUSES, TYPES, filter_grants, source_names
from app.services import clusters as cluster_service
from app.services.matching import engine
from app.web.common import render

router = APIRouter()


@router.get("/grants")
def grants(request: Request, page: int = 1, db: Session = Depends(get_db)):
    user = require(request, db)
    args = dict(request.query_params)
    rows, total = filter_grants(db, args, page=max(page, 1))
    sources = list(db.scalars(select(GrantSource).where(GrantSource.records_count > 0).order_by(GrantSource.name)))
    top = {}
    if rows:
        for m in db.scalars(select(Match).where(Match.grant_id.in_([g.id for g in rows]), Match.person_type == "faculty",
                                                Match.rank == 1)):
            top[m.grant_id] = m
    qs = "&".join(f"{k}={v}" for k, v in args.items() if k != "page" and v)
    return render(request, db, user, "grants.html", active="/grants", rows=rows, total=total, page=page, args=args,
                  types=TYPES, statuses=STATUSES, sources=sources, source_names=source_names(db), top=top, qs=qs,
                  pages=(total + 24) // 25)


@router.get("/grants/{gid}")
def grant_detail(gid: int, request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    g = db.get(Grant, gid)
    if g is None or (g.hidden and user.role != "admin"):
        raise HTTPException(404, "Grant not found")
    matches = list(db.scalars(select(Match).where(Match.grant_id == gid).order_by(Match.person_type, Match.rank)))
    fac_ids = [m.person_id for m in matches if m.person_type == "faculty"]
    stu_ids = [int(m.person_id) for m in matches if m.person_type == "student"]
    people = {("faculty", f.id): f for f in db.scalars(select(Faculty).where(Faculty.id.in_(fac_ids)))}
    people.update({("student", str(s.id)): s for s in db.scalars(select(Student).where(Student.id.in_(stu_ids)))})
    fac = [m for m in matches if m.person_type == "faculty"]
    stu = [m for m in matches if m.person_type == "student"]
    # topic group: how many faculty across schools share this grant's area (from the cached matches)
    schools = Counter(people[("faculty", m.person_id)].school for m in fac if ("faculty", m.person_id) in people)
    src = db.get(GrantSource, g.source)
    return render(request, db, user, "grant_detail.html", active="/grants", grant=g, fac=fac, stu=stu, people=people,
                  schools=schools, src=src, gclusters=cluster_service.for_grant(db, g.id) if not g.hidden else [])


@router.get("/researchers")
def researchers(request: Request, q: str = "", school: str = "", db: Session = Depends(get_db)):
    user = require(request, db)
    query = select(Faculty).where(Faculty.visible == true()).order_by(Faculty.school, Faculty.full_name)
    if school:
        query = query.where(Faculty.school == school)
    rows = list(db.scalars(query))
    interests = {}
    for i in db.scalars(select(Interest).where(Interest.owner_type == "faculty", Interest.status == "approved")
                          .order_by(Interest.owner_id, Interest.text_norm)):
        interests.setdefault(i.owner_id, []).append(i.text)
    ranked = None
    if q.strip():
        ranked = []
        for (ptype, pid), score, _ in engine.score_text(db, q)[:30]:
            ranked.append((ptype, pid, score))
    students = {str(s.id): s for s in db.scalars(select(Student))}
    schools = sorted({f.school for f in rows if f.school})
    return render(request, db, user, "researchers.html", active="/researchers", rows=rows, interests=interests, q=q,
                  ranked=ranked, faculty={f.id: f for f in db.scalars(select(Faculty))}, students=students,
                  school=school, schools=schools)


@router.get("/faculty/{fid}")
def faculty_profile(fid: str, request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    f = db.get(Faculty, fid)
    if f is None or not f.visible:
        raise HTTPException(404)
    return render(request, db, user, "person.html", active="/researchers", **faculty_context(db, f))


def faculty_context(db: Session, f: Faculty):
    interests = list(db.scalars(select(Interest).where(Interest.owner_type == "faculty", Interest.owner_id == f.id,
                                                       Interest.status == "approved").order_by(Interest.text_norm)))
    pubs = list(db.scalars(select(Publication).where(Publication.owner_type == "faculty", Publication.owner_id == f.id,
                                                     Publication.status == "approved")
                           .order_by(case((Publication.year.is_(None), 1), else_=0), Publication.year.desc(), Publication.id)))
    awards = list(db.scalars(select(Award).where(Award.faculty_id == f.id).order_by(Award.id)))
    projects = list(db.scalars(select(Project).where(Project.faculty_id == f.id).order_by(Project.id)))
    matched = reverse_matches(db, "faculty", f.id)
    return dict(kind="faculty", person=f, interests=interests, pubs=pubs, awards=awards, projects=projects,
                matched=matched, pclusters=cluster_service.for_person(db, "faculty", f.id))


def reverse_matches(db: Session, ptype: str, pid: str, limit: int = 12):
    out = []
    for gid, score, ev in engine.grants_for_person(db, ptype, pid, limit=limit):
        g = db.get(Grant, gid)
        if g and not g.hidden and g.status != "closed":
            out.append((g, score, ev))
        if len(out) >= limit:
            break
    return out


@router.get("/students/{sid}")
def student_profile(sid: int, request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    s = db.get(Student, sid)
    if s is None or not s.visible:
        raise HTTPException(404)
    # only approved research evidence is shown; email and pending items stay private
    interests = list(db.scalars(select(Interest).where(Interest.owner_type == "student", Interest.owner_id == str(sid),
                                                       Interest.status == "approved").order_by(Interest.text_norm)))
    pubs = list(db.scalars(select(Publication).where(Publication.owner_type == "student", Publication.owner_id == str(sid),
                                                     Publication.status == "approved").order_by(Publication.id)))
    return render(request, db, user, "person.html", active="/researchers", kind="student", person=s,
                  interests=interests, pubs=pubs, awards=[], projects=[],
                  matched=reverse_matches(db, "student", str(sid)),
                  pclusters=cluster_service.for_person(db, "student", str(sid)))


@router.get("/clusters")
def clusters(request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    return render(request, db, user, "clusters.html", active="/clusters", groups=cluster_service.overview(db))


@router.get("/clusters/{key}")
def cluster_page(key: str, request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    c = cluster_service.detail(db, key)
    if c is None:
        raise HTTPException(404)
    grants = []
    for gid, sim in c["grant_ids"]:
        g = db.get(Grant, gid)
        if g and not g.hidden and g.status != "closed":
            grants.append((g, sim))
        if len(grants) >= 15:
            break
    return render(request, db, user, "cluster.html", active="/clusters", c=c, grants=grants,
                  source_names=source_names(db))


@router.get("/api/interest-suggestions")
def interest_suggestions(request: Request, q: str = "", db: Session = Depends(get_db)):
    require(request, db)
    q = q.strip()
    if len(q) < 2:
        return JSONResponse([])
    like = f"%{q}%"
    rows = db.execute(select(func.min(Interest.text), func.count()).where(Interest.status == "approved", Interest.text.ilike(like))
                      .group_by(func.lower(Interest.text)).order_by(func.count().desc()).limit(10)).all()
    out = [r[0] for r in rows]
    for f in db.scalars(select(Faculty).where(Faculty.openalex_topics.cast(Interest.text.type).ilike(like)).limit(30)):
        for t in f.openalex_topics or []:
            if q.lower() in t.lower() and t not in out:
                out.append(t)
    return JSONResponse(out[:12])
