"""Export routes: every report is available as PDF, Excel (.xlsx) or CSV, e.g. /export/grants.pdf?scope=UAE.

Exports follow the same permissions as the pages they come from (logged in, approved students only; admin
exports for admins only) and contain only what those pages show.
"""
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, inspect, or_, select, true
from sqlalchemy.orm import Session

from app.auth import require
from app.db import get_db
from app.models import (Award, Faculty, Grant, GrantSource, IngestionRun, Interest, Match, Notification, Project,
                        Publication, Student)
from app.services import clusters as cluster_service
from app.services.exports import Report, respond
from app.services.grants import filter_grants, source_names
from app.services.matching import COMPONENTS, label as match_label
from app.web.common import owner_of

router = APIRouter(prefix="/export")
MAX_ROWS = 5000


# ---------------------------------------------------------------- helpers
def score(s) -> int:
    return round(float(s) * 100)


def amount(g: Grant) -> str:
    if g.amount_text:
        return g.amount_text
    if g.amount_max:
        return f"{g.currency or ''} {g.amount_max:,.0f}".strip()
    return "Not published"


def evidence_summary(ev: dict, limit: int = 3) -> str:
    parts = []
    for key, (label, _) in COMPONENTS.items():
        e = (ev or {}).get(key)
        if e and e.get("items"):
            parts.append(f"{label}: " + "; ".join(i["label"] for i in e["items"][:limit]))
    if (ev or {}).get("same_funder"):
        parts.append("Previously funded by this funder: " + "; ".join(ev["same_funder"]))
    return " | ".join(parts)


def names(db: Session, keys) -> dict:
    fac = [pid for t, pid in keys if t == "faculty"]
    stu = [int(pid) for t, pid in keys if t == "student" and str(pid).isdigit()]
    out = {("faculty", f.id): f for f in db.scalars(select(Faculty).where(Faculty.id.in_(fac)))} if fac else {}
    if stu:
        out.update({("student", str(s.id)): s for s in db.scalars(select(Student).where(Student.id.in_(stu)))})
    return out


def grant_cluster_names(db: Session, gid: int) -> str:
    return "; ".join(f"{c['name']} (fit {score(s)})" for c, s, _n, _sch in cluster_service.for_grant(db, gid))


def is_ra(s: Student) -> bool:
    return s.source == "signup" and s.access_status == "approved"


# ---------------------------------------------------------------- grants list
@router.get("/grants.{fmt}")
def export_grants(fmt: str, request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    args = dict(request.query_params)
    rows, total = filter_grants(db, args, include_hidden=user.role == "admin" and args.get("status") == "all",
                                page=1, per_page=MAX_ROWS)
    ids = [g.id for g in rows]
    top = defaultdict(list)
    if ids:
        ms = list(db.scalars(select(Match).where(Match.grant_id.in_(ids), Match.rank <= 3)
                            .order_by(Match.grant_id, Match.person_type, Match.rank)))
        people = names(db, {(m.person_type, m.person_id) for m in ms})
        for m in ms:
            p = people.get((m.person_type, m.person_id))
            top[(m.grant_id, m.person_type)].append(f"{p.full_name if p else m.person_id} ({score(m.score)})")
    srcs = source_names(db)
    table = []
    for g in rows:
        table.append([g.title, g.funder or "", g.type.capitalize(), "UAE" if g.scope == "UAE" else "International",
                      g.emirate or g.country or "", g.status.capitalize() if g.status != "unknown" else "Status not published",
                      g.deadline, amount(g), "; ".join(top[(g.id, "faculty")]), "; ".join(top[(g.id, "student")]),
                      grant_cluster_names(db, g.id) if not g.hidden else "", "; ".join(g.eligibility_warnings or []),
                      g.apply_url or g.source_url or "", srcs.get(g.source, g.source), g.last_checked])
    filters = ", ".join(f"{k}: {v}" for k, v in args.items() if v and k not in ("page", "mode"))
    rep = Report("Grants, Fellowships & Scholarships", "aurak-grants",
                 f"{total} grants · filters: {filters or 'open and forthcoming calls (default)'}")
    rep.table("Grants", ["Title", "Funder", "Type", "Scope", "Emirate / country", "Status", "Deadline", "Amount",
                         "Top matched faculty (match score)", "Matched students", "Research clusters",
                         "Eligibility warning", "Apply / source link", "Source", "Last updated (UTC)"], table,
              note="Matched people are the top three by match score. A match does not mean a person is available "
                   "or willing to collaborate. Research clusters are proposed by this project (AI-suggested).")
    return respond(rep, fmt)


# ---------------------------------------------------------------- one grant (detailed funding report)
@router.get("/grants/{gid}.{fmt}")
def export_grant(gid: int, fmt: str, request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    g = db.get(Grant, gid)
    if g is None or (g.hidden and user.role != "admin"):
        raise HTTPException(404, "Grant not found")
    src = db.get(GrantSource, g.source)
    ms = list(db.scalars(select(Match).where(Match.grant_id == gid).order_by(Match.person_type, Match.rank)))
    people = names(db, {(m.person_type, m.person_id) for m in ms})
    rep = Report(g.title, f"aurak-grant-report-{gid}", f"Funding report · {g.funder or 'Funder not published'}")
    rep.facts("Key facts", [
        ("Funder", g.funder), ("Programme", g.programme), ("Type", g.type.capitalize()),
        ("Scope", "UAE" if g.scope == "UAE" else "International"), ("Country / emirate", " · ".join(x for x in (g.country, g.emirate) if x)),
        ("Status", g.status.capitalize() if g.status != "unknown" else "Status not published"),
        ("Opens", g.open_date), ("Deadline", g.deadline), ("All published deadlines", g.deadlines),
        ("Deadline note", g.deadline_text), ("Amount", amount(g)), ("Duration", g.duration),
        ("Research areas", g.research_areas), ("Keywords", g.keywords), ("Contact", g.contact),
        ("Apply link", g.apply_url), ("Source", f"{src.name if src else g.source} · {g.source_url or ''}"),
        ("First seen", g.first_seen), ("Last updated (UTC)", g.last_checked)])
    if g.eligibility_warnings:
        rep.text("Eligibility warnings", "\n".join(g.eligibility_warnings))
    rep.text("Description", g.description)
    rep.text("Eligibility", g.eligibility_text)
    if g.eligibility_flags:
        rep.facts("Eligibility flags", [(k.replace("_", " "), v) for k, v in g.eligibility_flags.items() if v])
    rep.text("Required documents", g.required_documents)
    gc = cluster_service.for_grant(db, gid) if not g.hidden else []
    rep.table("Research clusters", ["Cluster", "Fit with this grant", "Faculty in cluster", "Schools"],
              [[c["name"], score(s), n, sch] for c, s, n, sch in gc],
              note="Clusters are proposed by this project; AURAK has no official research clusters. Membership is AI-suggested.")
    fac = [m for m in ms if m.person_type == "faculty"]
    stu = [m for m in ms if m.person_type == "student"]
    rep.table("Matched AURAK faculty", ["Rank", "Name", "Title", "Department", "School", "Email", "Match score",
                                         "Match", "Why (evidence)"],
              [[m.rank, getattr(people.get(("faculty", m.person_id)), "full_name", m.person_id),
                getattr(people.get(("faculty", m.person_id)), "title", ""),
                getattr(people.get(("faculty", m.person_id)), "department", ""),
                getattr(people.get(("faculty", m.person_id)), "school", ""),
                getattr(people.get(("faculty", m.person_id)), "email", ""),
                score(m.score), match_label(m.score), evidence_summary(m.evidence)] for m in fac],
              note="A match does not mean the person is available or willing to collaborate.")
    rep.table("Matched students", ["Rank", "Name", "Major", "School", "Match score", "Why (evidence)"],
              [[m.rank, getattr(people.get(("student", m.person_id)), "full_name", m.person_id),
                getattr(people.get(("student", m.person_id)), "major", ""),
                getattr(people.get(("student", m.person_id)), "school", ""),
                score(m.score), evidence_summary(m.evidence)] for m in stu])
    detail = []
    for m in ms:
        p = people.get((m.person_type, m.person_id))
        for key, (label, _) in COMPONENTS.items():
            e = (m.evidence or {}).get(key) or {}
            for it in e.get("items") or []:
                detail.append([p.full_name if p else m.person_id, m.person_type.capitalize(), label, it.get("label"),
                               "exact phrase" if it.get("exact") else score(it.get("similarity") or 0)])
    rep.table("Evidence detail", ["Person", "Type", "Evidence type", "Item", "Similarity"], detail)
    return respond(rep, fmt)


# ---------------------------------------------------------------- researchers
def _assistants(db: Session, school: str = ""):
    q = select(Student).where(Student.source == "signup", Student.access_status == "approved",
                              Student.visible == true()).order_by(Student.full_name)
    if school:
        q = q.where(Student.school == school)
    return list(db.scalars(q))


def _interests(db: Session, owner_type: str) -> dict:
    out = defaultdict(list)
    for i in db.scalars(select(Interest).where(Interest.owner_type == owner_type, Interest.status == "approved")
                        .order_by(Interest.owner_id, Interest.text_norm)):
        out[i.owner_id].append(i.text)
    return out


@router.get("/researchers.{fmt}")
def export_researchers(fmt: str, request: Request, school: str = "", db: Session = Depends(get_db)):
    require(request, db)
    q = select(Faculty).where(Faculty.visible == true()).order_by(Faculty.school, Faculty.full_name)
    if school:
        q = q.where(Faculty.school == school)
    fac = list(db.scalars(q))
    fi, si = _interests(db, "faculty"), _interests(db, "student")
    pcl = lambda t, pid: "; ".join(c["name"] for c, s, primary in cluster_service.for_person(db, t, pid))  # noqa: E731
    rep = Report("AURAK Researchers", "aurak-researchers", f"{len(fac)} faculty" + (f" · {school}" if school else ""))
    rep.table("Faculty", ["Name", "Title", "School", "Department", "Email", "Research interests", "OpenAlex topics",
                          "Works", "Citations", "Research clusters (AI-suggested)", "AURAK profile"],
              [[f.full_name, f.title, f.school, f.department, f.email, fi.get(f.id, []), f.openalex_topics or [],
                f.works_count, f.cited_by_count, pcl("faculty", f.id), f.profile_url] for f in fac])
    ras = _assistants(db, school)
    rep.table("Research assistants", ["Name", "Major", "School", "Research interests", "Approved publications",
                                      "Research clusters (AI-suggested)"],
              [[s.full_name, s.major, s.school, si.get(str(s.id), []),
                len(db.scalars(select(Publication.id).where(Publication.owner_type == "student",
                                                            Publication.owner_id == str(s.id),
                                                            Publication.status == "approved")).all()),
                pcl("student", str(s.id))] for s in ras])
    return respond(rep, fmt)


def _person_report(db: Session, kind: str, person, interests, pubs, awards, projects) -> Report:
    from app.web.browse import reverse_matches
    pid = person.id if kind == "faculty" else str(person.id)
    rep = Report(person.full_name, f"aurak-{kind}-{pid}", "Faculty profile" if kind == "faculty" else "Research assistant profile")
    if kind == "faculty":
        rep.facts("Profile", [("Title", person.title), ("School", person.school), ("Department", person.department),
                              ("Email", person.email), ("Biography", person.biography),
                              ("AURAK profile", person.profile_url), ("Google Scholar", person.google_scholar_url),
                              ("ORCID", person.orcid_url), ("Scopus", person.scopus_urls),
                              ("OpenAlex", f"{person.works_count or 0} works · {person.cited_by_count or 0} citations"
                               if person.openalex_id else "Not linked")])
    else:
        rep.facts("Profile", [("Role", "Research assistant (approved student)" if is_ra(person) else "Student"),
                              ("Major", person.major), ("School", person.school)])
    rep.facts("Research", [("Research interests", [i.text for i in interests]),
                           ("OpenAlex topics", getattr(person, "openalex_topics", None) or []),
                           ("Research clusters (AI-suggested)",
                            [c["name"] + (" (main)" if primary else "") for c, s, primary in cluster_service.for_person(db, kind, pid)])])
    rep.table("Publications", ["Year", "Title", "Venue", "DOI", "Source"],
              [[p.year, p.title, p.venue, p.doi, p.source] for p in pubs])
    if kind == "faculty":
        rep.table("Past grants and projects", ["Type", "Title", "Funder", "Year", "Amount", "Source"],
                  [["Grant", a.title, a.funder, a.year, a.amount_text, a.source_url] for a in awards] +
                  [["Project", p.title, "", "", "", p.source_url] for p in projects])
    rep.table("Matching grants", ["Grant", "Funder", "Status", "Deadline", "Amount", "Match score", "Why (evidence)"],
              [[g.title, g.funder, g.status, g.deadline, amount(g), score(s), evidence_summary(ev)]
               for g, s, ev in reverse_matches(db, kind, pid)],
              note="Open and upcoming calls ranked by match score against this profile's evidence.")
    return rep


@router.get("/faculty/{fid}.{fmt}")
def export_faculty(fid: str, fmt: str, request: Request, db: Session = Depends(get_db)):
    require(request, db)
    f = db.get(Faculty, fid)
    if f is None or not f.visible:
        raise HTTPException(404)
    interests = list(db.scalars(select(Interest).where(Interest.owner_type == "faculty", Interest.owner_id == f.id,
                                                       Interest.status == "approved").order_by(Interest.text_norm)))
    pubs = list(db.scalars(select(Publication).where(Publication.owner_type == "faculty", Publication.owner_id == f.id,
                                                     Publication.status == "approved")
                           .order_by(case((Publication.year.is_(None), 1), else_=0), Publication.year.desc(), Publication.id)))
    awards = list(db.scalars(select(Award).where(Award.faculty_id == f.id).order_by(Award.id)))
    projects = list(db.scalars(select(Project).where(Project.faculty_id == f.id).order_by(Project.id)))
    return respond(_person_report(db, "faculty", f, interests, pubs, awards, projects), fmt)


@router.get("/students/{sid}.{fmt}")
def export_student(sid: int, fmt: str, request: Request, db: Session = Depends(get_db)):
    require(request, db)
    s = db.get(Student, sid)
    if s is None or not s.visible:
        raise HTTPException(404)
    interests = list(db.scalars(select(Interest).where(Interest.owner_type == "student", Interest.owner_id == str(sid),
                                                       Interest.status == "approved").order_by(Interest.text_norm)))
    pubs = list(db.scalars(select(Publication).where(Publication.owner_type == "student", Publication.owner_id == str(sid),
                                                     Publication.status == "approved").order_by(Publication.id)))
    return respond(_person_report(db, "student", s, interests, pubs, [], []), fmt)


# ---------------------------------------------------------------- clusters
@router.get("/clusters.{fmt}")
def export_clusters(fmt: str, request: Request, db: Session = Depends(get_db)):
    require(request, db)
    groups = cluster_service.overview(db)
    rep = Report("AURAK Research Clusters", "aurak-research-clusters",
                 "Proposed by this project · membership is AI-suggested (AURAK has no official research clusters)")
    rep.table("Clusters", ["Cluster", "Faculty", "Schools", "Students", "Related open grants", "Main faculty members", "Scope"],
              [[c["name"], len(c["faculty"]), len(c["schools"]), len(c["students"]), c["grants"],
                "; ".join(m[1]["name"] for m in c["faculty"] if m[3])[:600], c["scope"]] for c in groups])
    rep.table("Members", ["Cluster", "Name", "Type", "Department / school", "Fit", "Main cluster"],
              [[c["name"], m[1]["name"], m[0][0].capitalize(), m[1].get("department") or m[1].get("school") or "",
                score(m[2]), "Yes" if m[3] else ""] for c in groups for m in c["members"]])
    return respond(rep, fmt)


@router.get("/clusters/{key}.{fmt}")
def export_cluster(key: str, fmt: str, request: Request, db: Session = Depends(get_db)):
    require(request, db)
    c = cluster_service.detail(db, key)
    if c is None:
        raise HTTPException(404)
    grants = []
    for gid, sim in c["grant_ids"]:
        g = db.get(Grant, gid)
        if g and not g.hidden and g.status != "closed":
            grants.append((g, sim))
    rep = Report(c["name"], f"aurak-cluster-{key}", "Proposed cluster (not an official AURAK unit) · AI-suggested membership")
    rep.facts("Cluster", [("Scope", c["scope"]), ("Keywords", c["terms"]), ("Members", len(c["members"])),
                          ("Schools", len(c["schools"]))])
    rep.table("Members", ["Name", "Type", "Department / school", "Fit", "Main cluster"],
              [[m[1]["name"], m[0][0].capitalize(), m[1].get("department") or m[1].get("school") or "", score(m[2]),
                "Yes" if m[3] else ""] for m in c["members"]])
    rep.table("Open grants for this cluster", ["Grant", "Funder", "Status", "Deadline", "Amount", "Fit", "Apply / source link"],
              [[g.title, g.funder, g.status, g.deadline, amount(g), score(s), g.apply_url or g.source_url] for g, s in grants])
    return respond(rep, fmt)


# ---------------------------------------------------------------- notifications
@router.get("/notifications.{fmt}")
def export_notifications(fmt: str, request: Request, db: Session = Depends(get_db)):
    user = require(request, db, "student", "faculty")
    ot, oid = owner_of(user)
    rows = list(db.scalars(select(Notification).where(Notification.owner_type == ot, Notification.owner_id == oid)
                           .order_by(Notification.created_at.desc(), Notification.score.desc()).limit(MAX_ROWS)))
    grants = {g.id: g for g in db.scalars(select(Grant).where(Grant.id.in_([n.grant_id for n in rows])))} if rows else {}
    rep = Report("Grant Notifications", "aurak-notifications", f"{user.name} · {len(rows)} notifications")
    rep.table("Notifications", ["Date (UTC)", "Grant", "Funder", "Status", "Deadline", "Match score", "Read", "Link"],
              [[n.created_at, grants[n.grant_id].title if n.grant_id in grants else n.message,
                getattr(grants.get(n.grant_id), "funder", ""), getattr(grants.get(n.grant_id), "status", ""),
                getattr(grants.get(n.grant_id), "deadline", None), score(n.score), "Yes" if n.read else "No",
                getattr(grants.get(n.grant_id), "apply_url", "")] for n in rows])
    return respond(rep, fmt)


# ---------------------------------------------------------------- admin
@router.get("/admin/sources.{fmt}")
def export_sources(fmt: str, request: Request, db: Session = Depends(get_db)):
    require(request, db, "admin")
    rows = list(db.scalars(select(GrantSource).order_by(GrantSource.method, GrantSource.name)))
    runs = list(db.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(500)))
    rep = Report("Source Health", "aurak-source-health", "Automatic grant sources and recent ingestion runs")
    rep.table("Sources", ["Source", "Method", "Scope", "Status", "Records", "Last run (UTC)", "Last success (UTC)",
                          "Last error", "Notes", "URL"],
              [[s.name, s.method, s.scope, s.last_status or "", s.records_count, s.last_run_at, s.last_success_at,
                s.last_error, s.notes, s.url] for s in rows])
    rep.table("Ingestion runs", ["Source", "Trigger", "Started (UTC)", "Finished (UTC)", "Status", "New", "Updated", "Error"],
              [[r.source_key, r.trigger, r.started_at, r.finished_at, r.status, r.created, r.updated, r.error] for r in runs])
    return respond(rep, fmt)


@router.get("/admin/db.{fmt}")
def export_table(fmt: str, request: Request, table: str = "faculty", q: str = "", db: Session = Depends(get_db)):
    require(request, db, "admin")
    from app.web.admin import HIDDEN_COLS, TABLES
    model = TABLES.get(table)
    if model is None:
        raise HTTPException(404)
    cols = [c.key for c in inspect(model).columns if c.key not in HIDDEN_COLS and c.key != "vector"]
    query = select(model)
    if q:
        text_cols = [getattr(model, c) for c in cols if str(inspect(model).columns[c].type).startswith(("VARCHAR", "TEXT", "NVARCHAR"))]
        query = query.where(or_(*[c.ilike(f"%{q}%") for c in text_cols]))
    rows = list(db.scalars(query.order_by(inspect(model).primary_key[0]).limit(MAX_ROWS)))
    rep = Report(f"Database table: {table}", f"aurak-db-{table}", f"{len(rows)} rows" + (f" matching “{q}”" if q else ""))
    rep.table(table, cols, [[getattr(r, c) for c in cols] for r in rows])
    return respond(rep, fmt)
