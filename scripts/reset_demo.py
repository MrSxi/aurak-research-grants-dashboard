r"""Reset the demo student (dashboard access, interests, publications, approval requests, matches, notifications)
before a demo. The demo student goes back to "not approved", so the access-request flow can be shown.

Usage: .venv\Scripts\python scripts\reset_demo.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Interest, Match, Notification, Publication, Request, Student, User  # noqa: E402

db = SessionLocal()
u = db.query(User).filter_by(email=config.STUDENT_EMAIL).one_or_none()
if u and u.student_id:
    sid = str(u.student_id)
    for model in (Interest, Publication, Notification):
        db.query(model).filter_by(owner_type="student", owner_id=sid).delete()
    db.query(Request).filter_by(student_id=u.student_id).delete()
    db.query(Match).filter_by(person_type="student", person_id=sid).delete()
    db.get(Student, u.student_id).access_status = "none"
    db.commit()
    print("Demo student reset:", db.get(Student, u.student_id).full_name)
else:
    print("No demo student found; run scripts/import_data.py first.")
