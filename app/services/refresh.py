"""Scheduled refresh: grant connectors, OpenAlex faculty publications, matches and notifications."""
import logging
import threading
import traceback
from datetime import date, datetime

from sqlalchemy import select

from app import openalex
from app.config import NOTIFY_MIN_SCORE, NOTIFY_TOP_N
from app.connectors import CONNECTORS, all_sources
from app.connectors.base import GRANT_FIELDS
from app.db import SessionLocal
from app.models import Grant, GrantSource, IngestionRun, Match, Notification
from app.services.matching import engine
from app.util import compute_status

log = logging.getLogger("refresh")
_running = threading.Lock()
state = {"running": False, "started_at": None, "step": None, "last_finished": None}


def sync_sources(db):
    for c in all_sources():
        s = db.get(GrantSource, c.key)
        if s is None:
            s = GrantSource(key=c.key)
            db.add(s)
        s.name, s.url, s.method, s.scope, s.notes = c.name, c.url, c.method, c.scope, c.notes
        if c.method == "manual":  # never fetched, so no fetch status or error applies
            s.last_status, s.last_error = None, None
    # drop sources that no longer exist (e.g. the old manual-entry source) when nothing points at them
    keys = {c.key for c in all_sources()}
    for s in db.scalars(select(GrantSource)):
        if s.key not in keys and not db.scalar(select(Grant.id).where(Grant.source == s.key).limit(1)):
            db.delete(s)
    db.commit()


def recompute_statuses(db):
    today = date.today()
    for g in db.scalars(select(Grant)):
        st = compute_status(g.open_date, g.deadline, g.source_status, today)
        if g.source_status == "unknown" and not g.deadline and not g.open_date:
            st = "unknown"
        if g.status != st:
            g.status = st
    db.commit()


def run_connector(db, connector, trigger="scheduled") -> list[int]:
    """Run one connector. Upserts on (source, source_record_id). Returns ids of newly created grants."""
    run = IngestionRun(source_key=connector.key, trigger=trigger)
    db.add(run)
    src = db.get(GrantSource, connector.key)
    src.last_run_at = datetime.utcnow()
    db.commit()
    created_ids = []
    try:
        known = dict(db.execute(select(Grant.source_record_id, Grant.content_hash)
                                .where(Grant.source == connector.key)).all())
        records = connector.fetch(known)
        now = datetime.utcnow()
        for rec in records:
            g = db.scalar(select(Grant).where(Grant.source == connector.key,
                                              Grant.source_record_id == rec["source_record_id"]))
            if rec.get("unchanged") and g:
                g.last_checked = now
                continue
            if g is None:
                g = Grant(source=connector.key, source_record_id=rec["source_record_id"], first_seen=now,
                          scope=rec.get("scope") or connector.scope)
                db.add(g)
                is_new = True
            else:
                is_new = False
                if g.manual:  # an admin edited it: never overwrite admin corrections
                    g.last_checked = now
                    continue
            for f in GRANT_FIELDS:
                if f in rec and f != "source_record_id":
                    setattr(g, f, rec[f])
            g.content_hash = rec.get("content_hash")
            g.last_checked = now
            st = compute_status(g.open_date, g.deadline, g.source_status)
            g.status = "unknown" if (g.source_status == "unknown" and not g.deadline and not g.open_date) else st
            db.flush()
            if is_new:
                created_ids.append(g.id)
                run.created += 1
            else:
                run.updated += 1
        run.status = "ok" if connector.method != "monitored" else "monitored"
        src.last_status = run.status
        src.last_success_at = now
        errs = getattr(connector, "last_errors", None)
        src.last_error = "; ".join(errs) if errs else None
        if errs:
            run.error = src.last_error
    except Exception as e:  # keep last good data, surface the error in Source health
        db.rollback()
        log.warning("Connector %s failed: %s", connector.key, e)
        run = db.merge(run)
        src = db.get(GrantSource, connector.key)
        run.status, run.error = "error", f"{type(e).__name__}: {e}"[:2000]
        src.last_status, src.last_error = "error", run.error
        created_ids = []
    run.finished_at = datetime.utcnow()
    src.records_count = db.query(Grant).filter(Grant.source == connector.key).count()
    db.commit()
    return created_ids


def notify(db, grant_ids: list[int]):
    """Create in-app notifications for faculty (and approved students) who strongly match new grants."""
    n = 0
    for m in db.scalars(select(Match).where(Match.grant_id.in_(grant_ids), Match.rank <= NOTIFY_TOP_N,
                                            Match.score >= NOTIFY_MIN_SCORE)):
        g = db.get(Grant, m.grant_id)
        if g.status == "closed" or g.hidden:
            continue
        exists = db.scalar(select(Notification).where(Notification.owner_type == m.person_type,
                                                      Notification.owner_id == m.person_id,
                                                      Notification.grant_id == m.grant_id))
        if not exists:
            db.add(Notification(owner_type=m.person_type, owner_id=m.person_id, grant_id=m.grant_id, score=m.score,
                                message=f"New {g.type} matches your research: {g.title}"))
            n += 1
    db.commit()
    return n


def refresh_all(trigger="scheduled", include_openalex=True, sources: list[str] | None = None):
    if not _running.acquire(blocking=False):
        log.info("Refresh already running; skipped")
        return {"skipped": True}
    state.update(running=True, started_at=datetime.utcnow(), step="starting")
    summary = {"new_grants": 0, "openalex_new_publications": 0, "notifications": 0, "errors": []}
    db = SessionLocal()
    try:
        sync_sources(db)
        new_ids = []
        for c in CONNECTORS:
            if sources and c.key not in sources:
                continue
            state["step"] = f"grants: {c.name}"
            new_ids += run_connector(db, c, trigger)
        summary["new_grants"] = len(new_ids)
        recompute_statuses(db)
        if include_openalex:
            state["step"] = "OpenAlex faculty publications"
            try:
                with openalex.client() as oc:
                    summary["openalex_new_publications"] = openalex.sync_faculty_works(db, oc)
                    db.commit()
            except Exception as e:
                db.rollback()
                summary["errors"].append(f"OpenAlex: {e}")
                log.warning("OpenAlex sync failed: %s", e)
        state["step"] = "rebuilding match index"
        engine.rebuild(db)
        state["step"] = "computing matches"
        engine.match_grants(db)
        state["step"] = "notifications"
        summary["notifications"] = notify(db, new_ids)
    except Exception:
        summary["errors"].append(traceback.format_exc(limit=3))
        log.exception("Refresh failed")
    finally:
        db.close()
        state.update(running=False, step=None, last_finished=datetime.utcnow(), last_summary=summary)
        _running.release()
    log.info("Refresh finished: %s", summary)
    return summary


def refresh_in_background(trigger="manual"):
    t = threading.Thread(target=refresh_all, kwargs={"trigger": trigger}, daemon=True)
    t.start()
    return t


def recompute_statuses_job():
    """Daily: deadlines pass at midnight, so open grants become closed without a full refresh."""
    db = SessionLocal()
    try:
        recompute_statuses(db)
    finally:
        db.close()
