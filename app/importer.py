"""Import the combined AURAK dataset ZIP into the single database. Re-runnable (upserts)."""
import json
import logging
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.auth import hash_password
from app.models import Award, Faculty, Interest, Project, Publication, Student, User
from app.util import clean_doi, norm_text

log = logging.getLogger("importer")
PREFIX = "AURAK_combined_2026-10-14/"


def _load(z: zipfile.ZipFile, name: str) -> list[dict]:
    return json.loads(z.read(PREFIX + name).decode("utf-8-sig"))


def _blank(v):
    return v if v not in ("", None, [], "unknown") else None


def _int(v):
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


def import_dataset(db: Session, zip_path=None) -> dict:
    zip_path = zip_path or config.DATASET_ZIP
    counts = {}
    with zipfile.ZipFile(zip_path) as z:
        faculty = _load(z, "faculty.json")
        for r in faculty:
            f = db.get(Faculty, r["faculty_id"]) or Faculty(id=r["faculty_id"])
            f.full_name = r["full_name"]
            f.title = _blank(r.get("title"))
            f.school = _blank(r.get("school"))
            f.department = _blank(r.get("department"))
            f.email = _blank(r.get("email"))
            f.profile_url = _blank(r.get("faculty_profile_url"))
            f.image_url = _blank(r.get("profile_image_url"))
            f.biography = _blank(r.get("biography"))
            f.education = _blank(r.get("education"))
            f.google_scholar_url = _blank(r.get("google_scholar_url"))
            f.orcid_url = _blank(r.get("orcid_url"))
            f.scopus_urls = r.get("scopus_urls") or ([r["scopus_url"]] if r.get("scopus_url") else [])
            f.research_keywords = r.get("research_keywords") or []
            f.research_interests_status = _blank(r.get("research_interests_status"))
            f.source_url = _blank(r.get("source_url"))
            db.merge(f)
            for text in r.get("research_interests") or []:
                tn = norm_text(text)
                if tn and not db.scalar(select(Interest).where(Interest.owner_type == "faculty",
                                                               Interest.owner_id == f.id, Interest.text_norm == tn)):
                    db.add(Interest(owner_type="faculty", owner_id=f.id, text=text.strip(), text_norm=tn,
                                    source="dataset", status="approved"))
            db.flush()
        counts["faculty"] = len(faculty)

        pubs = _load(z, "publications.json")
        known = {p for (p,) in db.execute(select(Publication.dataset_id).where(Publication.dataset_id.is_not(None)))}
        new = 0
        for r in pubs:
            if r["publication_id"] in known or not r.get("title"):
                continue
            abstract = _blank(r.get("abstract")) or _blank(r.get("abstract_excerpt"))
            db.add(Publication(owner_type="faculty", owner_id=r["faculty_id"], dataset_id=r["publication_id"],
                               doi=clean_doi(r.get("doi")), title=r["title"], title_norm=norm_text(r["title"]),
                               venue=_blank(r.get("venue")), year=_int(r.get("year")),
                               authors=_blank(r.get("authors")), abstract=abstract, keywords=[],
                               url=_blank(r.get("publication_url")), source="dataset", status="approved"))
            new += 1
        counts["publications_added"] = new

        for r in _load(z, "grants.json"):
            db.merge(Award(id=r["grant_id"], faculty_id=r["faculty_id"], title=r["grant_title"],
                           funder=_blank(r.get("funding_agency")), amount_text=_blank(r.get("amount")),
                           year=_blank(r.get("year")), status=_blank(r.get("status")),
                           description=_blank(r.get("description")), source_url=_blank(r.get("source_url"))))
        for r in _load(z, "projects.json"):
            db.merge(Project(id=r["project_id"], faculty_id=r["faculty_id"], title=r["project_title"],
                             description=_blank(r.get("description")), source_url=_blank(r.get("source_url"))))
        # Source-documented student project members: stored for the record; they have no research
        # profile, so they are only matched once interests/publications are approved for them.
        for r in _load(z, "students_involved.json"):
            if not db.scalar(select(Student).where(Student.source == "dataset", Student.full_name == r["student_name"])):
                db.add(Student(full_name=r["student_name"], source="dataset", source_url=_blank(r.get("source_url")),
                               related_faculty_id=r.get("faculty_id")))
    db.flush()
    seed_demo_accounts(db)
    return counts


def seed_demo_accounts(db: Session):
    def upsert(email, name, password, role, **links):
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, name=name, password_hash=hash_password(password), role=role, is_demo=True, **links)
            db.add(u)
        return u

    upsert(config.ADMIN_EMAIL, "Demo Admin", config.ADMIN_PASSWORD, "admin")
    fac = next((f for f in db.scalars(select(Faculty))
                if norm_text(config.FACULTY_DEMO_NAME) in norm_text(f.full_name)), None)
    if fac:
        upsert(config.FACULTY_EMAIL, fac.full_name, config.FACULTY_PASSWORD, "faculty", faculty_id=fac.id)
    if not db.scalar(select(User).where(User.email == config.STUDENT_EMAIL)):
        st = Student(full_name="Demo Student", email=config.STUDENT_EMAIL,
                     school="School of Engineering and Computing", major="Computer Science", source="signup")
        db.add(st)
        db.flush()
        upsert(config.STUDENT_EMAIL, "Demo Student", config.STUDENT_PASSWORD, "student", student_id=st.id)
    # Example approved student (Research Assistant) with full dashboard access
    if not db.scalar(select(User).where(User.email == config.DEMO_RA_EMAIL)):
        st = db.scalar(select(Student).where(Student.student_number == config.DEMO_RA_STUDENT_ID))
        if st is None:
            st = Student(full_name=config.DEMO_RA_NAME, email=config.DEMO_RA_EMAIL, student_number=config.DEMO_RA_STUDENT_ID,
                         access_status="approved", school="School of Engineering and Computing",
                         major="Computer Science", source="signup")
            db.add(st)
            db.flush()
        upsert(config.DEMO_RA_EMAIL, config.DEMO_RA_NAME, config.DEMO_RA_PASSWORD, "student", student_id=st.id)
    db.flush()
