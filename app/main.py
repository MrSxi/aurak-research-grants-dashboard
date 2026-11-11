"""FastAPI app: AURAK Research Grants Dashboard.

Run:  .venv\\Scripts\\python -m uvicorn app.main:app --port 8000
"""
import logging
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.auth import AccessPending, LoginRequired, current_user
from app.db import SessionLocal, init_db
from app.services import refresh
from app.services.matching import engine
from app.web import admin, browse, chat, exports, me, public
from app.web.common import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "sentence_transformers", "apscheduler.executors.default"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
scheduler: BackgroundScheduler | None = None


def _warm_index():
    db = SessionLocal()
    try:
        engine.rebuild(db)
    except Exception:
        logging.getLogger("startup").exception("Could not build the match index")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    init_db()
    db = SessionLocal()
    refresh.sync_sources(db)
    from app.importer import seed_demo_accounts  # adds demo accounts created after the first import
    seed_demo_accounts(db)
    db.commit()
    db.close()
    threading.Thread(target=_warm_index, daemon=True).start()
    from app.services.chat import warm_up
    threading.Thread(target=warm_up, daemon=True).start()
    if config.SCHEDULER_ENABLED:
        scheduler = BackgroundScheduler(timezone="Asia/Dubai")
        scheduler.add_job(refresh.refresh_all, "interval", hours=config.REFRESH_HOURS, id="refresh",
                          kwargs={"trigger": "scheduled"}, max_instances=1, coalesce=True)
        scheduler.add_job(refresh.recompute_statuses_job, "cron", hour=0, minute=5, id="statuses")
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="AURAK Research Grants Dashboard", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 7)
app.mount("/static", StaticFiles(directory=str(config.ROOT / "app" / "static")), name="static")
for r in (public.router, exports.router, browse.router, me.router, admin.router, chat.router):
    app.include_router(r)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", 303)


@app.exception_handler(AccessPending)
async def _access_pending(request: Request, exc: AccessPending):
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Dashboard access not approved yet."}, status_code=403)
    return RedirectResponse("/me", 303)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    db = SessionLocal()
    try:
        resp = render(request, db, current_user(request, db), "error.html", code=exc.status_code,
                      message=exc.detail if exc.status_code != 404 else "This page does not exist.")
        resp.status_code = exc.status_code
        return resp
    finally:
        db.close()
