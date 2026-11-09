from datetime import date, timedelta

from sqlalchemy import case, false, func, or_, select, String
from sqlalchemy.orm import Session

from app.models import Grant, GrantSource
from app.services.matching import engine

TYPES = ["grant", "fellowship", "scholarship", "award", "challenge"]
STATUSES = [("active", "Open & forthcoming"), ("open", "Open"), ("forthcoming", "Forthcoming"),
            ("unknown", "Status not published"), ("closed", "Closed"), ("all", "All")]


def filter_grants(db: Session, args: dict, include_hidden=False, page=1, per_page=25):
    q = select(Grant)
    if not include_hidden:
        q = q.where(Grant.hidden == false())
    status = args.get("status") or "active"
    if status == "active":
        q = q.where(Grant.status.in_(["open", "forthcoming", "unknown"]))
    elif status != "all":
        q = q.where(Grant.status == status)
    if args.get("scope") in ("UAE", "international"):
        q = q.where(Grant.scope == args["scope"])
    if args.get("type") in TYPES:
        q = q.where(Grant.type == args["type"])
    if args.get("source"):
        q = q.where(Grant.source == args["source"])
    if args.get("area"):
        like = f"%{args['area']}%"
        q = q.where(or_(Grant.title.ilike(like), Grant.description.ilike(like),
                        Grant.research_areas.cast(String).ilike(like)))
    if args.get("deadline_days"):
        try:
            days = int(args["deadline_days"])
            q = q.where(Grant.deadline.is_not(None), Grant.deadline >= date.today(),
                        Grant.deadline <= date.today() + timedelta(days=days))
        except ValueError:
            pass
    if args.get("min_amount"):
        try:
            q = q.where(Grant.amount_max >= float(args["min_amount"]))
        except ValueError:
            pass
    if args.get("has_amount"):
        q = q.where(Grant.amount_max.is_not(None))
    if args.get("eligible_uae"):
        q = q.where(Grant.scope == "UAE")

    text = (args.get("q") or "").strip()
    semantic_ids = None
    if text and args.get("mode") == "meaning":
        ranked = engine.search_grants(db, text, limit=60) if engine.ready else []
        semantic_ids = [gid for gid, sim in ranked if sim >= 0.25]
        q = q.where(Grant.id.in_(semantic_ids or [-1]))
    elif text:
        for word in text.split()[:6]:
            like = f"%{word}%"
            q = q.where(or_(Grant.title.ilike(like), Grant.description.ilike(like), Grant.funder.ilike(like),
                            Grant.programme.ilike(like)))

    total = db.scalar(select(func.count()).select_from(q.subquery()))
    sort = args.get("sort") or ("relevance" if semantic_ids else "deadline")
    if sort == "newest":
        q = q.order_by(Grant.first_seen.desc(), Grant.id.desc())
    elif sort == "amount":
        q = q.order_by(case((Grant.amount_max.is_(None), 1), else_=0), Grant.amount_max.desc(), Grant.status, Grant.id)  # ties as SQLite ordered them
    elif sort == "relevance" and semantic_ids:
        order = case({gid: i for i, gid in enumerate(semantic_ids)}, value=Grant.id)
        q = q.order_by(order)
    else:
        q = q.order_by(case((Grant.deadline.is_(None), 1), else_=0), Grant.deadline.asc(), Grant.id)
    rows = list(db.scalars(q.offset((page - 1) * per_page).limit(per_page)))
    return rows, total


def source_names(db: Session) -> dict:
    return dict(db.execute(select(GrantSource.key, GrantSource.name)).all())
